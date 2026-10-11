"""The human approval gate end to end: real zabuni-mcp server, real data, fake (deterministic) LLM, in-memory checkpointer.

Covers: the run pauses with flags in pending_review; resuming applies decisions and drafts the memo from APPROVED flags
only; a reason is required; the clarify loop re-investigates and pauses again; a rejected flag is remembered and not
re-raised; the token budget stops the run cleanly and the memo says so; decisions reach DuckDB and the audit log.
"""
import asyncio
import json

import duckdb
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.audit import AuditLog
from agent.graph import pause_payload, pending_payload, resume_agent, run_agent
from agent.mcp_client import McpBackend
from agent.nodes import AgentContext
from agent.settings import Settings
from agent.state import ClarifyAnswer, ClarifyPlan, FindingDraft, Plan, PlanStep, Scope, SynthOutput
from tests.fakes import FakeLLM

BUYER = "Makueni County Government"
ROLE = "internal_auditor"


@pytest.fixture(autouse=True)
def isolated_flags_db(tmp_path, monkeypatch):
    path = tmp_path / "flags.duckdb"
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(path))
    return path


def plan_for(*steps, buyer=BUYER, years=(2022, 2023, 2024)):
    return Plan(objective="test investigation", scope=Scope(buyer=buyer, years=list(years)),
                steps=[PlanStep(tool=t, args=a, reason=f"because {t}") for t, a in steps])


def synth(system, user):
    items = json.loads(user.split("Candidate flags:\n", 1)[1].split("\n\nNotes:", 1)[0])
    return SynthOutput(summary="Neutral summary written by the fake model.",
                       findings=[FindingDraft(candidate_id=i["id"], severity=i["severity_hint"], ocids=i["ocids"][:1],
                                              explanation=f"{i['summary']} Flagged for human review.") for i in items])


def make_llm(steps=None, **extra):
    script = {Plan: plan_for(*(steps or [("detect_splitting", {"buyer": BUYER, "limit": 5})])), SynthOutput: synth}
    script.update(extra)
    return FakeLLM(script)


class Session:
    """Keeps one backend, checkpointer and context open across several graph invocations (run, resume, resume...)."""

    def __init__(self, tmp_path, llm, rules_edit=None, run_id="run_test"):
        self.tmp_path, self.llm, self.rules_edit, self.run_id = tmp_path, llm, rules_edit, run_id

    async def __aenter__(self):
        settings = Settings(provider="groq", model="fake", logs_dir=self.tmp_path / "logs", outputs_dir=self.tmp_path / "outputs")
        self.backend = await McpBackend(settings, use_filesystem=False).__aenter__()
        rules = await self.backend.read_resource_json("rules://ppada")
        if self.rules_edit:
            self.rules_edit(rules)
        audit = AuditLog(self.run_id, settings.logs_dir, rules["agent"]["audit_output_max_chars"])
        self.backend.audit = audit
        self.ctx = AgentContext(backend=self.backend, llm=self.llm, audit=audit, rules=rules, tools=await self.backend.tools())
        self.saver = InMemorySaver()
        return self

    async def __aexit__(self, *exc):
        await self.backend.__aexit__(*exc)

    async def run(self, query="Review Makueni County Government awards 2022 2023 2024"):
        return await run_agent(self.ctx, query, self.saver)

    async def resume(self, decisions):
        return await resume_agent(self.ctx, decisions, self.saver)


def go(coro_fn):
    return asyncio.run(coro_fn())


def d(flag_id, decision, reason="checked against the published record", role=ROLE):
    return {"flag_id": flag_id, "decision": decision, "reason": reason, "reviewer_role": role}


def events(tmp_path, type_):
    f = tmp_path / "logs" / "agent_events.jsonl"
    recs = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []
    return [r for r in recs if r["type"] == type_]


def tool_names(tmp_path):
    f = tmp_path / "logs" / "tool_calls.jsonl"
    return [json.loads(line)["tool"] for line in f.read_text(encoding="utf-8").splitlines()]


def table(path, sql):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# ----------------------------------------------------------------------------- interrupt and resume
def test_the_run_pauses_after_the_synthesiser_with_every_flag_pending_and_nothing_in_the_memo(tmp_path, isolated_flags_db):
    async def scenario():
        async with Session(tmp_path, make_llm()) as s:
            state = await s.run()
            return state, await pending_payload(s.ctx, s.saver)

    state, payload = go(scenario)
    paused = pause_payload(state)
    assert paused is not None and paused["kind"] == "review" and len(paused["flags"]) >= 2
    assert payload["flags"] == paused["flags"]                                    # the checkpoint holds the same request
    assert {f["status"] for f in state["flags_created"]} == {"pending_review"}
    assert all(f["evidence"] and f["ocids"] for f in paused["flags"])             # every pending flag is sourced
    assert "draft_eval_memo" not in tool_names(tmp_path) and not state.get("memo_path")
    assert {r[0] for r in table(isolated_flags_db, "SELECT status FROM flags")} == {"pending_review"}
    assert table(isolated_flags_db, "SELECT count(*) FROM memos")[0][0] == 0


