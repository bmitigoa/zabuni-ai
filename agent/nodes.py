"""The three LangGraph nodes (Planner, Investigator, Synthesiser) plus the human-review placeholder.

`AgentContext` carries everything a node needs (MCP backend, LLM, audit log, rules, discovered tools) so nodes stay
plain async functions that take state and return a partial state update. The LLM makes the judgement calls (what to
check, how to fix a failing call, how to word and rank findings); deterministic policy (agent/policy.py) handles
follow-ups, widening and the citation guard, so what the model can and cannot do is explicit and testable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent import policy
from agent.audit import AuditLog
from agent.discovery import ToolInfo, catalogue, compact_schema, planning_tools, tool_with_role
from agent.llm import ChatLLM, LLMError
from agent.mcp_client import McpBackend, ToolCallFailed
from agent.state import AgentState, ArgsFix, Plan, SynthOutput

PLANNER_SYSTEM = """You are the planning module of Zabuni AI, a decision-support tool that screens Kenyan public-procurement awards (OCDS data, 2018-2026) for value-for-money and integrity risks. You never recommend, approve or award anything: humans decide.
Plan an investigation for the user's request using ONLY the tools in the catalogue.
Rules:
- Each step names exactly one tool from the catalogue and gives arguments that satisfy that tool's schema.
- Put the buyer's full official name (when the request gives one) and the years in EVERY step whose tool accepts them: a year range such as 2022-2024 becomes years [2022, 2023, 2024] for tools with a `years` argument and date_from 2022-01-01 / date_to 2024-12-31 for tools with date arguments. A step that omits the buyer would scan every buyer in Kenya.
- Never invent an ocid or award_id: they are only known after a search. Do not plan a step that needs one (the agent adds follow-up checks on awards a search returns).
- Prefer 3 to 6 steps, broad checks first. Give each step a one-sentence reason.
- Also return `scope`: the buyer name and the years you extracted (null when the request does not give them).
Example: for the request "Review Acme Ltd 2021-2022", every step whose tool accepts a buyer sets "buyer": "Acme Ltd"; every step that accepts years sets "years": [2021, 2022]; steps that accept date_from / date_to set "date_from": "2021-01-01" and "date_to": "2022-12-31". Never leave args_json empty when the request names a buyer or a period."""

FIX_SYSTEM = """A tool call failed. Return corrected arguments for the SAME tool, or give_up=true if it cannot be fixed.
Use the error message, the argument schema and any candidate names to correct the call. Change as little as possible. Use only values that appear in the error message, the candidates, the schema or the original arguments: never invent a value (a category, a buyer, an id). Return the arguments as a JSON object string in args_json."""

SYNTH_SYSTEM = """You are the reporting module of Zabuni AI. You receive candidate flags proposed by analysis tools. Produce:
(1) findings ordered from most to least important for a human reviewer; each refers to one candidate by its id, with an explanation of at most 40 words saying what the data shows and a severity from {severities};
(2) a neutral 3-5 sentence summary of the investigation, including its limits.
Rules: base everything ONLY on the candidates and run notes below. Mention an ocid only if it is listed for that candidate. Do not recommend, approve, reject or award anything and do not say anyone acted improperly: these are screening indicators for human review, and each may have a legitimate explanation (the data has no item-level detail)."""


@dataclass
class AgentContext:
    """Dependencies shared by the nodes of one run."""
    backend: McpBackend
    llm: ChatLLM
    audit: AuditLog
    rules: dict[str, Any]
    tools: list[ToolInfo]                                   # everything discovered on the zabuni server
    calls: int = 0                                          # tool calls made (step cap)
    by_name: dict[str, ToolInfo] = field(init=False)

    def __post_init__(self) -> None:
        self.by_name = {t.name: t for t in self.tools}

    @property
    def cfg(self) -> dict[str, Any]:
        return self.rules["agent"]

    @property
    def plannable(self) -> list[ToolInfo]:
        return planning_tools(self.tools)

    async def call(self, step: dict[str, Any], args: dict[str, Any]) -> Any:
        """One logged MCP call for a plan/follow-up step."""
        self.calls += 1
        info = self.by_name[step["tool"]]
        return await self.backend.call(info.server, info.name, args, step={"reason": step.get("reason"),
                                                                           "origin": step.get("origin")})


# ----------------------------------------------------------------------------- Planner
async def planner(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Ask the LLM for a structured plan built from the *discovered* tool catalogue; validate it; queue the steps."""
    tools = ctx.plannable
    names = {t.name for t in tools}
    user = f"Request: {state['query']}\n\nTool catalogue:\n{catalogue(tools)}"
    steps: list[dict[str, Any]] = []
    plan: Plan | None = None
    for attempt in range(2):
        plan = await ctx.llm.structured(Plan, PLANNER_SYSTEM, user)
        steps, rejected = [], []
        for s in plan.steps[: int(ctx.cfg["max_plan_steps"])]:
            if s.tool in names:
                steps.append({"tool": s.tool, "args": s.args, "reason": s.reason, "origin": "plan"})
            else:
                rejected.append(s.tool)
        if rejected:
            ctx.audit.event("plan_rejected_steps", tools=rejected, valid_tools=sorted(names))
        if steps:
            break
        user += (f"\n\nYour previous plan used no valid tool. Valid tool names: {sorted(names)}. "
                 "Plan again using exactly those names.")
    if not steps or plan is None:
        raise LLMError("The Planner produced no valid steps after a retry.")
    seen: set[str] = set()
    for s in steps:                                            # models forget to copy the scope into args: put it back
        s["args"], injected = policy.inject_scope(s["args"], ctx.by_name[s["tool"]].input_schema, plan.scope.model_dump(),
                                                  ctx.cfg["scope_args"])
        if injected:
            ctx.audit.event("scope_injected", tool=s["tool"], injected=injected, scope=plan.scope.model_dump())
    steps = policy.dedupe_steps(steps, seen)
    ctx.audit.event("plan", objective=plan.objective, scope=plan.scope.model_dump(), steps=steps,
                    available_tools=sorted(names))
    return {"plan": {"objective": plan.objective, "steps": steps}, "scope": plan.scope.model_dump(),
            "queue": steps, "seen": sorted(seen), "steps_done": 0, "results": [], "notes": [],
            "stopped_reason": None}


