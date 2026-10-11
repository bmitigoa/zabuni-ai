"""The review-time tools called directly: record_decision, attach_clarification, list_flags, draft_eval_memo."""
import pytest

from mcp_server import tools

BUYER = "Makueni County Government"
PENDING, APPROVED, REJECTED, CLARIFY = "pending_review", "approved", "rejected", "clarification_requested"


@pytest.fixture(autouse=True)
def flags_db(tmp_path, monkeypatch):
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(tmp_path / "flags.duckdb"))


def fails(fragment, fn, *args, **kwargs):
    """A tool refuses bad input by returning result.error (never by raising to the agent)."""
    out = fn(*args, **kwargs)
    assert fragment in out["result"]["error"], out["result"]
    return out


@pytest.fixture(scope="module")
def candidates():
    return tools.detect_splitting(buyer=BUYER, limit=3)["result"]["flags"]


def raise_flag(c, run_id="run_t", **over):
    args = {"ocid": c["ocids"][0], "rule": c["rule"], "severity": "medium", "evidence": c["evidence"], "run_id": run_id,
            "explanation": "Several awards to one supplier just below the threshold within two weeks."}
    args.update(over)
    return tools.flag_finding(**args)["result"]["flag_id"]


def decide(fid, decision="approve", reason="checked the cited record", role="internal_auditor"):
    return tools.record_decision(flag_id=fid, decision=decision, reason=reason, reviewer_role=role)


def test_a_decision_needs_a_known_decision_a_reason_a_role_and_an_open_flag(candidates):
    fid = raise_flag(candidates[0])
    for kwargs, message in (({"decision": "award"}, "decision must be one of"), ({"reason": "no"}, "reason is required"),
                            ({"role": "Jane Doe"}, "recorded by role")):
        fails(message, lambda: decide(fid, **kwargs))
    fails("No flag", lambda: decide("flag_missing"))
    assert tools.list_flags(flag_ids=[fid])["result"]["flags"][0]["status"] == PENDING             # refusals changed nothing
    assert decide(fid)["result"]["status"] == APPROVED
    fails("already approved", lambda: decide(fid, "reject"))                                      # a decision is final


def test_clarification_round_trip_returns_the_flag_to_review_and_is_capped(candidates):
    fid, fid2 = raise_flag(candidates[0]), raise_flag(candidates[1])
    assert decide(fid, "clarify", "Was this a framework agreement?")["result"]["status"] == CLARIFY
    fails("No flag", lambda: tools.attach_clarification(flag_id="flag_missing", response="x" * 30))
    fails("only a flag awaiting clarification", lambda: tools.attach_clarification(flag_id=fid2, response="Not awaiting any question."))
    res = tools.attach_clarification(flag_id=fid, response="The awards cite one framework agreement; the record shows no more.")
    assert res["result"]["status"] == PENDING and res["result"]["question"].startswith("Was this")
    flag = tools.list_flags(flag_ids=[fid])["result"]["flags"][0]
    assert flag["clarifications"][0]["response"].startswith("The awards cite")
    fails("only a flag awaiting clarification", lambda: tools.attach_clarification(flag_id=fid, response="Answering twice is not allowed here."))
    decide(fid, "clarify", "And in the year before?")
    tools.attach_clarification(flag_id=fid, response="The previous year shows nothing comparable in the record.")
    fails("clarification round", lambda: decide(fid, "clarify", "One more question please"))      # capped by rules
    assert decide(fid, "approve", "enough information now")["result"]["status"] == APPROVED


def test_an_attached_clarification_may_only_cite_real_awards(candidates):
    fid = raise_flag(candidates[0])
    decide(fid, "clarify", "Anything else for this supplier?")
    fake = [{"ocid": "ocds-9zzz-FAKE", "award_id": "1", "field": "amount", "value": 5}]
    fails("do not exist", lambda: tools.attach_clarification(flag_id=fid, response="This cites a record that is not in the data.", evidence=fake))
    assert tools.list_flags(flag_ids=[fid])["result"]["flags"][0]["status"] == CLARIFY               # nothing was recorded


def test_list_flags_filters_by_run_and_status(candidates):
    a, b = raise_flag(candidates[0], "run_a"), raise_flag(candidates[1], "run_b")
    decide(a, "reject", "lawful framework contract")
    assert [f["flag_id"] for f in tools.list_flags(run_id="run_a")["result"]["flags"]] == [a]
    assert [f["flag_id"] for f in tools.list_flags(status=PENDING)["result"]["flags"]] == [b]
    fails("status must be one of", lambda: tools.list_flags(status="done"))
    out = tools.list_flags(run_id="run_b")
    assert out["evidence"] and all(e["ocid"] for e in out["evidence"])                              # listed flags stay sourced


def test_the_memo_lists_only_approved_flags_and_counts_the_rest(candidates):
    ids = [raise_flag(c, "run_m") for c in candidates[:3]]
    decide(ids[0], "approve", "pattern confirmed in the record")
    decide(ids[1], "reject", "lawful framework contract")                                           # ids[2] stays pending
    out = tools.draft_eval_memo(run_id="run_m", run_notes=["The run stopped early: test budget."])
    md, res = out["result"]["markdown"], out["result"]
    assert res["n_approved"] == 1 and res["n_considered"] == 3 and res["flag_ids"] == [ids[0]]
    assert ids[0] in md and ids[1] not in md and ids[2] not in md
    assert "pattern confirmed in the record" in md and "internal_auditor" in md
    assert "does not recommend or award any tender" in md and "The run stopped early: test budget." in md
    assert "Bid documents are not published" in md                                                  # data limitations always present
    assert any("not approved" in w for w in out["warnings"])
    assert all(e["ocid"] for e in out["evidence"])


def test_a_memo_with_nothing_approved_says_so_instead_of_inventing_findings(candidates):
    fid = raise_flag(candidates[0], "run_n")
    out = tools.draft_eval_memo(run_id="run_n")
    assert out["result"]["n_approved"] == 0 and fid not in out["result"]["markdown"]
    assert any("No flag is approved" in w for w in out["warnings"])
    fails("run_id or flag_ids", lambda: tools.draft_eval_memo())