def test_resuming_stores_decisions_and_the_memo_lists_only_approved_flags(tmp_path, isolated_flags_db):
    async def scenario():
        async with Session(tmp_path, make_llm()) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            decisions = [d(ids[0], "approve", "supplier pattern confirmed in the record")] + \
                        [d(i, "reject", "legitimate framework contract") for i in ids[1:]]
            return ids, await s.resume(decisions)

    ids, final = go(scenario)
    assert pause_payload(final) is None and final["review_status"] == "memo_drafted"
    memo = final["memo_markdown"]
    assert ids[0] in memo and "supplier pattern confirmed in the record" in memo and ROLE in memo
    assert not any(i in memo for i in ids[1:]) and "does not recommend or award any tender" in memo
    approved = {f["flag_id"] for f in final["flags_created"] if f["status"] == "approved"}
    assert approved == {ids[0]} and {f["status"] for f in final["flags_created"] if f["flag_id"] != ids[0]} == {"rejected"}
    rows = table(isolated_flags_db, "SELECT flag_id, decision, reason, reviewer_role FROM flag_decisions ORDER BY decided_at")
    assert {r[0] for r in rows} == set(ids) and {r[3] for r in rows} == {ROLE} and all(r[2] for r in rows)
    logged = events(tmp_path, "decision")
    assert len(logged) == len(ids) and {e["reviewer_role"] for e in logged} == {ROLE}
    assert "draft_eval_memo" in tool_names(tmp_path)


def test_a_decision_without_a_reason_is_refused_and_the_flag_stays_pending(tmp_path):
    async def scenario():
        async with Session(tmp_path, make_llm()) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            decisions = [d(ids[0], "approve", reason="")] + [d(i, "approve") for i in ids[1:]]
            return ids, await s.resume(decisions)

    ids, after = go(scenario)
    payload = pause_payload(after)
    assert payload is not None and [f["flag_id"] for f in payload["flags"]] == [ids[0]]       # paused again, only for that flag
    assert any("refused" in n for n in after["notes"]) and not after.get("memo_markdown")
    assert next(f for f in after["flags_created"] if f["flag_id"] == ids[0])["status"] == "pending_review"


def test_a_reviewer_role_outside_the_configured_list_is_refused(tmp_path):
    async def scenario():
        async with Session(tmp_path, make_llm()) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            return ids, await s.resume([d(i, "approve", role="Jane Doe") for i in ids])        # a person's name is not a role

    ids, after = go(scenario)
    assert pause_payload(after) is not None and not after.get("decisions")
    assert sum("refused" in n for n in after["notes"]) == len(ids)


def test_a_paused_run_can_be_resumed_by_a_new_context_through_the_checkpointer(tmp_path):
    """The CLI reviews in a different process: a fresh backend and context must be able to resume the same thread."""
    async def scenario():
        saver = InMemorySaver()
        async with Session(tmp_path, make_llm()) as first:
            first.saver = saver
            ids = [f["flag_id"] for f in pause_payload(await first.run())["flags"]]
        async with Session(tmp_path, make_llm()) as second:                      # new MCP server process, new FakeLLM
            second.saver = saver
            assert (await pending_payload(second.ctx, saver)) is not None
            return ids, await second.resume([d(i, "approve") for i in ids])

    ids, final = go(scenario)
    assert final["review_status"] == "memo_drafted" and all(i in final["memo_markdown"] for i in ids)


# ----------------------------------------------------------------------------- the clarify loop
def test_clarify_re_investigates_the_flag_with_the_question_then_pauses_again(tmp_path, isolated_flags_db):
    question = "Is this buyer's spending in the same category also high in the previous year?"

    def plan_clarify(system, user):
        assert question in user and "Flag:" in user                             # the reviewer's question reached the model
        return ClarifyPlan(steps=[PlanStep(tool="search_awards", args={"buyer": BUYER, "limit": 2}, reason="look at awards")])

    def answer(system, user):
        assert question in user and "Results" in user
        return ClarifyAnswer(response="The re-check found the same buyer's awards; no extra pattern was visible in them.")

    llm = make_llm(**{"ClarifyPlan": None})
    llm.script = {Plan: llm.script[Plan], SynthOutput: synth, ClarifyPlan: plan_clarify, ClarifyAnswer: answer}

    async def scenario():
        async with Session(tmp_path, llm) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            second = await s.resume([d(ids[0], "clarify", question)] + [d(i, "approve") for i in ids[1:]])
            third = await s.resume([d(ids[0], "approve", "the answer addresses the question")])
            return ids, second, third

    ids, second, third = go(scenario)
    again = pause_payload(second)
    assert again is not None and [f["flag_id"] for f in again["flags"]] == [ids[0]]            # only the queried flag is back
    answered = again["flags"][0]["clarifications"]
    assert answered and answered[0]["response"].startswith("The re-check") and "search_awards" in tool_names(tmp_path)
    assert next(f for f in second["flags_created"] if f["flag_id"] == ids[0])["status"] == "pending_review"
    assert second["clarifications"][0]["question"] == question and events(tmp_path, "clarification")
    assert third["review_status"] == "memo_drafted" and ids[0] in third["memo_markdown"]
    assert table(isolated_flags_db, "SELECT count(*) FROM flag_clarifications")[0][0] == 1
    assert {r[0] for r in table(isolated_flags_db, "SELECT decision FROM flag_decisions")} == {"clarify", "approve"}


