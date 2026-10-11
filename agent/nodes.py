"""The LangGraph nodes: Planner, Investigator, Synthesiser, human_review (the approval gate), clarify and memo.

`AgentContext` carries everything a node needs (MCP backend, LLM, audit log, rules, discovered tools) so nodes stay plain
async functions that take state and return a partial state update. The LLM makes the judgement calls (what to check, how
to fix a failing call, how to word and rank findings, what to look at when a reviewer asks a question); deterministic
policy (agent/policy.py) handles follow-ups, widening, the budget and the citation guard, so what the model can and
cannot do is explicit and testable. Prompts are deliberately short: a local 7B model reads about 40 tokens a second.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import interrupt

from agent import policy
from agent.audit import AuditLog
from agent.budget import Budget
from agent.discovery import ToolInfo, catalogue, compact_schema, planning_tools, tool_with_role
from agent.llm import LLMError, LLMQuotaError
from agent.mcp_client import McpBackend, ToolCallFailed
from agent.state import AgentState, ArgsFix, ClarifyAnswer, ClarifyPlan, Plan, SynthOutput

PLANNER_SYSTEM = """You plan an investigation of Kenyan public-procurement awards (OCDS data) for value-for-money and integrity risks. You never recommend or award anything; humans decide.
Use ONLY the tools listed. Each step: one tool, its arguments as a JSON string, and a reason of at most 12 words. Use only arguments the tool lists.
- Put the buyer and the years in EVERY step whose tool accepts them (2022-2024 means years [2022, 2023, 2024]; date_from 2022-01-01, date_to 2024-12-31). A step without its buyer scans every buyer in Kenya.
- Never invent an ocid or award_id; follow-up checks on found awards are added automatically.
- 3 to 6 steps, broad checks first.
- Return scope: the buyer and years found in the request (null if absent).
Example: for "Review Acme Ltd 2021-2022" every step gets "buyer": "Acme Ltd" and "years": [2021, 2022] (or date_from/date_to)."""

FIX_SYSTEM = """A tool call failed. Return corrected arguments for the SAME tool as a JSON string in args_json, or give_up=true.
Change as little as possible. Use only values from the error message, the candidates, the schema or the original arguments; never invent a value."""

SYNTH_SYSTEM = """You write the reporting step of Zabuni AI. Given candidate flags from tools: order them from most to least important; for each give an explanation of at most 40 words saying what the data shows, and a severity ({severities}); then a 3-5 sentence neutral summary that states its limits.
Use only the candidates and notes given; mention an ocid only if it is listed for that candidate. Never recommend, approve, reject or award; do not say anyone acted improperly: these are screening indicators that may have legitimate explanations."""

CLARIFY_PLAN_SYSTEM = """A reviewer questioned a flag. Plan at most {n} tool steps from the list that could answer the question (reuse the flag's buyer, supplier and years). Each step: tool, arguments as a JSON string, a reason of at most 12 words. If no tool can help, return no steps."""

CLARIFY_ANSWER_SYSTEM = """Answer the reviewer's question in at most 60 words using ONLY the tool results given. If they do not answer it, say what was and was not found. Mention an ocid only if it is listed. No recommendations."""


@dataclass
class AgentContext:
    """Dependencies shared by the nodes of one run."""
    backend: McpBackend
    llm: Any                                                # ChatLLM / FallbackLLM / a test fake
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

    @property
    def budget(self) -> Budget:
        return Budget.from_rules(self.cfg, lambda: self.calls, lambda: int(getattr(self.llm, "tokens", 0)))

    def role_tool(self, key: str) -> ToolInfo | None:
        """The discovered tool whose role is configured under rules agent.<key> (no tool name appears in agent code)."""
        return tool_with_role(self.tools, self.cfg[key])

    async def call(self, step: dict[str, Any], args: dict[str, Any]) -> Any:
        """One logged MCP call for a plan/follow-up step."""
        self.calls += 1
        info = self.by_name[step["tool"]]
        return await self.backend.call(info.server, info.name, args, step={"reason": step.get("reason"),
                                                                           "origin": step.get("origin")})

    async def call_role(self, key: str, args: dict[str, Any], why: str) -> Any:
        """Call the tool with a configured role. Raises ToolCallFailed when the server offers none."""
        info = self.role_tool(key)
        if info is None:
            raise ToolCallFailed(f"The server offers no tool with the role configured as agent.{key}.")
        return await self.backend.call(info.server, info.name, args, step={"reason": why})


def _stop(state: AgentState, reason: str) -> list[str]:
    reasons = list(state.get("stop_reasons") or [])
    if reason not in reasons:
        reasons.append(reason)
    return reasons


# ----------------------------------------------------------------------------- Planner
async def planner(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Ask the LLM for a structured plan built from the *discovered* tool catalogue; validate it; queue the steps."""
    tools = ctx.plannable
    names = {t.name for t in tools}
    user = f"Request: {state['query']}\n\nTools:\n{catalogue(tools, int(ctx.cfg['catalogue_brief_chars']))}"
    steps: list[dict[str, Any]] = []
    plan: Plan | None = None
    for attempt in range(2):
        plan = await ctx.llm.structured(Plan, PLANNER_SYSTEM, user, kind="plan")
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
        user += f"\n\nYour previous plan used no valid tool. Valid tool names: {sorted(names)}. Plan again with exactly those names."
    if not steps or plan is None:
        raise LLMError("The Planner produced no valid steps after a retry.")
    seen: set[str] = set()
    scope = plan.scope.model_dump()
    for s in steps:
        schema = ctx.by_name[s["tool"]].input_schema
        s["args"], dropped = policy.prune_unknown_args(s["args"], schema)          # a small model may invent an argument
        if dropped:
            ctx.audit.event("args_pruned", tool=s["tool"], dropped=dropped, where="plan")
        s["args"], injected = policy.inject_scope(s["args"], schema, scope, ctx.cfg["scope_args"])   # and forget the scope
        if injected:
            ctx.audit.event("scope_injected", tool=s["tool"], injected=injected, scope=scope)
    steps = policy.dedupe_steps(steps, seen)
    ctx.audit.event("plan", objective=plan.objective, scope=scope, steps=steps, available_tools=sorted(names))
    return {"plan": {"objective": plan.objective, "steps": steps}, "scope": scope, "queue": steps, "seen": sorted(seen),
            "steps_done": 0, "results": [], "notes": [], "stopped_reason": None, "stop_reasons": [], "decisions": [],
            "suppressed": [], "clarifications": []}


# ----------------------------------------------------------------------------- Investigator
async def _attempt(ctx: AgentContext, step: dict[str, Any], args: dict[str, Any], notes: list[str], *,
                   enforce_cap: bool = True) -> tuple[Any, str | None, dict[str, Any], int]:
    """Run one call. Arguments the tool does not declare are dropped (logged). On a schema problem, transport failure or
    tool-reported error the LLM is asked ONCE (agent.max_corrections) for corrected arguments and the call is retried.
    Returns (output, error, final_args, corrections)."""
    tool = ctx.by_name[step["tool"]]
    corrections = 0
    while True:
        args, dropped = policy.prune_unknown_args(args, tool.input_schema)
        if dropped:
            ctx.audit.event("args_pruned", tool=tool.name, dropped=dropped, where="call")
            notes.append(f"{tool.name}: dropped argument(s) the tool does not have: {', '.join(dropped)}")
        problems = policy.schema_problems(args, tool.input_schema)
        output: Any = None
        err: str | None = None
        if problems:
            err = "invalid arguments: " + "; ".join(problems)
        elif enforce_cap and ctx.calls >= int(ctx.cfg["max_steps"]):
            return None, "step cap reached", args, corrections
        else:
            try:
                output = await ctx.call(step, args)
                err = policy.error_text(output)
            except ToolCallFailed as exc:
                err = f"tool call failed: {exc}"
        if err is None:
            return output, None, args, corrections
        if corrections >= int(ctx.cfg["max_corrections"]) or ctx.budget.stop_reason():
            return output, err, args, corrections
        candidates = (output or {}).get("result", {}).get("candidates") if isinstance(output, dict) else None
        user = (f"Tool: {tool.name}\nArguments: {compact_schema(tool.input_schema)}\n"
                f"Failed with: {json.dumps(args, default=str)}\nError: {err}\n"
                + (f"Candidate names offered by the tool: {candidates}\n" if candidates else ""))
        try:
            fix = await ctx.llm.structured(ArgsFix, FIX_SYSTEM, user, kind="fix")
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
    if ctx.calls >= int(ctx.cfg["max_steps"]):                 # tool-call cap: stop investigating, keep what was found
        reason = f"step cap of {ctx.cfg['max_steps']} tool calls reached"
        ctx.audit.event("step_cap", max_steps=ctx.cfg["max_steps"], skipped_steps=[(s["tool"], s["args"]) for s in queue])
        notes.append(f"Step cap reached ({ctx.cfg['max_steps']} tool calls): {len(queue)} planned step(s) were not run.")
        return {"queue": [], "notes": notes, "steps_done": ctx.calls, "stopped_reason": reason,
                "stop_reasons": _stop(state, reason)}
    token_stop = ctx.budget.stop_reason()
    if token_stop:                                             # token budget: the Synthesiser's tokens are kept in reserve
        ctx.audit.event("budget_stop", reason=token_stop, skipped_steps=[(s["tool"], s["args"]) for s in queue])
        notes.append(f"Budget reached ({token_stop}): {len(queue)} planned step(s) were not run.")
        return {"queue": [], "notes": notes, "steps_done": ctx.calls, "stopped_reason": token_stop,
                "stop_reasons": _stop(state, token_stop)}
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
    """Rank findings, apply the citation guard, persist each accepted finding with flag_finding, draft a summary.
    A flag a reviewer REJECTED earlier comes back from flag_finding as `rejected` and is not raised again."""
    results = state.get("results", [])
    candidates = policy.extract_candidates(results)
    pool = policy.evidence_pool(results)
    fl = ctx.rules["flags"]
    summary = _deterministic_summary(candidates, state)
    drafts: list[dict[str, Any]] = []
    notes = list(state.get("notes", []))
    stop_reasons = list(state.get("stop_reasons") or [])
    if candidates:
        out_of_tokens = ctx.budget.stop_reason(keep_reserve=False, count_calls=False)
        if out_of_tokens:                                                       # out of tokens: use the tools' own wording
            reason = out_of_tokens
            ctx.audit.event("synth_skipped", reason=reason)
            stop_reasons = _stop(state, reason)
            notes.append(f"The LLM summary was skipped ({reason}); the tools' own wording was used.")
        else:
            order = sorted(candidates, key=lambda c: c["severity_hint"] != fl["severities"][-1])   # most severe first (stable)
            shown = order[: int(ctx.cfg["synth_max_candidates"])]
            listing = [{"id": c["id"], "tool": c["tool"], "summary": c["summary"], "severity_hint": c["severity_hint"],
                        "ocids": c["ocids"][:4]} for c in shown]
            user = (f"Candidate flags:\n{json.dumps(listing, separators=(',', ':'))}\n\nNotes:\n"
                    + "\n".join(f"- {n[:160]}" for n in state.get("notes", [])[-8:]) + f"\nStopped early: {state.get('stopped_reason')}")
            try:
                out = await ctx.llm.structured(SynthOutput, SYNTH_SYSTEM.replace("{severities}", ", ".join(fl["severities"])),
                                               user, kind="synth")
                drafts = [f.model_dump() for f in out.findings]
                if policy.ocids_in(out.summary) - {e["ocid"] for e in pool}:
                    ctx.audit.event("citation_guard", where="summary", reason="summary mentions an ocid not in tool evidence")
                else:
                    summary = out.summary
            except LLMError as exc:
                ctx.audit.event("synth_fallback", reason=str(exc))
                if isinstance(exc, LLMQuotaError):
                    stop_reasons = _stop(state, f"the LLM quota ran out before the summary was written ({str(exc)[:120]})")
    accepted, dropped = policy.guard_findings(drafts, candidates, pool, fl["severities"], int(fl["min_explanation_chars"]))
    for d in dropped:
        ctx.audit.event("citation_guard", where="finding", reason=d["reason"], ocids=d.get("ocids"),
                        draft_rule=d["draft"].get("rule"), draft_explanation=d["draft"].get("explanation", "")[:200])
    created: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    writer = ctx.role_tool("flag_writer_role")
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
        item = {**f, "flag_id": res["flag_id"], "status": res["status"], "created": res["created"], "clarifications": []}
        if res["status"] == fl["status_rejected"]:
            suppressed.append({"flag_id": res["flag_id"], "rule": f["rule"], "ocid": f["ocid"]})
            ctx.audit.event("rejected_flag_suppressed", flag_id=res["flag_id"], rule=f["rule"], ocid=f["ocid"])
        else:
            created.append(item)
    if suppressed:
        notes.append(f"{len(suppressed)} flag(s) a reviewer rejected earlier were remembered and not raised again.")
    return {"findings": accepted, "dropped": dropped, "flags_created": created, "suppressed": suppressed,
            "summary": summary, "notes": notes, "stop_reasons": stop_reasons,
            "review_status": "pending_review" if created else "complete"}


# ----------------------------------------------------------------------------- the human approval gate
def _flag_view(flag: dict[str, Any], shown: int) -> dict[str, Any]:
    """What the reviewer sees for one flag (compact: the first evidence items plus a count)."""
    return {"flag_id": flag["flag_id"], "rule": flag["rule"], "severity": flag["severity"], "explanation": flag["explanation"],
            "ocids": flag["ocids"], "n_evidence": len(flag["evidence"]), "evidence": flag["evidence"][:shown],
            "clarifications": flag.get("clarifications", [])}


async def _refresh_statuses(ctx: AgentContext, flags: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-read each flag's authoritative status (and clarification history) from the flags store."""
    if not flags or ctx.role_tool("flag_reader_role") is None:
        return flags
    try:
        out = await ctx.call_role("flag_reader_role", {"flag_ids": [f["flag_id"] for f in flags]}, "refresh flag statuses")
    except ToolCallFailed:
        return flags
    stored = {f["flag_id"]: f for f in (out.get("result") or {}).get("flags", [])} if isinstance(out, dict) else {}
    return [{**f, "status": stored[f["flag_id"]]["status"] if f["flag_id"] in stored else f["status"],
             "clarifications": stored[f["flag_id"]]["clarifications"] if f["flag_id"] in stored else f.get("clarifications", [])}
            for f in flags]


async def human_review(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """THE APPROVAL GATE. The run pauses here (a LangGraph `interrupt`, persisted by the SQLite checkpointer) with the flags
    in `pending_review`. A reviewer resumes it with `{"decisions": [{flag_id, decision, reason, reviewer_role}, ...]}`
    where decision is approve, reject or clarify, a reason is required, and the reviewer is recorded by role. Each decision
    is stored through the decision-writing tool (DuckDB + the audit log). Flags left undecided keep the run paused;
    nothing reaches the memo without an explicit approval."""
    fl, rv = ctx.rules["flags"], ctx.rules["review"]
    flags = await _refresh_statuses(ctx, list(state.get("flags_created", [])))
    pending = [f for f in flags if f["status"] == fl["status_pending"]]
    if not pending:
        return {"flags_created": flags, "review_status": "complete"}
    payload = {"kind": "review", "run_id": ctx.audit.run_id, "query": state.get("query"),
               "flags": [_flag_view(f, int(rv["interrupt_evidence_shown"])) for f in pending]}
    answer = interrupt(payload)                                # <-- the run pauses here until a reviewer resumes it
    decisions = (answer or {}).get("decisions", []) if isinstance(answer, dict) else []
    notes = list(state.get("notes", []))
    applied = list(state.get("decisions", []))
    pending_ids = {f["flag_id"] for f in pending}
    for d in decisions:
        fid = d.get("flag_id")
        if fid not in pending_ids:
            notes.append(f"A decision for {fid!r} was ignored: that flag is not awaiting review.")
            continue
        args = {"flag_id": fid, "decision": d.get("decision"), "reason": d.get("reason"),
                "reviewer_role": d.get("reviewer_role"), "run_id": ctx.audit.run_id}
        try:
            out = await ctx.call_role("decision_writer_role", args, "store the reviewer's decision")
        except ToolCallFailed as exc:
            notes.append(f"The decision for {fid} could not be stored: {exc}")
            continue
        err = policy.error_text(out)
        if err:
            notes.append(f"The decision for {fid} was refused: {err}")
            continue
        ctx.audit.event("decision", flag_id=fid, decision=args["decision"], reviewer_role=args["reviewer_role"],
                        reason=args["reason"], status=out["result"]["status"])
        applied.append({**d, "decision_id": out["result"]["decision_id"], "status": out["result"]["status"]})
    flags = await _refresh_statuses(ctx, flags)
    return {"flags_created": flags, "decisions": applied, "notes": notes, "review_status": "in_review"}


def route_after_review(state: AgentState) -> str:
    """After a decision round: clarify what was queried, pause again for flags still undecided, else write the memo."""
    statuses = [f["status"] for f in state.get("flags_created", [])]
    if any(s == "clarification_requested" for s in statuses):
        return "clarify"
    if any(s == "pending_review" for s in statuses):
        return "human_review"
    return "memo"


# ----------------------------------------------------------------------------- clarification loop
async def _clarify_one(ctx: AgentContext, state: AgentState, flag: dict[str, Any], question: str,
                       notes: list[str]) -> dict[str, Any]:
    """Re-investigate ONE flag with the reviewer's question, then record the answer (and any new evidence) on the flag."""
    rv, fl = ctx.rules["review"], ctx.rules["flags"]
    tokens_at_start = int(getattr(ctx.llm, "tokens", 0))
    cap = int(ctx.cfg["max_clarify_tokens"])
    scope = state.get("scope", {})
    names = {t.name for t in ctx.plannable}
    steps: list[dict[str, Any]] = []
    answer_text, new_evidence, results = "", [], []
    try:
        user = (f"Flag: {flag['rule']} - {flag['explanation'][:300]}\nCited ocids: {flag['ocids'][:5]}\n"
                f"Scope: buyer={scope.get('buyer')} years={scope.get('years')}\nQuestion: {question}\n\n"
                f"Tools:\n{catalogue(ctx.plannable, int(ctx.cfg['catalogue_brief_chars']))}")
        plan = await ctx.llm.structured(ClarifyPlan, CLARIFY_PLAN_SYSTEM.format(n=rv["clarify_max_steps"]), user, kind="clarify_plan")
        for s in plan.steps[: int(rv["clarify_max_steps"])]:
            if s.tool in names:
                schema = ctx.by_name[s.tool].input_schema
                args, _ = policy.prune_unknown_args(s.args, schema)
                args, _ = policy.inject_scope(args, schema, scope, ctx.cfg["scope_args"])
                steps.append({"tool": s.tool, "args": args, "reason": s.reason, "origin": f"clarification of {flag['flag_id']}"})
        for step in steps:
            if int(getattr(ctx.llm, "tokens", 0)) - tokens_at_start >= cap:
                notes.append(f"Clarification of {flag['flag_id']} stopped: token limit for one round reached.")
                break
            output, err, args, _ = await _attempt(ctx, step, step["args"], notes, enforce_cap=False)
            if err is None and output is not None:
                results.append({"tool": step["tool"], "args": args, "output": output})
                new_evidence += [{k: e[k] for k in ("ocid", "award_id", "field", "value")} for e in output.get("evidence", [])]
            else:
                notes.append(f"Clarification step {step['tool']} failed: {err}")
        new_evidence = new_evidence[: int(rv["interrupt_evidence_shown"]) * 2]
        if results:
            compact = [{"tool": r["tool"], **policy.compact_result(r["output"])} for r in results]
            reply = await ctx.llm.structured(ClarifyAnswer, CLARIFY_ANSWER_SYSTEM,
                                             f"Question: {question}\nResults:\n{json.dumps(compact, separators=(',', ':'))}", kind="clarify_answer")
            allowed = {e["ocid"] for e in new_evidence} | set(flag["ocids"])
            if policy.ocids_in(reply.response) - allowed:
                ctx.audit.event("citation_guard", where="clarification", reason="answer mentions an ocid not in tool evidence")
            else:
                answer_text = reply.response.strip()
    except LLMError as exc:
        notes.append(f"Clarification of {flag['flag_id']}: the LLM was unavailable ({str(exc)[:140]}).")
    if len(answer_text) < int(fl["min_explanation_chars"]):
        found = "; ".join(f"{r['tool']}: {policy.compact_result(r['output']).get('explanation') or 'result returned'}" for r in results)
        answer_text = ("Re-investigation of the reviewer's question. " + (found if found else "No tool result bearing on the "
                       "question could be obtained.") + " No recommendation is made.")
    out = await ctx.call_role("clarification_writer_role", {"flag_id": flag["flag_id"], "response": answer_text,
                                                            "evidence": new_evidence, "run_id": ctx.audit.run_id},
                              "record the answer to the reviewer's question")
    err = policy.error_text(out)
    if err:
        notes.append(f"The clarification answer for {flag['flag_id']} was refused: {err}")
    ctx.audit.event("clarification", flag_id=flag["flag_id"], question=question, steps=[s["tool"] for s in steps],
                    new_evidence=len(new_evidence), response=answer_text[:300])
    return {"flag_id": flag["flag_id"], "question": question, "response": answer_text, "steps": [s["tool"] for s in steps],
            "new_evidence": len(new_evidence)}


async def clarify(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """For every flag whose reviewer asked for clarification: re-investigate with the question, record the answer, and
    return the flag to `pending_review`; the graph then pauses again (human_review)."""
    flags = list(state.get("flags_created", []))
    notes = list(state.get("notes", []))
    log = list(state.get("clarifications", []))
    questions = {d["flag_id"]: d["reason"] for d in state.get("decisions", []) if d.get("decision") == "clarify"}
    for flag in [f for f in flags if f["status"] == ctx.rules["flags"]["status_clarify"]]:
        question = questions.get(flag["flag_id"]) or "Please re-check this flag."
        try:
            log.append(await _clarify_one(ctx, state, flag, question, notes))
        except ToolCallFailed as exc:
            notes.append(f"Clarification of {flag['flag_id']} could not be recorded: {exc}")
    flags = await _refresh_statuses(ctx, flags)
    return {"flags_created": flags, "clarifications": log, "notes": notes}


# ----------------------------------------------------------------------------- the memo
async def memo(state: AgentState, ctx: AgentContext) -> dict[str, Any]:
    """Draft the evaluation memo from the APPROVED flags (the memo tool refuses to list any other) and save it to
    outputs/ through the Filesystem MCP server. The memo says how the run ended (budget, quota, errors)."""
    notes = list(state.get("notes", []))
    run_notes = [ctx.budget.memo_notice(r) for r in state.get("stop_reasons", [])]
    run_notes = [n for n in run_notes if n]
    suppressed = state.get("suppressed", [])
    if suppressed:
        run_notes.append(f"{len(suppressed)} flag(s) rejected by reviewers in earlier runs were remembered and not raised again.")
    run_notes += [n for n in notes if n.startswith(("The decision", "A decision", "Clarification", "MCP"))][:6]
    ids = [f["flag_id"] for f in state.get("flags_created", [])]
    try:
        out = await ctx.call_role("memo_writer_role", {"flag_ids": ids, "run_id": ctx.audit.run_id, "run_notes": run_notes,
                                                       "title": state.get("query")}, "draft the evaluation memo")
    except ToolCallFailed as exc:
        notes.append(f"The memo could not be drafted: {exc}")
        return {"notes": notes, "review_status": "memo_failed"}
    err = policy.error_text(out)
    if err:
        notes.append(f"The memo tool refused: {err}")
        return {"notes": notes, "review_status": "memo_failed"}
    res = out["result"]
    path = None
    try:
        path = await ctx.backend.save_text(f"memo_{ctx.audit.run_id}.md", res["markdown"], ctx.cfg["filesystem_write_tool_hint"])
    except ToolCallFailed as exc:
        notes.append(f"The memo was drafted but not saved to outputs/: {exc}")
    ctx.audit.event("memo", memo_id=res["memo_id"], n_approved=res["n_approved"], n_considered=res["n_considered"], path=path)
    return {"memo_id": res["memo_id"], "memo_path": path, "memo_markdown": res["markdown"], "notes": notes,
            "review_status": "memo_drafted"}
