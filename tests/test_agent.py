"""Agent behaviour with a deterministic fake LLM and the REAL zabuni-mcp server over stdio (real data, real tools).

Covers: planning from discovered tools, tool auto-discovery (a tool added to a server needs no agent change), the
follow-up branch, the recovery branch, tool-error correction, the step cap and the citation guard.
"""
import asyncio
import json
import re
import sys
import textwrap
from pathlib import Path

import duckdb
import pytest

from agent.audit import AuditLog
from agent.graph import run_agent
from agent.llm import LLMError
from agent.mcp_client import McpBackend, zabuni_connection
from agent.nodes import AgentContext
from agent.settings import Settings
from agent.state import ArgsFix, FindingDraft, Plan, PlanStep, Scope, SynthOutput
from mcp_server.rules import load_rules
from tests.fakes import FakeLLM

BUYER = "Makueni County Government"


@pytest.fixture(autouse=True)
def isolated_flags_db(tmp_path, monkeypatch):
    """flag_finding writes here; the env var reaches the server subprocess through agent.mcp_client.zabuni_env."""
    path = tmp_path / "flags.duckdb"
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(path))
    return path


def make_plan(*steps, buyer=BUYER, years=(2022, 2023, 2024)):
    return Plan(objective="test investigation", scope=Scope(buyer=buyer, years=list(years)),
                steps=[PlanStep(tool=t, args=a, reason=f"because {t}") for t, a in steps])


def run_scenario(llm, tmp_path, rules_edit=None, connections=None, rules=None):
    """Start the real server(s), run the graph with the given fake LLM, return (state, ctx)."""
    async def go():
        settings = Settings(provider="groq", model="fake", logs_dir=tmp_path / "logs", outputs_dir=tmp_path / "outputs")
        async with McpBackend(settings, use_filesystem=False, connections=connections) as backend:
            r = rules if rules is not None else await backend.read_resource_json("rules://ppada")
            if rules_edit:
                rules_edit(r)
            audit = AuditLog("run_test", settings.logs_dir, r["agent"]["audit_output_max_chars"])
            backend.audit = audit
            ctx = AgentContext(backend=backend, llm=llm, audit=audit, rules=r, tools=await backend.tools())
            return await run_agent(ctx, "test request"), ctx
    return asyncio.run(go())


def events(tmp_path, type_=None):
    f = tmp_path / "logs" / "agent_events.jsonl"
    recs = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []
    return [r for r in recs if type_ is None or r["type"] == type_]


def tool_calls(tmp_path):
    f = tmp_path / "logs" / "tool_calls.jsonl"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines()] if f.exists() else []


def stored_flags(path):
    if not Path(path).exists():
        return []
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute("SELECT flag_id, ocid, rule, status, evidence_json FROM flags").fetchall()
    finally:
        con.close()


def synth_from_candidates(user: str) -> SynthOutput:
    """A cooperative 'model': one finding per candidate id it is shown, worded from the candidate's own summary."""
    items = json.loads(user.split("Candidate flags:\n", 1)[1].split("\n\nRun notes", 1)[0])
    return SynthOutput(summary="Neutral summary written by the fake model.",
                       findings=[FindingDraft(candidate_id=i["id"], severity=i["severity_hint"], ocids=i["ocids"][:1],
                                              explanation=f"{i['summary']} Flagged for human review.") for i in items])


# ----------------------------------------------------------------------------- discovery and planning
def test_planner_prompt_is_built_from_the_discovered_tools_and_excludes_the_flag_writer(tmp_path):
    llm = FakeLLM({Plan: make_plan(("search_awards", {"buyer": BUYER, "limit": 2}))})
    state, ctx = run_scenario(llm, tmp_path)
    prompt = llm.prompts["Plan"][0][1]
    assert {t.name for t in ctx.plannable} <= set(re.findall(r"### (\w+)", prompt)) and len(ctx.plannable) >= 5
    writer = next(t for t in ctx.tools if t.role)                     # found by role from list_tools, not by name
    assert f"### {writer.name}" not in prompt and not writer.read_only
    assert state["results"][0]["tool"] == "search_awards" and state["results"][0]["error"] is None
    assert state["plan"]["steps"][0]["origin"] == "plan"


