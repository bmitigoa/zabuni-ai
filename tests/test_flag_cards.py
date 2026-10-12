"""What a reviewer sees on a flag card: the flagged award apart from its comparison set, one card per award, scope checks,
data-entry hints, provisional labels and money formatting."""
import pytest

from agent import policy
from mcp_server import tools
from mcp_server.fmt import fmt_value, kes
from mcp_server.rules import load_rules
from mcp_server.toolkit import data_entry_hints, threshold_text, validation_label
from tests.test_review_flow import BUYER, Session, go, make_llm, plan_for
from tests.test_review_flow import isolated_flags_db  # noqa: F401  (autouse fixture)

AWARD = {"ocid": "ocds-5whusi-42686", "award_id": "20250205150132-43031"}        # staff medical cover, Makueni, 2022-12-14
DQ = load_rules()["data_quality"]


# ----------------------------------------------------------------------------- money
def test_money_is_kes_with_thousands_separators_and_no_trailing_zero():
    assert kes(8916920.0) == "KES 8,916,920" and kes(330.5) == "KES 330.50" and kes(0) == "KES 0" and kes("n/a") == "n/a"
    assert fmt_value("award_amount", 198999999.0) == "KES 198,999,999"
    assert fmt_value("contract_date_signed", "2023-03-23") == "2023-03-23" and fmt_value("count", 3.0) == "3"


# ----------------------------------------------------------------------------- hints and provisional labels
@pytest.mark.parametrize("amount, ratio, expected", [
    (5_000_000, 25.0, True),                                            # extreme multiple of the comparison median
    (30_000_000, 2.0, True),                                            # a large round number
    (198_999_999, 6.6, True),                                           # a run of nines looks like a placeholder
    (4_761_539, 2.0, False), (None, 50.0, False), (9_000_000, 3.0, False)])
def test_data_entry_hints_follow_the_rules(amount, ratio, expected):
    assert bool(data_entry_hints(amount, ratio)) == expected
    if expected:
        assert "verify the amount against the source document" in data_entry_hints(amount, ratio)[0]


def test_every_threshold_is_labelled_provisional_until_a_practitioner_validates_it():
    assert validation_label("compute_price_benchmark") == "provisional"
    assert threshold_text("detect_splitting", "3 awards within 30 days").endswith("(provisional)")
    assert load_rules()["validation"]["validated_rules"] == []


# ----------------------------------------------------------------------------- the tools describe the flagged award apart from the comparison set
@pytest.fixture(scope="module")
def bench():
    out = tools.compute_price_benchmark(**AWARD)
    assert out["result"]["flags"], "the staff medical cover award should be flagged by the benchmark"
    return out["result"]["flags"][0]


def test_the_benchmark_flag_names_the_flagged_award_and_how_its_comparison_set_was_built(bench):
    d = bench["detail"]
    award = d["subject"]["awards"][0]
    assert d["subject"]["kind"] == "award" and (award["ocid"], award["award_id"]) == (AWARD["ocid"], AWARD["award_id"])
    assert award["buyer"] == BUYER and award["date"] == "2022-12-14" and award["amount_kes"] > 100_000_000 and award["supplier"]
    sig = d["signals"][0]
    assert "similar awards" in sig["metric"] and sig["threshold"].endswith("(provisional)")
    comp = sig["comparison"]
    assert comp["n"] >= 10 and comp["buyers"] > 1 and comp["basis"] and comp["note"]
    flagged_ids = {(a["ocid"], a["award_id"]) for a in d["subject"]["awards"]}
    comparables = [e for e in bench["evidence"] if (e["ocid"], e["award_id"]) not in flagged_ids]
    assert comparables and all(e["ocid"] != AWARD["ocid"] for e in comparables)                  # the comparison set is separate


