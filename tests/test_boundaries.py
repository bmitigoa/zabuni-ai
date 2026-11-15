"""Refine 2: server-side boundaries.

* Only the tools on the write allowlist (rules boundaries.write_tools) may open the flags database for writing; every other tool
  fails safely if it tries, and the analysis database is read-only with external file access off.
* Input limits (rows, year range, string length, list length) are enforced INSIDE the tools, from rules.py.
"""
import json
import os

import duckdb
import pytest

from mcp_server import boundary, flagstore
from mcp_server.common import DB_PATH, cursor
from mcp_server.registry import TOOLS, discover_tools, safe
from mcp_server.rules import load_rules

discover_tools()
RULES = load_rules()
LIM = RULES["limits"]
WRITERS = RULES["boundaries"]["write_tools"]
READ_TOOLS = sorted(n for n, s in TOOLS.items() if n not in WRITERS)


@pytest.fixture(autouse=True)
def isolated_flags_db(tmp_path, monkeypatch):
    path = tmp_path / "flags.duckdb"
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(path))
    return path


def call(name, **kwargs):
    return TOOLS[name].fn(**kwargs)


def refused(out, fragment=None):
    msg = out["result"].get("error", "")
    return bool(msg) and (fragment is None or fragment in msg)


# ============================================================================= the allowlist
def test_the_allowlist_matches_the_tools_declared_not_read_only():
    declared_writers = sorted(n for n, s in TOOLS.items() if not s.read_only)
    assert declared_writers == sorted(WRITERS)                 # a new writing tool must be added to the rules AND declared
    assert len(READ_TOOLS) >= 6 and all(TOOLS[n].read_only for n in READ_TOOLS)


@pytest.mark.parametrize("name", READ_TOOLS)
def test_every_read_tool_is_refused_when_it_tries_to_open_the_flags_database_for_writing(name, isolated_flags_db):
    with boundary.running(name):
        with pytest.raises(boundary.WriteNotAllowed, match=name):
            with flagstore.connect():
                pass
    assert not isolated_flags_db.exists()                      # refused BEFORE the file or its schema was created


@pytest.mark.parametrize("name", WRITERS)
def test_every_allowed_writer_can_open_the_flags_database(name, isolated_flags_db):
    with boundary.running(name):
        with flagstore.connect() as con:
            assert con.execute("SELECT count(*) FROM flags").fetchone()[0] == 0
    assert isolated_flags_db.exists()


@pytest.mark.parametrize("name", READ_TOOLS)
@pytest.mark.parametrize("sql", [
    "CREATE TABLE zz_evil (i INTEGER)",
    "INSERT INTO tenders SELECT * FROM tenders LIMIT 1",
    "UPDATE awards SET amount = 0",
    "DELETE FROM award_suppliers",
    "DROP TABLE tenders",
    "COPY (SELECT 1) TO 'zz_exfil.csv'",
    "ATTACH 'zz_other.duckdb' AS other",
    "SELECT * FROM read_csv('requirements.txt')",
])
def test_every_read_tool_fails_safely_if_it_tries_to_write_or_read_files_through_the_analysis_database(name, sql):
    before = DB_PATH.stat().st_mtime_ns
    with boundary.running(name):
        with pytest.raises(duckdb.Error):
            cursor().execute(sql)
    assert DB_PATH.stat().st_mtime_ns == before                # the frozen snapshot file was not touched
    assert not os.path.exists("zz_exfil.csv") and not os.path.exists("zz_other.duckdb")


def test_a_read_tool_that_tries_to_write_gets_an_error_envelope_not_a_crash_and_nothing_is_written(isolated_flags_db):
    def sneaky_read_tool():
        with flagstore.connect() as con:                        # a read tool (not on the allowlist) opening the flags store to write
            con.execute("INSERT INTO memos VALUES ('x', 'r', now(), '[]', 'forged', 'sha')")
        return {"result": {}, "evidence": [], "params_used": {}, "warnings": []}

    out = safe(sneaky_read_tool)()
    assert refused(out, "Refused") and "sneaky_read_tool" in out["result"]["error"] and "read-only" in out["result"]["error"]
    assert not isolated_flags_db.exists()


def test_the_allowlist_comes_from_the_rules_file_not_from_code(tmp_path, monkeypatch, isolated_flags_db):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"boundaries": {"write_tools": ["flag_finding"]}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(path))
    with boundary.running("draft_eval_memo"):                  # normally allowed
        with pytest.raises(boundary.WriteNotAllowed):
            with flagstore.connect():
                pass
    with boundary.running("flag_finding"):
        with flagstore.connect():
            pass


def test_code_outside_any_tool_is_not_restricted_by_the_tool_allowlist(isolated_flags_db):
    assert boundary.current_tool() is None
    with flagstore.connect():
        pass


def test_the_current_tool_is_reset_after_a_call():
    with boundary.running("search_awards"):
        assert boundary.current_tool() == "search_awards"
    assert boundary.current_tool() is None


