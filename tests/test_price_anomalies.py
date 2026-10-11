"""detect_price_anomalies: Isolation Forest outliers per cohort, deterministic, every flag sourced to real awards."""
import pytest

from mcp_server import tools
from mcp_server.common import rows
from mcp_server.registry import TOOLS
from mcp_server.rules import load_rules

PA = load_rules()["price_anomalies"]


@pytest.fixture(scope="module")
def ranked():
    return tools.detect_price_anomalies(limit=5)


def test_the_ranking_returns_high_side_outliers_with_a_ratio_explanation(ranked):
    res = ranked["result"]
    assert 1 <= len(res["anomalies"]) <= 5 and len(res["flags"]) == len(res["anomalies"])
    ratios = [a["ratio_to_median"] for a in res["anomalies"]]
    assert ratios == sorted(ratios, reverse=True) and min(ratios) >= PA["min_ratio_to_median"]
    for a in res["anomalies"]:
        assert f"{a['ratio_to_median']:.1f}x median of " in a["explanation"] and "comparable awards" in a["explanation"]
        assert a["cohort"]["n"] >= PA["min_cohort"] and len(a["comparables_cited"]) == PA["comparables_cited"]
    assert all(c["n"] >= PA["min_cohort"] for c in res["cohorts"])                       # small cohorts are never modelled


def test_the_same_data_always_gives_the_same_answer(ranked):
    again = tools.detect_price_anomalies(limit=5)
    assert again["result"]["anomalies"] == ranked["result"]["anomalies"] and again["evidence"] == ranked["evidence"]


def test_every_anomaly_and_comparable_is_a_real_award_with_the_cited_amount(ranked):
    for e in ranked["evidence"]:
        found = rows("SELECT award_amount FROM award_facts WHERE ocid = ? AND award_id = ?", [e["ocid"], e["award_id"]])
        assert found and float(found[0]["award_amount"]) == pytest.approx(float(e["value"])), e
    for f in ranked["result"]["flags"]:
        assert f["rule"] == "detect_price_anomalies" and f["evidence"] and f["ocids"] and f["severity_hint"] in ("low", "medium", "high")


def test_a_buyer_filter_keeps_the_national_cohort_but_reports_only_that_buyer():
    out = tools.detect_price_anomalies(buyer="Makueni County Government")
    assert all(a["buyer"].strip().lower().startswith("makueni") for a in out["result"]["anomalies"])
    assert out["params_used"]["buyer_resolved"]
    assert out["result"]["cohorts"] and max(c["n"] for c in out["result"]["cohorts"]) > PA["min_cohort"]
    unknown = tools.detect_price_anomalies(buyer="Zzzz Nonexistent Buyer")
    assert "error" in unknown["result"]                                                  # same buyer resolution as every tool


def test_a_narrow_scope_with_too_few_awards_is_reported_not_modelled():
    out = tools.detect_price_anomalies(title_keyword="zzqxv nothing")
    assert out["result"]["anomalies"] == [] and out["result"]["flags"] == [] and out["warnings"]
    small = tools.detect_price_anomalies(category="goods", buyer_type="health", years=[2019], title_keyword="generator")
    assert small["result"]["cohorts"] == [] or all(c["n"] >= PA["min_cohort"] for c in small["result"]["cohorts"])


def test_it_is_a_read_only_discoverable_tool_without_a_role():
    spec = TOOLS["detect_price_anomalies"]
    assert spec.read_only and spec.role is None                                         # so the Planner can see it