# ----------------------------------------------------------------------------- Investigator
async def _attempt(ctx: AgentContext, step: dict[str, Any], args: dict[str, Any],
                   notes: list[str]) -> tuple[Any, str | None, dict[str, Any], int]:
    """Run one call; on a schema problem, transport failure or tool-reported error, ask the LLM for corrected
    arguments and retry (at most agent.max_corrections times). Returns (output, error, final_args, corrections)."""
    tool = ctx.by_name[step["tool"]]
    corrections = 0
    while True:
        problems = policy.schema_problems(args, tool.input_schema)
        output: Any = None
        err: str | None = None
        if problems:
            err = "invalid arguments: " + "; ".join(problems)
        elif ctx.calls >= int(ctx.cfg["max_steps"]):
            return None, "step cap reached", args, corrections
        else:
            try:
                output = await ctx.call(step, args)
                err = policy.error_text(output)
            except ToolCallFailed as exc:
                err = f"tool call failed: {exc}"
        if err is None:
            return output, None, args, corrections
        if corrections >= int(ctx.cfg["max_corrections"]):
            return output, err, args, corrections
        candidates = (output or {}).get("result", {}).get("candidates") if isinstance(output, dict) else None
        user = (f"Tool: {tool.name}\nArgument schema: {compact_schema(tool.input_schema)}\n"
                f"Arguments that failed: {json.dumps(args, default=str)}\nError: {err}\n"
                + (f"Candidate names offered by the tool: {candidates}\n" if candidates else ""))
        try:
            fix = await ctx.llm.structured(ArgsFix, FIX_SYSTEM, user)
        except LLMError as exc:
            notes.append(f"{tool.name}: could not correct arguments ({exc})")
            return output, err, args, corrections
        corrections += 1
        ctx.audit.event("correction", tool=tool.name, error=err, old_args=args, new_args=fix.args,
                        give_up=fix.give_up, explanation=fix.explanation)
        if fix.give_up or not fix.args:
            return output, err, args, corrections
        notes.append(f"{tool.name}: retried with corrected arguments after '{err[:120]}'")
        args = fix.args