DUMMY_SERVER = textwrap.dedent('''
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
    mcp = FastMCP("dummy")

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True), description="A brand-new check added to the server after "
              "the agent was written. Returns an empty flag list.")
    def zz_brand_new_check(buyer: str = "") -> dict:
        return {"result": {"flags": [], "note": "ran"}, "evidence": [], "params_used": {"buyer": buyer}, "warnings": []}

    mcp.run(transport="stdio")
''')


def test_a_tool_added_to_the_server_is_usable_with_no_agent_change(tmp_path):
    script = tmp_path / "dummy_server.py"
    script.write_text(DUMMY_SERVER, encoding="utf-8")
    conns = {"zabuni": {"transport": "stdio", "command": sys.executable, "args": [str(script)], "env": None}}
    llm = FakeLLM({Plan: make_plan(("zz_brand_new_check", {"buyer": BUYER}))})
    state, ctx = run_scenario(llm, tmp_path, connections=conns, rules=load_rules())
    assert "### zz_brand_new_check" in llm.prompts["Plan"][0][1]                     # discovered, shown to the planner
    assert state["results"][0]["tool"] == "zz_brand_new_check" and state["results"][0]["error"] is None
    assert state["results"][0]["output"]["result"]["note"] == "ran"


def test_planner_rejects_unknown_tools_and_replans_once_then_fails_clearly(tmp_path):
    good = make_plan(("search_awards", {"buyer": BUYER, "limit": 1}))
    llm = FakeLLM({Plan: [make_plan(("does_not_exist", {})), good]})
    state, _ = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))   # focus on planning
    assert len(llm.prompts["Plan"]) == 2 and "Valid tool names" in llm.prompts["Plan"][1][1]
    assert [r["tool"] for r in state["results"]] == ["search_awards"] and events(tmp_path, "plan_rejected_steps")
    with pytest.raises(LLMError, match="no valid steps"):
        run_scenario(FakeLLM({Plan: make_plan(("nope", {}))}), tmp_path)


# ----------------------------------------------------------------------------- follow-up branch
def test_price_outliers_trigger_splitting_and_concentration_follow_ups_for_that_buyer(tmp_path, isolated_flags_db):
    # a request with no buyer/years scope: nothing is injected into the search, so it spans the whole dataset
    llm = FakeLLM({Plan: make_plan(("search_awards", {"title_keyword": "fuel", "sort_by": "largest", "limit": 3}), buyer=None, years=()),
                   SynthOutput: lambda system, user: synth_from_candidates(user)})
    state, ctx = run_scenario(llm, tmp_path)
    tools = [r["tool"] for r in state["results"]]
    assert tools[0] == "search_awards" and tools.count("compute_price_benchmark") == 3          # one price check per award found
    assert "detect_splitting" in tools and "supplier_concentration" in tools                     # triggered by flagged outliers
    follow = [r for r in state["results"] if r["origin"].startswith("follow-up of")]
    assert len(follow) == len(tools) - 1 and not any(r["error"] for r in state["results"])
    kra = next(r for r in state["results"] if r["tool"] == "detect_splitting" and r["args"].get("supplier"))
    assert "years" not in kra["args"] and kra["args"]["buyer"] == kra["args"]["buyer"].strip()       # no scope -> none to pass on
    ups = events(tmp_path, "follow_up")
    assert len(ups) == len(follow) and any(u["trigger"] == "compute_price_benchmark" for u in ups)
    assert any("follow-up after" in n for n in state["notes"])
    # synthesis: every accepted finding was persisted as pending_review and the ocids are real
    assert state["flags_created"] and all(f["status"] == "pending_review" for f in state["flags_created"])
    assert state["review_status"] == "pending_review"
    db = stored_flags(isolated_flags_db)
    assert len(db) == len(state["flags_created"]) and {row[3] for row in db} == {"pending_review"}
    assert ctx.calls == len(tool_calls(tmp_path)) <= ctx.rules["agent"]["max_steps"] + len(db)


# ----------------------------------------------------------------------------- recovery branch
def test_insufficient_comparables_is_widened_and_the_widening_is_recorded(tmp_path):
    args = {"title_keyword": "generator", "category": "goods", "buyer_type": "health", "years": [2019]}
    llm = FakeLLM({Plan: make_plan(("compute_price_benchmark", args))})
    state, ctx = run_scenario(llm, tmp_path)
    r = state["results"][0]
    assert r["error"] is None and 1 <= len(r["recoveries"]) <= ctx.rules["agent"]["max_recoveries_per_step"]
    first = r["recoveries"][0]
    assert first["op"] == {"op": "drop", "arg": "buyer_type"} and first["comparables_before"] < ctx.rules["price_benchmark"]["min_comparables"]
    assert "buyer_type" not in r["args"]                                                        # the final call is the widened one
    assert r["output"]["result"]["cohort"]["n"] >= ctx.rules["price_benchmark"]["min_comparables"]
    assert not r["output"]["result"]["insufficient_comparables"]
    assert events(tmp_path, "recovery") and any("widened" in n for n in state["notes"])
    assert state["steps_done"] == 1 + len(r["recoveries"])