def test_an_extreme_multiple_or_a_placeholder_looking_amount_carries_the_data_entry_hint(bench):
    assert bench["detail"]["hints"] == []                                       # 8.7x, not round, no run of nines: no hint
    out = tools.detect_price_anomalies(buyer=BUYER, years=[2022, 2023], limit=5)
    extreme = next(f for f in out["result"]["flags"] if f["detail"]["subject"]["awards"][0]["ocid"] == AWARD["ocid"])
    assert extreme["detail"]["hints"] and "source document" in extreme["detail"]["hints"][0]       # 71x the cohort median
    nines = tools.compute_price_benchmark(ocid="ocds-5whusi-1110244-2-2022/2023", award_id="20250205150258-53204")["result"]["flags"][0]
    assert str(int(nines["detail"]["subject"]["awards"][0]["amount_kes"])).endswith("999999") and nines["detail"]["hints"]


def test_the_anomaly_flag_describes_a_different_comparison_set_for_the_same_award():
    out = tools.detect_price_anomalies(buyer=BUYER, years=[2022, 2023], limit=5)
    flag = next(f for f in out["result"]["flags"] if f["detail"]["subject"]["awards"][0]["ocid"] == AWARD["ocid"])
    comp = flag["detail"]["signals"][0]["comparison"]
    assert comp["n"] > 1000 and "category" in comp["basis"] and comp["buyers"] > 10


def test_a_splitting_flag_lists_every_award_of_the_cluster_with_buyer_supplier_date_and_amount():
    flag = tools.detect_splitting(buyer=BUYER, years=[2023], limit=1)["result"]["flags"][0]
    awards = flag["detail"]["subject"]["awards"]
    assert flag["detail"]["subject"]["kind"] == "awards" and len(awards) >= 2
    assert all(a["buyer"] == BUYER and a["supplier"] and a["date"] and a["amount_kes"] for a in awards)


def test_a_buyer_level_flag_names_the_buyer_not_an_award():
    flag = tools.detect_noncompetitive_method(limit=3)["result"]["flags"][0]
    assert flag["detail"]["subject"]["kind"] == "buyer" and flag["detail"]["subject"]["name"]
    assert flag["detail"]["signals"][0]["comparison"]["label"] == "awards of all Kenyan buyers"


# ----------------------------------------------------------------------------- merging and scope
def finding(rule, severity, n, med, extra_evidence=(), award=AWARD):
    detail = {"subject": {"kind": "award", "name": None, "awards": [{**award, "buyer": BUYER, "supplier": "S", "date": "2022-12-14",
                                                                     "amount_kes": 1.0, "title": "T"}]},
              "signals": [{"rule": rule, "summary": f"{rule} summary", "comparison": {"n": n, "median_kes": med}}], "hints": []}
    ev = [{"ocid": award["ocid"], "award_id": award["award_id"], "field": "award_amount", "value": 1.0}, *extra_evidence]
    return {"rule": rule, "severity": severity, "explanation": f"{rule} explanation text", "evidence": ev, "ocid": award["ocid"],
            "ocids": [award["ocid"]], "detail": detail}


def test_two_findings_about_one_award_become_one_card_with_both_signals_and_the_reason_the_sets_differ():
    a = finding("compute_price_benchmark", "medium", 96, 22_000_000)
    b = finding("detect_price_anomalies", "high", 4659, 2_780_000,
                extra_evidence=[{"ocid": "ocds-x", "award_id": "9", "field": "award_amount", "value": 2.0}])
    other = finding("detect_splitting", "medium", 0, 0, award={"ocid": "ocds-other", "award_id": "1"})
    merged, absorbed = policy.merge_same_award([a, other, b], ["low", "medium", "high"], "The multiples differ.")
    assert absorbed == 1 and len(merged) == 2
    card = merged[0]
    assert card["severity"] == "high" and card["rule"] == "compute_price_benchmark" and card["merged_from"] == 2
    assert [s["rule"] for s in card["detail"]["signals"]] == ["compute_price_benchmark", "detect_price_anomalies"]
    assert card["detail"]["comparison_note"] == "The multiples differ." and len(card["evidence"]) == 2
    assert merged[1]["rule"] == "detect_splitting" and "comparison_note" not in merged[1]["detail"]


