"""Day 5 fixes: the scope guard (no buyer/supplier/period -> ask, run nothing), the flag cap, and the per-run budget that
survives a pause and a resume in another process."""
import asyncio

from agent import policy
from agent.graph import pause_payload, run_agent
from agent.state import Plan
from agent.usage import UsageLedger
from tests.fakes import FakeLLM
from tests.test_review_flow import BUYER, Session, events, go, make_llm, plan_for
from tests.test_review_flow import isolated_flags_db  # noqa: F401  (autouse fixture: a throw-away flags database)

SEVERITIES = ["low", "medium", "high"]


def llm_with_plan(plan):
    script = dict(make_llm().script)
    script[Plan] = plan
    return FakeLLM(script)


# ----------------------------------------------------------------------------- pure policy
def test_a_scope_needs_a_buyer_a_supplier_or_a_period():
    assert not policy.has_scope({}) and not policy.has_scope({"buyer": " ", "years": []}) and not policy.has_scope({"buyer": None})
    assert policy.has_scope({"buyer": "Acme"}) and policy.has_scope({"supplier": "Acme"}) and policy.has_scope({"years": [2023]})


def test_the_callers_scope_wins_and_blanks_are_ignored():
    merged = policy.merge_scope({"buyer": "Model Buyer", "years": [2020]}, {"buyer": "Typed Buyer", "years": None, "supplier": ""})
    assert merged == {"buyer": "Typed Buyer", "years": [2020]}
    assert policy.merge_scope({"buyer": "A"}, None) == {"buyer": "A"}


def test_the_cap_keeps_the_strongest_findings_first_and_ties_in_order():
    def f(name, sev, n):
        return {"rule": name, "severity": sev, "evidence": [{}] * n}
    items = [f("a", "low", 9), f("b", "high", 2), f("c", "medium", 5), f("d", "high", 6), f("e", "high", 6)]
    kept, capped = policy.cap_findings(items, SEVERITIES, 3)
    assert [k["rule"] for k in kept] == ["d", "e", "b"]                 # high first, more evidence first, original order on a tie
    assert [c["rule"] for c in capped] == ["c", "a"]
    assert policy.cap_findings(items, SEVERITIES, 99)[1] == []


def test_a_supplier_in_the_scope_is_injected_into_tools_that_accept_it():
    schema = {"properties": {"supplier": {"type": "string"}, "buyer": {"type": "string"}}}
    args, injected = policy.inject_scope({}, schema, {"supplier": "Wavecrest Limited"}, {"buyer": "buyer", "supplier": "supplier"})
    assert args == {"supplier": "Wavecrest Limited"} and injected == ["supplier"]


# ----------------------------------------------------------------------------- the scope guard in the graph
def test_a_request_with_no_scope_asks_for_it_and_runs_no_tool(tmp_path):
    llm = llm_with_plan(plan_for(("detect_splitting", {}), buyer=None, years=()))

    async def scenario():
        async with Session(tmp_path, llm) as s:
            return await s.run("hello")
    state = go(scenario)
    assert state["scope_request"] and "buyer" in state["scope_request"].lower() and state["review_status"] == "needs_scope"
    assert pause_payload(state) is None and state["results"] == [] and state["flags_created"] == []
    assert not (tmp_path / "logs" / "tool_calls.jsonl").exists() and events(tmp_path, "scope_request")                          # nothing was called at all
    assert llm.calls == 1                                                                              # only the planner call was spent


def test_the_same_vague_plan_runs_when_the_caller_supplies_the_scope(tmp_path):
    llm = llm_with_plan(plan_for(("detect_splitting", {"limit": 3}), buyer=None, years=()))

    async def scenario():
        async with Session(tmp_path, llm) as s:
            return await run_agent(s.ctx, "hello", s.saver, {"buyer": BUYER, "years": [2023]})
    state = go(scenario)
    assert not state.get("scope_request")
    assert state["results"][0]["args"]["buyer"] == BUYER and state["results"][0]["args"]["years"] == [2023]


def test_a_supplier_only_request_is_in_scope(tmp_path):
    llm = llm_with_plan(plan_for(("search_awards", {"limit": 2}), buyer=None, years=()))

    async def scenario():
        async with Session(tmp_path, llm, rules_edit=lambda r: r["agent"].update(followups=[])) as s:
            return await run_agent(s.ctx, "awards to Wavecrest", s.saver, {"supplier": "Wavecrest"})
    state = go(scenario)
    assert not state.get("scope_request") and state["results"][0]["args"]["supplier"] == "Wavecrest"