def test_widening_stops_when_nothing_helps_and_never_exceeds_the_recovery_limit(tmp_path):
    llm = FakeLLM({Plan: make_plan(("compute_price_benchmark", {"title_keyword": "zzqxv nothing", "category": "goods",
                                                               "buyer_type": "health", "years": [2019]}))})
    state, ctx = run_scenario(llm, tmp_path)
    r = state["results"][0]
    assert len(r["recoveries"]) == ctx.rules["agent"]["max_recoveries_per_step"]
    assert r["output"]["result"]["insufficient_comparables"]                                    # still too few: reported, not hidden
    assert [x["op"]["arg"] for x in r["recoveries"]] == ["buyer_type", "years"]


# ----------------------------------------------------------------------------- tool errors: one corrected retry
def test_a_tool_error_is_retried_once_with_arguments_corrected_from_the_candidates(tmp_path):
    def fix(system, user):
        assert "no buyer matches" in user and "MAKUENI COUNTY GOVERNMENT" in user                # error + candidates shown
        return ArgsFix(args={"buyer": BUYER, "years": [2023]}, explanation="typo in the buyer name")

    llm = FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": "Makueni County Govrnment", "years": [2023]})),
                   ArgsFix: fix, SynthOutput: lambda system, user: synth_from_candidates(user)})
    state, _ = run_scenario(llm, tmp_path)
    r = state["results"][0]
    assert r["error"] is None and r["corrections"] == 1 and r["args"]["buyer"] == BUYER
    assert events(tmp_path, "correction") and any("corrected arguments" in n for n in state["notes"])
    first_two = tool_calls(tmp_path)[:2]                                                         # later calls: flag_finding writes
    assert [c["output_summary"].get("error") is not None for c in first_two] == [True, False]   # failed, then fixed


def test_invalid_arguments_never_reach_the_server_and_are_corrected_first(tmp_path):
    llm = FakeLLM({Plan: make_plan(("search_awards", {"buyer": BUYER, "limit": "ten"})),
                   ArgsFix: ArgsFix(args={"buyer": BUYER, "limit": 5})})
    state, _ = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))
    assert state["results"][0]["error"] is None and state["results"][0]["args"]["limit"] == 5
    calls = tool_calls(tmp_path)
    assert len(calls) == 1 and calls[0]["inputs"]["limit"] == 5                                 # the bad call was never made
    assert "invalid arguments" in llm.prompts["ArgsFix"][0][1]


def test_correction_is_bounded_and_give_up_is_respected(tmp_path):
    wrong = ArgsFix(args={"buyer": "Still Not A Buyer"})
    state, ctx = run_scenario(FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": "Nope Nope"})), ArgsFix: wrong}), tmp_path)
    assert state["results"][0]["error"] and state["results"][0]["corrections"] == ctx.rules["agent"]["max_corrections"]
    assert len(tool_calls(tmp_path)) == 1 + ctx.rules["agent"]["max_corrections"]               # first try + one corrected retry
    give_up = FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": "Nope Nope"})), ArgsFix: ArgsFix(give_up=True)})
    state2, _ = run_scenario(give_up, tmp_path / "second")
    assert state2["results"][0]["error"] and state2["stopped_reason"] is None and "failed" in " ".join(state2["notes"])


# ----------------------------------------------------------------------------- step cap
def test_the_step_cap_stops_the_run_and_says_so(tmp_path):
    steps = [("search_awards", {"buyer": BUYER, "limit": n}) for n in (1, 2, 3, 4)]
    llm = FakeLLM({Plan: make_plan(*steps)})
    state, ctx = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(max_steps=2, followups=[]))
    assert len(state["results"]) == 2 == ctx.calls
    assert "step cap of 2" in state["stopped_reason"] and any("Step cap reached" in n for n in state["notes"])
    assert events(tmp_path, "step_cap")[0]["max_steps"] == 2
    assert "not evidence" in state["summary"]                                                   # honest about the gap


