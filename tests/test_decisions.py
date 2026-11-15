"""Refine 4: decision memory as read-only MCP resources: decisions://run/{run_id} and decisions://flag/{flag_id}."""
import asyncio
import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server import boundary, decisions, flagstore, tools
from mcp_server.common import ROOT, ToolInputError
from mcp_server.rules import load_rules

BUYER = "Makueni County Government"


@pytest.fixture(autouse=True)
def flags_db(tmp_path, monkeypatch):
    path = tmp_path / "flags.duckdb"
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(path))
    return path


@pytest.fixture(scope="module")
def candidates():
    return tools.detect_splitting(buyer=BUYER, limit=3)["result"]["flags"]


def raise_flag(c, run_id="run_dec"):
    return tools.flag_finding(ocid=c["ocids"][0], rule=c["rule"], severity="medium", evidence=c["evidence"], run_id=run_id,
                              explanation="Several awards to one supplier just below the threshold within two weeks.")["result"]["flag_id"]


def decide(fid, decision, reason, role="internal_auditor"):
    return tools.record_decision(flag_id=fid, decision=decision, reason=reason, reviewer_role=role)


def test_a_run_lists_every_flag_with_role_decision_reason_time_and_the_ocids_it_cites(candidates):
    a, b, c = (raise_flag(x) for x in candidates[:3])
    decide(a, "approve", "supplier pattern confirmed in the record")
    decide(b, "reject", "lots of one programme, not splitting", "procurement_officer")
    # c stays pending
    out = decisions.run_decisions("run_dec")
    assert out["run_id"] == "run_dec" and out["n_flags"] == 3 and out["n_decisions"] == 2
    by = {f["flag_id"]: f for f in out["flags"]}
    assert by[a]["status"] == "approved" and by[b]["status"] == "rejected" and by[c]["status"] == "pending_review"
    d = by[a]["decisions"][0]
    assert d["reviewer_role"] == "internal_auditor" and d["decision"] == "approve" and d["reason"].startswith("supplier pattern")
    assert d["decided_at"] and d["status_after"] == "approved" and d["decision_id"]
    assert by[b]["decisions"][0]["reviewer_role"] == "procurement_officer" and by[c]["decisions"] == []
    assert by[a]["ocids"] == sorted(set(by[a]["ocids"])) and by[a]["ocids"] and all(o.startswith("ocds-") for o in by[a]["ocids"])
    assert "Jane" not in json.dumps(out) and "read-only" in out["note"].lower()                  # roles only, never names


def test_a_flag_shows_its_clarification_rounds_and_a_second_decision_history(candidates):
    fid = raise_flag(candidates[0])
    decide(fid, "clarify", "Was this a framework agreement?")
    tools.attach_clarification(flag_id=fid, response="The awards cite one framework agreement; the record shows no more.",
                               evidence=candidates[0]["evidence"][:1])
    decide(fid, "approve", "answer is consistent with the record")
    h = decisions.flag_decision_history(fid)
    assert [d["decision"] for d in h["decisions"]] == ["clarify", "approve"] and h["status"] == "approved"
    q = h["clarifications"][0]
    assert q["question"].startswith("Was this") and q["answer"].startswith("The awards cite") and q["new_evidence_ocids"]
    assert h["run_id"] == "run_dec"


def test_an_unknown_run_or_flag_returns_an_empty_or_error_answer_and_never_invents(candidates):
    assert decisions.run_decisions("run_nobody")["flags"] == []
    out = decisions.flag_decision_history("flag_missing")
    assert "No flag" in out["error"] and "decisions" not in out
    with pytest.raises(ToolInputError):
        decisions.run_decisions("   ")
    with pytest.raises(ToolInputError, match="too long"):
        decisions.flag_decision_history("f" * (load_rules()["limits"]["max_name_chars"] + 1))


def test_reading_decisions_is_read_only_and_creates_nothing(flags_db):
    assert not flags_db.exists()
    assert decisions.run_decisions("run_dec")["n_flags"] == 0 and decisions.flag_decision_history("x")["error"]
    assert not flags_db.exists()                                         # a missing database is an empty answer, not a new file
    assert "decisions" not in __import__("mcp_server.registry", fromlist=["TOOLS"]).TOOLS     # a resource, not a tool: no action to take


def test_the_run_listing_is_capped_by_rules_and_says_so(candidates, tmp_path, monkeypatch):
    rules_file = tmp_path / "rules.json"
    rules_file.write_text(json.dumps({"limits": {"decisions_listed_max": 2}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(rules_file))
    for c in candidates[:3]:
        raise_flag(c)
    out = decisions.run_decisions("run_dec")
    assert out["n_flags"] == 2 and "first 2 flags" in out["truncated"]


async def _over_stdio(flags_path, run_id, flag_id):
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(ROOT),
                                   env={"ZABUNI_FLAGS_DB": str(flags_path)})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            templates = {t.uriTemplate for t in (await s.list_resource_templates()).resourceTemplates}
            run = json.loads((await s.read_resource(f"decisions://run/{run_id}")).contents[0].text)
            flag = json.loads((await s.read_resource(f"decisions://flag/{flag_id}")).contents[0].text)
            empty = json.loads((await s.read_resource("decisions://run/run_nobody")).contents[0].text)
            return templates, run, flag, empty


def test_the_resources_are_listed_and_readable_through_the_real_server(candidates, flags_db):
    fid = raise_flag(candidates[0])
    decide(fid, "reject", "legitimate framework contract")
    templates, run, flag, empty = asyncio.run(asyncio.wait_for(_over_stdio(flags_db, "run_dec", fid), timeout=120))
    assert {"decisions://run/{run_id}", "decisions://flag/{flag_id}"} <= templates
    assert run["n_flags"] == 1 and run["flags"][0]["decisions"][0]["decision"] == "reject"
    assert flag["flag_id"] == fid and flag["status"] == "rejected" and flag["ocids"]
    assert empty["flags"] == []


def test_a_resource_read_cannot_write_even_inside_a_tool_context(candidates, flags_db):
    """The module only ever opens the flags database read-only: a write attempt through it fails."""
    raise_flag(candidates[0])
    with flagstore.connect(read_only=True) as con:
        with pytest.raises(Exception):
            con.execute("DELETE FROM flag_decisions")
    with boundary.running("search_awards"):
        assert decisions.run_decisions("run_dec")["n_flags"] == 1
