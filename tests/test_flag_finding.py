"""flag_finding: the first non-read-only tool. Writes only to a separate flags database; rejects unsourced flags."""
import json

import duckdb
import pytest

from mcp_server import tools
from mcp_server.common import DB_PATH, rows
from mcp_server.registry import TOOLS

BUYER = "Makueni County Government"


@pytest.fixture
def flags_db(tmp_path, monkeypatch):
    path = tmp_path / "flags.duckdb"
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(path))
    return path


@pytest.fixture(scope="module")
def cluster():
    """A real splitting cluster: its evidence and flag come straight from a tool result, as the agent will use them."""
    out = tools.detect_splitting(buyer=BUYER, limit=1)
    return out["result"]["flags"][0]


def call(cluster, **over):
    args = {"ocid": cluster["ocids"][0], "rule": cluster["rule"], "severity": "medium",
            "explanation": "Three awards to one supplier just below the threshold within two weeks.",
            "evidence": cluster["evidence"], "run_id": "run_test"}
    args.update(over)
    return tools.flag_finding(**args)


def stored(path):
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute("SELECT flag_id, status, rule, severity, run_id, evidence_json FROM flags").fetchall()
    finally:
        con.close()


def test_flag_is_stored_pending_review_with_its_evidence(flags_db, cluster):
    out = call(cluster)
    assert out["result"]["created"] and out["result"]["status"] == "pending_review"
    assert out["evidence"] == cluster["evidence"]                       # the evidence is echoed back
    (flag_id, status, rule, severity, run_id, evidence_json), = stored(flags_db)
    assert (flag_id, status, rule, severity, run_id) == (out["result"]["flag_id"], "pending_review",
                                                         "detect_splitting", "medium", "run_test")
    assert json.loads(evidence_json) == cluster["evidence"]


def test_identical_flag_is_not_duplicated(flags_db, cluster):
    first, second = call(cluster), call(cluster)
    assert first["result"]["flag_id"] == second["result"]["flag_id"] and not second["result"]["created"]
    assert any("already exists" in w for w in second["warnings"]) and len(stored(flags_db)) == 1


@pytest.mark.parametrize("override, fragment", [
    ({"evidence": []}, "needs evidence"),                                         # unsourced -> rejected
    ({"evidence": None}, "needs evidence"),
    ({"evidence": [{"ocid": "ocds-x"}]}, "must have the keys"),
    ({"evidence": [{"ocid": "ocds-FAKE", "award_id": "A1", "field": "award_amount", "value": 1}],
      "ocid": "ocds-FAKE"}, "do not exist"),                                      # invented citation -> rejected
    ({"ocid": "ocds-not-in-the-evidence"}, "ocid must be one of"),
    ({"severity": "catastrophic"}, "severity must be one of"),
    ({"rule": "made_up_rule"}, "rule must be the name of an analysis tool"),
    ({"rule": "flag_finding"}, "rule must be the name of an analysis tool"),      # a flag cannot cite the writer itself
    ({"explanation": "too short"}, "explanation must be at least"),
])
def test_unsourced_or_invalid_flags_are_rejected_and_nothing_is_written(flags_db, cluster, override, fragment):
    out = call(cluster, **override)
    assert fragment in out["result"]["error"] and out["evidence"] == []
    assert not flags_db.exists() or stored(flags_db) == []


def test_it_is_the_declared_write_tool_and_the_analysis_database_stays_read_only(flags_db, cluster):
    spec = TOOLS["flag_finding"]
    assert not spec.read_only and spec.role == "flag_writer"
    before = rows("SELECT COUNT(*) AS n FROM awards")[0]["n"]
    call(cluster)
    assert rows("SELECT COUNT(*) AS n FROM awards")[0]["n"] == before
    with pytest.raises(duckdb.Error):                                              # the shared connection cannot write
        duckdb.connect(str(DB_PATH), read_only=True).execute("CREATE TABLE should_fail (x INT)")