# ----------------------------------------------------------------------------- citation guard (integration)
def test_the_citation_guard_drops_invented_ocids_and_nothing_invented_is_stored(tmp_path, isolated_flags_db):
    def lying_synth(system, user):
        items = json.loads(user.split("Candidate flags:\n", 1)[1].split("\n\nRun notes", 1)[0])
        c = items[0]
        return SynthOutput(summary="The supplier also won ocds-9zzz-INVENTED-SUMMARY last year.", findings=[
            FindingDraft(candidate_id=c["id"], severity="high", ocids=["ocds-9zzz-FAKE-1"], explanation="Cites a made-up ocid."),
            FindingDraft(rule="search_awards", severity="high", ocids=["ocds-9zzz-FAKE-2"], explanation="An invented extra finding with nothing behind it."),
        ])

    llm = FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": BUYER, "limit": 2})), SynthOutput: lying_synth})
    state, _ = run_scenario(llm, tmp_path)
    assert {d["reason"] for d in state["dropped"]} == {"ocid not in tool-returned evidence"}
    assert len(state["dropped"]) == 2 and len(events(tmp_path, "citation_guard")) == 3          # two findings + the summary
    assert "INVENTED" not in state["summary"] and "ocds-9zzz" not in json.dumps(state["findings"])
    assert state["flags_created"], "the tool's own flags must survive a lying model"
    assert all(f["source"] == "candidate_default" for f in state["findings"])
    assert not any("ocds-9zzz" in row[1] or "ocds-9zzz" in row[4] for row in stored_flags(isolated_flags_db))


def test_a_failing_synthesiser_falls_back_to_the_tools_own_flags(tmp_path):
    def broken(system, user):
        raise LLMError("model unavailable")

    llm = FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": BUYER, "limit": 1})), SynthOutput: broken})
    state, _ = run_scenario(llm, tmp_path)
    assert state["flags_created"] and events(tmp_path, "synth_fallback")
    assert all(f["source"] == "candidate_default" for f in state["findings"]) and "human review" in state["summary"]


# ----------------------------------------------------------------------------- scope injection and the synthesiser cap
def test_a_plan_that_forgets_the_scope_in_its_arguments_gets_it_back_so_nothing_scans_all_buyers(tmp_path):
    """The failure seen with the live model: scope extracted correctly, but every step's args were empty."""
    llm = FakeLLM({Plan: make_plan(("detect_splitting", {}), ("supplier_concentration", {}), ("search_awards", {"limit": 2})),
                   SynthOutput: lambda system, user: synth_from_candidates(user)})
    state, _ = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(followups=[]))
    by_tool = {r["tool"]: r for r in state["results"]}
    for tool in ("detect_splitting", "supplier_concentration"):
        assert by_tool[tool]["args"]["buyer"] == BUYER and by_tool[tool]["args"]["years"] == [2022, 2023, 2024]
    assert by_tool["search_awards"]["args"]["date_from"] == "2022-01-01" and by_tool["search_awards"]["args"]["date_to"] == "2024-12-31"
    assert by_tool["supplier_concentration"]["output"]["result"]["buyer"].strip() == BUYER      # one buyer, not a ranking
    assert "buyers" not in by_tool["supplier_concentration"]["output"]["result"]
    injected = events(tmp_path, "scope_injected")
    assert {e["tool"] for e in injected} == {"detect_splitting", "supplier_concentration", "search_awards"}


def test_only_the_configured_number_of_candidates_is_shown_to_the_synthesiser_but_none_is_lost(tmp_path):
    llm = FakeLLM({Plan: make_plan(("detect_splitting", {"buyer": BUYER, "limit": 5})),
                   SynthOutput: lambda system, user: synth_from_candidates(user)})
    state, _ = run_scenario(llm, tmp_path, rules_edit=lambda r: r["agent"].update(synth_max_candidates=1))
    shown = json.loads(llm.prompts["SynthOutput"][0][1].split("Candidate flags:" + chr(10), 1)[1].split(chr(10) * 2 + "Run notes", 1)[0])
    total = sum(len((r["output"]["result"].get("flags") or [])) for r in state["results"])
    assert len(shown) == 1 and total > 1 and len(state["flags_created"]) == total               # the rest keep the tool's text
    assert [f["source"] for f in state["findings"]].count("candidate") == 1