def test_signals_with_the_same_comparison_size_need_no_explanation():
    merged, _ = policy.merge_same_award([finding("a", "low", 50, 1), finding("b", "low", 50, 1)], ["low", "medium", "high"], "differ")
    assert "comparison_note" not in merged[0]["detail"]


def test_a_flagged_award_outside_the_requested_scope_is_detected_but_a_wider_comparison_set_is_not_a_problem():
    f = finding("compute_price_benchmark", "high", 96, 1)
    assert policy.scope_problems(f, {"buyer": BUYER, "years": [2022, 2023]}) == []
    assert any("not the requested buyer" in p for p in policy.scope_problems(f, {"buyer": "Kenya Rural Roads Authority"}))
    assert any("outside the requested years" in p for p in policy.scope_problems(f, {"years": [2019]}))
    assert any("supplier" in p for p in policy.scope_problems(f, {"supplier": "Wavecrest Limited"}))
    f["detail"]["signals"][0]["comparison"] = {"n": 5000, "buyers": 80, "years": [2018, 2026]}          # wider comparison: fine
    assert policy.scope_problems(f, {"buyer": BUYER, "years": [2022]}) == []
    kept, dropped = policy.drop_out_of_scope([f], {"years": [2019]})
    assert kept == [] and dropped[0]["problems"]


# ----------------------------------------------------------------------------- the graph
def test_the_graph_merges_benchmark_and_anomaly_flags_on_one_award_into_one_stored_flag(tmp_path):
    llm = make_llm(steps=[("detect_price_anomalies", {"buyer": BUYER, "years": [2022, 2023], "limit": 5}),
                          ("compute_price_benchmark", dict(AWARD))])

    async def scenario():
        async with Session(tmp_path, llm, rules_edit=lambda r: r["agent"].update(followups=[])) as s:
            return await s.run("Review Makueni County Government awards 2022 2023")
    state = go(scenario)
    mine = [f for f in state["flags_created"] if f["ocid"] == AWARD["ocid"]]
    assert len(mine) == 1                                                                 # one card, not two
    sigs = [s["rule"] for s in mine[0]["detail"]["signals"]]
    assert sigs == ["compute_price_benchmark", "detect_price_anomalies"] or sorted(sigs) == ["compute_price_benchmark", "detect_price_anomalies"]
    assert mine[0]["detail"].get("comparison_note") and mine[0]["detail"]["hints"]
    assert any("merged into one card" in n for n in state["notes"])


def test_the_graph_drops_a_flag_whose_award_is_outside_the_requested_scope(tmp_path):
    llm = make_llm(steps=[("compute_price_benchmark", dict(AWARD))])

    async def scenario():
        async with Session(tmp_path, llm, rules_edit=lambda r: r["agent"].update(followups=[])) as s:
            from agent.graph import run_agent
            return await run_agent(s.ctx, "Review awards in 2019", s.saver, {"years": [2019]})        # the award is dated 2022
    state = go(scenario)
    assert state["flags_created"] == [] and any("outside the requested scope" in n for n in state["notes"])


def test_a_title_with_no_usable_words_is_not_called_a_set_of_similar_awards():
    """Found on the hosted app: 'P157: MESHA -RIUMUKURUE' has no title word common enough, so the cohort was the whole category (35,573
    awards). The card must say that, not 'similar awards'."""
    flag = tools.compute_price_benchmark(ocid="ocds-5whusi-KeRRA/08/39/NYR/NYERITOWN/GOK/AIE22058/2-31-22|23-165",
                                         award_id="20240528103637-84893")["result"]["flags"][0]
    comp = flag["detail"]["signals"][0]["comparison"]
    assert comp["label"].startswith("awards of the same category") and "weak comparison" in comp["label"] and "no title-word filter" in comp["basis"]
    assert "similar awards" not in flag["detail"]["signals"][0]["metric"]