async def investigator(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Run the next queued step, with recovery and follow-ups; one step per graph visit (the graph loops)."""
    queue = list(state["queue"])
    notes = list(state.get("notes", []))
    results = list(state.get("results", []))
    seen = set(state.get("seen", []))
    stopped = state.get("stopped_reason")
    if ctx.calls >= int(ctx.cfg["max_steps"]):                 # step cap: stop investigating, keep what was found
        ctx.audit.event("step_cap", max_steps=ctx.cfg["max_steps"], skipped_steps=[(s["tool"], s["args"]) for s in queue])
        notes.append(f"Step cap reached ({ctx.cfg['max_steps']} tool calls): {len(queue)} planned step(s) were not run.")
        return {"queue": [], "notes": notes, "steps_done": ctx.calls,
                "stopped_reason": f"step cap of {ctx.cfg['max_steps']} tool calls reached"}
    step = queue.pop(0)
    output, err, args, corrections = await _attempt(ctx, step, dict(step["args"]), notes)
    recoveries: list[dict[str, Any]] = []
    while err is None and policy.needs_widening(output) and len(recoveries) < int(ctx.cfg["max_recoveries_per_step"]):
        nxt = policy.next_widening(args, output["result"]["widen_options"])
        if nxt is None or ctx.calls >= int(ctx.cfg["max_steps"]):
            break
        new_args, op = nxt
        before = output["result"]["cohort"]["n"]
        out2, err2, args2, c2 = await _attempt(ctx, step, new_args, notes)
        corrections += c2
        if err2 is not None or out2 is None:
            notes.append(f"{step['tool']}: widening ({op}) failed: {err2}")
            break
        after = out2["result"]["cohort"]["n"]
        recoveries.append({"op": op, "from": args, "to": args2, "comparables_before": before, "comparables_after": after})
        ctx.audit.event("recovery", tool=step["tool"], op=op, from_args=args, to_args=args2,
                        comparables_before=before, comparables_after=after)
        notes.append(f"{step['tool']}: too few comparables ({before}); widened by {op['op']} {op['arg']} -> {after}")
        output, args = out2, args2
    entry = {"tool": step["tool"], "args": args, "reason": step.get("reason"), "origin": step.get("origin"),
             "output": output if err is None else None, "error": err, "corrections": corrections,
             "recoveries": recoveries}
    results.append(entry)
    if err is not None:
        notes.append(f"{step['tool']}: failed ({err[:160]})")
    added: list[dict[str, Any]] = []
    if err is None:
        raw = policy.followups_for({"tool": step["tool"], "args": args}, output, state.get("scope", {}),
                                   ctx.cfg["followups"], set(ctx.by_name))
        added = policy.dedupe_steps(raw, seen)
        for a in added:
            ctx.audit.event("follow_up", trigger=step["tool"], tool=a["tool"], args=a["args"], reason=a["reason"])
            notes.append(f"follow-up after {step['tool']}: {a['tool']} {json.dumps(a['args'], default=str)} ({a['reason']})")
    return {"queue": added + queue, "seen": sorted(seen), "results": results, "notes": notes,
            "steps_done": ctx.calls, "stopped_reason": stopped}


def route_after_investigator(state: AgentState) -> str:
    """Conditional edge: keep investigating while steps remain, else move on to the Synthesiser."""
    return "investigator" if state.get("queue") else "synthesiser"


# ----------------------------------------------------------------------------- Synthesiser
def _deterministic_summary(candidates: list[dict[str, Any]], state: AgentState) -> str:
    n = len(state.get("results", []))
    if not candidates:
        return (f"{n} check(s) were run. No tool raised a flag. This is not evidence that nothing is wrong: the data has "
                "no item-level detail, no bidder information, and the thresholds are provisional.")
    return (f"{n} check(s) were run and {len(candidates)} flag(s) were raised for human review. Each is a screening "
            "indicator that may have a legitimate explanation; none is a finding of wrongdoing.")


async def synthesiser(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Rank findings, apply the citation guard, persist each accepted finding with flag_finding, draft a summary."""
    results = state.get("results", [])
    candidates = policy.extract_candidates(results)
    pool = policy.evidence_pool(results)
    fl = ctx.rules["flags"]
    summary = _deterministic_summary(candidates, state)
    drafts: list[dict[str, Any]] = []
    if candidates:
        order = sorted(candidates, key=lambda c: c["severity_hint"] != fl["severities"][-1])   # most severe first (stable)
        shown = order[: int(ctx.cfg["synth_max_candidates"])]
        listing = [{"id": c["id"], "tool": c["tool"], "summary": c["summary"], "severity_hint": c["severity_hint"],
                    "ocids": c["ocids"]} for c in shown]
        user = (f"Candidate flags:\n{json.dumps(listing, indent=1)}\n\nRun notes:\n"
                + "\n".join(f"- {n}" for n in state.get("notes", [])) + f"\nStopped early: {state.get('stopped_reason')}")
        try:
            out = await ctx.llm.structured(SynthOutput, SYNTH_SYSTEM.replace("{severities}", ", ".join(fl["severities"])), user)
            drafts = [f.model_dump() for f in out.findings]
            if policy.ocids_in(out.summary) - {e["ocid"] for e in pool}:
                ctx.audit.event("citation_guard", where="summary", reason="summary mentions an ocid not in tool evidence")
            else:
                summary = out.summary
        except LLMError as exc:
            ctx.audit.event("synth_fallback", reason=str(exc))
    accepted, dropped = policy.guard_findings(drafts, candidates, pool, fl["severities"], int(fl["min_explanation_chars"]))
    for d in dropped:
        ctx.audit.event("citation_guard", where="finding", reason=d["reason"], ocids=d.get("ocids"),
                        draft_rule=d["draft"].get("rule"), draft_explanation=d["draft"].get("explanation", "")[:200])
    notes = list(state.get("notes", []))
    created: list[dict[str, Any]] = []
    writer = tool_with_role(ctx.tools, ctx.cfg["flag_writer_role"])
    for f in accepted:
        if writer is None:
            notes.append("No flag-writing tool is available on the server; findings were not persisted.")
            break
        args = {"ocid": f["ocid"], "rule": f["rule"], "severity": f["severity"], "explanation": f["explanation"],
                "evidence": f["evidence"], "run_id": ctx.audit.run_id}
        try:
            out = await ctx.backend.call(writer.server, writer.name, args, step={"reason": "persist accepted finding"})
            ctx.calls += 1
        except ToolCallFailed as exc:
            notes.append(f"Persisting a finding failed: {exc}")
            continue
        err = policy.error_text(out)
        if err:
            notes.append(f"A finding was rejected by the flag writer: {err}")
            continue
        res = out["result"]
        created.append({**f, "flag_id": res["flag_id"], "status": res["status"], "created": res["created"]})
    return {"findings": accepted, "dropped": dropped, "flags_created": created, "summary": summary, "notes": notes}


# ----------------------------------------------------------------------------- human review (Day 4)
async def human_review(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """PLACEHOLDER for the Day 4 human-approval gate.

    Day 4 replaces this body with a LangGraph `interrupt(...)`: the reviewer approves / rejects / requests
    clarification for each flag (reason required), decisions are stored on the `flags` rows, rejected flags are not
    repeated, and only approved flags reach the memo. Until then every flag stays `pending_review`: nothing is final
    and nothing is recommended or awarded."""
    return {"review_status": ctx.rules["flags"]["status_pending"]}