# ----------------------------------------------------------------------------- the flag cap in the graph
def test_at_most_the_configured_number_of_flags_is_raised_and_the_memo_says_so(tmp_path):
    def cap2(r):
        r["agent"].update(max_flags_per_run=2)

    async def scenario():
        async with Session(tmp_path, make_llm(), rules_edit=cap2) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            decisions = [{"flag_id": i, "decision": "approve", "reason": "checked the cited record", "reviewer_role": "internal_auditor"}
                         for i in ids]
            return state, await s.resume(decisions)
    state, final = go(scenario)
    assert len(state["flags_created"]) == 2 and state["capped"] and any(n.startswith("Flag cap") for n in state["notes"])
    assert events(tmp_path, "flags_capped")[0]["limit"] == 2
    assert "Flag cap:" in final["memo_markdown"]


# ----------------------------------------------------------------------------- the budget belongs to the run
def test_a_resumed_session_inherits_the_runs_tool_calls_and_tokens(tmp_path, monkeypatch):
    from agent.session import open_session
    from agent.settings import Settings
    monkeypatch.setenv("ZABUNI_CHECKPOINT_DB", str(tmp_path / "cp.sqlite"))
    settings = Settings(provider="ollama", model="qwen2.5:7b", logs_dir=tmp_path / "logs", outputs_dir=tmp_path / "outputs")
    run_id = "run_budget_test"

    async def first_phase():
        async with open_session(settings, run_id, use_filesystem=False) as s:
            s.ctx.llm = FakeLLM(dict(make_llm().script))
            state = await run_agent(s.ctx, "Review Makueni County Government awards 2022 2023 2024", s.checkpointer)
            return state["steps_done"]

    steps_done = asyncio.run(first_phase())
    ledger = UsageLedger(settings.logs_dir / "llm_usage.jsonl")
    ledger.record_call(run_id=run_id, provider="ollama", model="qwen2.5:7b", kind="plan", prompt_tokens=700, completion_tokens=300,
                       seconds=1, cost_usd=0.0)
    ledger.record_call(run_id="another_run", provider="ollama", model="qwen2.5:7b", kind="plan", prompt_tokens=5, completion_tokens=5,
                       seconds=1, cost_usd=0.0)

    async def second_phase(resume, rid):
        async with open_session(settings, rid, use_filesystem=False, resume=resume) as s:
            return s.ctx.calls, s.ctx.token_base, s.ctx.budget.tokens()
    calls, base, budget_tokens = asyncio.run(second_phase(True, run_id))
    assert calls == steps_done > 0 and base == 1000 and budget_tokens == 1000                  # another run's tokens are not counted
    assert asyncio.run(second_phase(False, "a_brand_new_run")) == (0, 0, 0)


# ----------------------------------------------------------------------------- grounding: the model may not invent the scope
def test_a_scope_the_request_does_not_support_is_removed():
    invented = {"buyer": None, "years": [2022, 2023, 2024], "supplier": None}
    scope, removed = policy.ground_scope(invented, "hello")
    assert scope["years"] is None and removed == ["years"] and not policy.has_scope(scope)             # the "hello" failure


def test_years_and_names_that_the_request_states_are_kept():
    scope, removed = policy.ground_scope({"buyer": "Makueni County Government", "years": [2022, 2023], "supplier": None},
                                         "Review Makueni County Government awards 2022-2023 for splitting")
    assert removed == [] and scope["years"] == [2022, 2023] and scope["buyer"] == "Makueni County Government"
    ranged, removed = policy.ground_scope({"years": [2022, 2023, 2024]}, "awards from 2022 to 2024")
    assert removed == [] and ranged["years"] == [2022, 2023, 2024]                                    # a range keeps its middle year


def test_a_buyer_the_request_never_mentions_is_dropped_but_a_shortened_name_survives():
    assert policy.ground_scope({"buyer": "Kenya Power and Lighting Company"}, "something about Nairobi")[0]["buyer"] is None
    assert policy.ground_scope({"buyer": "Makueni County Government"}, "makueni county awards")[0]["buyer"] == "Makueni County Government"


def test_the_planner_removes_an_invented_scope_and_asks_instead_of_running(tmp_path):
    llm = llm_with_plan(plan_for(("detect_splitting", {}), buyer=None, years=(2022, 2023, 2024)))

    async def scenario():
        async with Session(tmp_path, llm) as s:
            return await s.run("hello")
    state = go(scenario)
    assert state["scope_request"] and state["results"] == [] and events(tmp_path, "scope_ungrounded")[0]["removed"] == ["years"]