def test_a_clarification_still_gets_an_honest_answer_when_the_model_cannot_help(tmp_path):
    llm = make_llm()
    llm.script = {Plan: llm.script[Plan], SynthOutput: synth, ClarifyPlan: ClarifyPlan(steps=[])}

    async def scenario():
        async with Session(tmp_path, llm) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            return await s.resume([d(ids[0], "clarify", "Can you check the tender notice itself?")] + [d(i, "approve") for i in ids[1:]])

    after = go(scenario)
    flag = pause_payload(after)["flags"][0]
    assert "No tool result bearing on the question" in flag["clarifications"][0]["response"]     # said plainly, nothing invented


# ----------------------------------------------------------------------------- rejected flags are remembered
def test_a_rejected_flag_is_not_raised_again_in_a_later_run_on_the_same_scope(tmp_path, isolated_flags_db):
    async def first_run():
        async with Session(tmp_path, make_llm(), run_id="run_one") as s:
            ids = [f["flag_id"] for f in pause_payload(await s.run())["flags"]]
            await s.resume([d(ids[0], "reject", "framework agreement, lawful")] + [d(i, "approve") for i in ids[1:]])
            return ids

    async def second_run():
        async with Session(tmp_path, make_llm(), run_id="run_two") as s:
            return await s.run()

    ids = go(first_run)
    state = go(second_run)
    new_ids = {f["flag_id"] for f in state["flags_created"]}
    assert ids[0] not in new_ids and new_ids == set(ids[1:])                              # same flags, minus the rejected one
    assert len(state["suppressed"]) == 1 and state["suppressed"][0]["flag_id"] == ids[0]
    assert events(tmp_path, "rejected_flag_suppressed") and any("remembered" in n for n in state["notes"])
    assert all(f["status"] == "approved" for f in state["flags_created"])                 # earlier approvals persist too
    assert table(isolated_flags_db, "SELECT count(*) FROM flags WHERE status = 'rejected'")[0][0] == 1


def test_when_every_flag_was_rejected_before_the_run_goes_straight_to_a_memo_saying_so(tmp_path):
    async def scenario():
        async with Session(tmp_path, make_llm(), run_id="run_one") as s:
            ids = [f["flag_id"] for f in pause_payload(await s.run())["flags"]]
            await s.resume([d(i, "reject", "all lawful, see framework agreements") for i in ids])
        async with Session(tmp_path, make_llm(), run_id="run_two") as s2:
            return ids, await s2.run()

    ids, state = go(scenario)
    assert pause_payload(state) is None and len(state["suppressed"]) == len(ids) and not state["flags_created"]
    assert "rejected by reviewers in earlier runs were remembered" in state["memo_markdown"]
    assert "No flag is approved" in state["memo_markdown"] or "no findings" in state["memo_markdown"].lower()


# ----------------------------------------------------------------------------- budget
class TokenFake(FakeLLM):
    """A fake whose every call 'uses' a fixed number of tokens, to exercise the token budget."""

    def __init__(self, script, per_call):
        super().__init__(script)
        self.per_call = per_call

    async def structured(self, schema, system, user, kind="llm"):
        out = await super().structured(schema, system, user, kind)
        self.tokens += self.per_call
        return out


def test_the_token_budget_stops_the_investigation_and_the_memo_says_so(tmp_path):
    llm = TokenFake({Plan: plan_for(("detect_splitting", {"buyer": BUYER}), ("supplier_concentration", {}),
                                    ("search_awards", {"limit": 2})), SynthOutput: synth}, per_call=1200)

    def small_budget(r):
        r["agent"].update(max_total_tokens=1000, synth_reserve_tokens=0, followups=[])

    async def scenario():
        async with Session(tmp_path, llm, rules_edit=small_budget) as s:
            return await s.run()

    state = go(scenario)
    assert state["results"] == []                                                   # the plan alone used the whole budget
    assert any("token budget" in r for r in state["stop_reasons"]) and "token budget" in state["stopped_reason"]
    assert events(tmp_path, "budget_stop")
    memo = state["memo_markdown"]
    assert "The run stopped early" in memo and "not evidence that none exists" in memo


def test_the_tool_call_budget_is_reported_in_the_memo(tmp_path):
    llm = make_llm(steps=[("search_awards", {"buyer": BUYER, "limit": n}) for n in (1, 2, 3, 4)])

    def cap(r):
        r["agent"].update(max_steps=2, followups=[])

    async def scenario():
        async with Session(tmp_path, llm, rules_edit=cap) as s:
            return await s.run()

    state = go(scenario)
    assert len(state["results"]) == 2 and any("step cap" in r or "budget" in r for r in state["stop_reasons"])
    assert "The run stopped early" in state["memo_markdown"]