# ============================================================================= row limits
def test_search_rows_are_capped_by_rules():
    out = call("search_awards", buyer="Makueni County Government", limit=10 ** 9)
    assert 0 < len(out["result"]["awards"]) <= LIM["search_max"]
    assert len(out["evidence"]) <= LIM["evidence_max_items"]


@pytest.mark.parametrize("name", ["detect_splitting", "supplier_concentration", "detect_noncompetitive_method", "detect_price_anomalies"])
def test_ranking_rows_are_capped_by_rules(name):
    out = call(name, limit=10 ** 9)
    res = out["result"]
    rows = next((v for k, v in res.items() if isinstance(v, list) and k != "flags"), [])
    assert len(rows) <= LIM["ranking_max"], (name, len(rows))
    assert len(out["evidence"]) <= LIM["evidence_max_items"]


# ============================================================================= year range
@pytest.mark.parametrize("name", ["search_awards_dates", "detect_splitting", "supplier_concentration", "detect_noncompetitive_method",
                                  "detect_price_anomalies", "compute_price_benchmark"])
def test_years_outside_the_data_window_are_refused_by_every_tool(name):
    lo, hi = RULES["data_cleaning"]["min_year"], RULES["data_cleaning"]["max_year"]
    if name == "search_awards_dates":
        out = call("search_awards", buyer="Makueni County Government", date_from="1900-01-01")
        assert out["result"].get("awards") is not None         # dates are filters, not a window: documented, not an error
        return
    kwargs = {"title_keyword": "fuel"} if name == "compute_price_benchmark" else {}
    for bad in ([lo - 1], [hi + 1], [1900, 2022], [10 ** 6]):
        assert refused(call(name, years=bad, **kwargs), "outside the data window"), (name, bad)


def test_too_many_years_are_refused():
    out = call("detect_splitting", years=list(range(LIM["max_list_items"] + 1)))
    assert refused(out, "too many items")


# ============================================================================= string length
@pytest.mark.parametrize("name,arg", [("search_awards", "buyer"), ("search_awards", "supplier"), ("search_awards", "title_keyword"),
                                      ("detect_splitting", "buyer"), ("detect_splitting", "supplier"), ("supplier_concentration", "buyer"),
                                      ("detect_noncompetitive_method", "buyer"), ("detect_price_anomalies", "buyer"),
                                      ("detect_price_anomalies", "title_keyword"), ("compute_price_benchmark", "title_keyword"),
                                      ("compute_price_benchmark", "ocid"), ("compute_price_benchmark", "award_id"),
                                      ("list_flags", "run_id"), ("list_flags", "status")])
def test_over_long_names_are_refused_by_every_tool_that_takes_them(name, arg):
    out = call(name, **{arg: "x" * (LIM["max_name_chars"] + 1)})
    assert refused(out, "too long"), (name, arg, out["result"])


def test_a_name_at_the_limit_is_still_accepted():
    out = call("search_awards", buyer="y" * LIM["max_name_chars"])
    assert "too long" not in json.dumps(out["result"])


def test_free_text_and_list_limits_on_the_write_tools(isolated_flags_db):
    big = "z" * (LIM["max_text_chars"] + 1)
    ev = [{"ocid": "o", "award_id": "1", "field": "f", "value": 1}]
    assert refused(call("flag_finding", ocid="o", rule="search_awards", severity="low", explanation=big, evidence=ev), "explanation is too long")
    assert refused(call("flag_finding", ocid="o", rule="search_awards", severity="low", explanation="fine " * 10,
                        evidence=ev * (LIM["max_list_items"] + 1)), "too many items")
    assert refused(call("record_decision", flag_id="f", decision="approve", reason=big, reviewer_role="internal_auditor"), "reason is too long")
    assert refused(call("record_decision", flag_id="f" * 300, decision="approve", reason="ok ok", reviewer_role="internal_auditor"), "too long")
    assert refused(call("attach_clarification", flag_id="f", response=big), "response is too long")
    assert refused(call("draft_eval_memo", run_id="r", run_notes=["n"] * (LIM["max_list_items"] + 1)), "too many items")
    assert refused(call("draft_eval_memo", run_id="r", run_notes=[big]), "too long")
    assert refused(call("draft_eval_memo", run_id="r", title=big), "title is too long")
    assert refused(call("draft_eval_memo", flag_ids=["f"] * (LIM["max_list_items"] + 1)), "too many items")
    assert refused(call("list_flags", flag_ids=["f"] * (LIM["max_list_items"] + 1)), "too many items")
    assert not isolated_flags_db.exists()                       # every refusal happened before anything was opened for writing


def test_limits_are_read_from_the_rules_file_not_from_code(tmp_path, monkeypatch):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"limits": {"max_name_chars": 5, "max_list_items": 2}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(path))
    assert refused(call("search_awards", buyer="Makueni"), "the limit is 5")
    assert refused(call("detect_splitting", years=[2020, 2021, 2022]), "the limit is 2")
