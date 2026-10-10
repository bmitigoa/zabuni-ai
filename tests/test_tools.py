"""Tests for the zabuni-mcp tools against real PPRA Kenya data, plus edge cases.

Every tool is checked for: real-data behaviour, the {result, evidence, params_used, warnings} envelope,
evidence that points at records that really exist, and graceful handling of bad input / empty results.
"""
import json

import pytest

from mcp_server import tools
from mcp_server.common import rows
from mcp_server.rules import PROVISIONAL, load_rules

BUYER = "Makueni County Government"
ENVELOPE_KEYS = {"result", "evidence", "params_used", "warnings"}


def assert_envelope(out):
    assert set(out) == ENVELOPE_KEYS
    assert isinstance(out["evidence"], list) and isinstance(out["warnings"], list)
    for e in out["evidence"]:
        assert set(e) == {"ocid", "award_id", "field", "value"}


def assert_evidence_exists(out):
    """Every evidence item must cite an award that exists in the stored OCDS data."""
    for e in out["evidence"]:
        hit = rows("SELECT 1 FROM awards WHERE ocid = ? AND award_id = ?", [e["ocid"], e["award_id"]])
        assert hit, f"evidence cites a non-existent award: {e}"


def is_error(out):
    return isinstance(out["result"], dict) and "error" in out["result"]


# ------------------------------------------------------------------------------- search_awards
def test_search_awards_real_data():
    out = tools.search_awards(buyer=BUYER, limit=5)
    assert_envelope(out)
    r = out["result"]
    assert r["total_matches"] > 1000 and r["returned"] == 5
    assert len(out["evidence"]) == 5 and all(e["field"] == "award_amount" for e in out["evidence"])
    assert all("MAKUENI" in a["buyer"].upper() for a in r["awards"])
    assert_evidence_exists(out)


def test_search_awards_filters_combine():
    out = tools.search_awards(buyer=BUYER, title_keyword="water", date_from="2023-01-01", date_to="2023-12-31", limit=50)
    assert_envelope(out)
    assert out["result"]["returned"] > 0
    for a in out["result"]["awards"]:
        assert "water" in a["title"].lower() and a["award_date"].startswith("2023")


def test_search_awards_limit_is_clamped():
    out = tools.search_awards(buyer=BUYER, limit=10_000)
    assert out["result"]["returned"] <= 50 and out["params_used"]["limit"] == 50


@pytest.mark.parametrize("kwargs", [
    {},                                              # no filter at all
    {"buyer": "   "},                                # blank
    {"buyer": "!!!"},                                # nothing searchable after normalisation
    {"date_from": "not-a-date"},
])
def test_search_awards_bad_input_returns_error(kwargs):
    out = tools.search_awards(**kwargs)
    assert_envelope(out)
    assert is_error(out) and out["evidence"] == [] and out["warnings"]


def test_search_awards_empty_results_do_not_crash():
    out = tools.search_awards(buyer=BUYER, date_from="2030-01-01")
    assert_envelope(out)
    assert out["result"]["total_matches"] == 0 and out["evidence"] == [] and out["warnings"]
    assert tools.search_awards(supplier="zzzz no such supplier")["result"]["awards"] == []


# ------------------------------------------------------------------------- compute_price_benchmark
def test_benchmark_keyword_cohort_statistics():
    out = tools.compute_price_benchmark(title_keyword="fuel", category="goods")
    assert_envelope(out)
    c = out["result"]["cohort"]
    assert c["n"] >= 10 and not out["result"]["insufficient_comparables"]
    assert c["p25"] <= c["median"] <= c["p75"] and c["iqr"] == pytest.approx(c["p75"] - c["p25"])
    assert out["evidence"] and len(out["result"]["comparables_cited"]) <= 10
    assert_evidence_exists(out)


def test_benchmark_for_specific_award_is_consistent(known_award):
    out = tools.compute_price_benchmark(ocid=known_award["ocid"], award_id=known_award["award_id"])
    assert_envelope(out)
    r = out["result"]
    t = r["target"]
    assert t["ocid"] == known_award["ocid"] and out["evidence"][0]["ocid"] == known_award["ocid"]
    assert t["ratio_to_median"] == pytest.approx(t["amount_kes"] / r["cohort"]["median"], abs=0.01)
    assert f"{t['ratio_to_median']:.1f}x median of {r['cohort']['n']}" in r["explanation"]
    assert_evidence_exists(out)


def test_benchmark_flags_extreme_hypothetical_amount_and_not_a_cheap_one():
    high = tools.compute_price_benchmark(title_keyword="fuel", category="goods", amount=5e9)
    low = tools.compute_price_benchmark(title_keyword="fuel", category="goods", amount=1000)
    assert high["result"]["flagged"] and high["result"]["target"]["ratio_to_median"] >= 3 and high["evidence"]
    assert not low["result"]["flagged"]


def test_benchmark_too_few_comparables_asks_agent_to_widen():
    out = tools.compute_price_benchmark(title_keyword="zzqxv no such words", category="goods")
    assert_envelope(out)
    r = out["result"]
    assert r["cohort"]["n"] == 0 and r["insufficient_comparables"] and r["flagged"] is False
    assert r["widen_suggestions"] and out["evidence"] == [] and out["warnings"]


def test_benchmark_small_cohort_is_marked_insufficient_and_never_flagged():
    out = tools.compute_price_benchmark(title_keyword="fuel", category="goods", buyer_type="health",
                                        years=[2018], amount=5e9)
    n = out["result"]["cohort"]["n"]
    assert out["result"]["insufficient_comparables"] == (n < 10)
    if n < 10:
        assert out["result"]["flagged"] is False and out["result"]["widen_suggestions"]


@pytest.mark.parametrize("kwargs", [
    {},                                                  # nothing to benchmark
    {"title_keyword": "fuel", "category": "gadgets"},    # bad category
    {"title_keyword": "fuel", "buyer_type": "alien"},    # bad buyer type
    {"title_keyword": "fuel", "years": [1999]},          # outside data window
    {"title_keyword": "fuel", "amount": -5},             # bad amount
    {"ocid": "ocds-does-not-exist"},                     # unknown ocid
])
def test_benchmark_bad_input_returns_error(kwargs):
    out = tools.compute_price_benchmark(**kwargs)
    assert_envelope(out)
    assert is_error(out) and out["evidence"] == []


# ------------------------------------------------------------------------------ detect_splitting
def test_splitting_clusters_satisfy_the_rule():
    out = tools.detect_splitting(buyer=BUYER)
    assert_envelope(out)
    sp = load_rules()["splitting"]
    r = out["result"]
    assert r["total_clusters"] >= 1
    cited = {(e["ocid"], e["award_id"]) for e in out["evidence"]}
    for c in r["clusters"]:
        T = c["threshold_kes"]
        assert c["n_awards"] >= sp["min_awards"] and c["span_days"] <= sp["window_days"]
        assert c["total_kes"] >= T and all(sp["near_below_ratio"] * T <= a["amount_kes"] < T for a in c["awards"])
        assert {(a["ocid"], a["award_id"]) for a in c["awards"]} <= cited
    assert_evidence_exists(out)


def test_splitting_collapses_duplicate_publications():
    out = tools.detect_splitting(buyer=BUYER)
    assert any("duplicate" in w for w in out["warnings"])
    for c in out["result"]["clusters"]:  # no two awards in a cluster may be identical records
        keys = [(a["title"].lower(), a["amount_kes"], a["award_date"]) for a in c["awards"]]
        assert len(keys) == len(set(keys))


def test_splitting_window_and_thresholds_are_configurable():
    wide = tools.detect_splitting(buyer=BUYER, window_days=365)["result"]["total_clusters"]
    narrow = tools.detect_splitting(buyer=BUYER, window_days=1)["result"]["total_clusters"]
    assert wide >= narrow
    none = tools.detect_splitting(buyer=BUYER, thresholds_kes=[1])  # nothing sits in [0.5, 1)
    assert none["result"]["total_clusters"] == 0 and none["evidence"] == [] and none["warnings"]


def test_splitting_whole_dataset_scan_respects_limit():
    out = tools.detect_splitting(limit=3)
    assert len(out["result"]["clusters"]) <= 3 and out["result"]["total_clusters"] >= 3
    assert any("scanned all buyers" in w for w in out["warnings"])


def test_splitting_unknown_and_ambiguous_buyer():
    unknown = tools.detect_splitting(buyer="Makueni Cnty Govrnment")
    assert is_error(unknown) and "MAKUENI COUNTY GOVERNMENT" in unknown["result"]["candidates"]
    ambiguous = tools.detect_splitting(buyer="County")
    assert is_error(ambiguous) and len(ambiguous["result"]["candidates"]) > 1
    assert is_error(tools.detect_splitting(buyer=BUYER, thresholds_kes=[-5]))
    assert is_error(tools.detect_splitting(years=[1990]))


# ------------------------------------------------------------------- detect_noncompetitive_method
def test_noncompetitive_buyer_vs_national_baseline():
    out = tools.detect_noncompetitive_method(buyer=BUYER)
    assert_envelope(out)
    s, base = out["result"]["buyer_stats"], out["result"]["baseline"]
    assert s["n_noncompetitive"] == s["n_direct"] + s["n_restricted"]
    assert 0 <= s["share_noncompetitive"] <= 1 and base["n_awards"] > s["n_awards"]
    assert s["ratio_to_baseline"] == pytest.approx(s["share_noncompetitive"] / base["share_noncompetitive"], abs=0.01)
    assert out["evidence"] and {e["value"] for e in out["evidence"]} <= {"direct", "selective"}
    assert_evidence_exists(out)


def test_noncompetitive_ranking_mode_and_flags_follow_rules():
    out = tools.detect_noncompetitive_method(limit=5)
    assert_envelope(out)
    nc = load_rules()["noncompetitive_method"]
    ranked = out["result"]["ranked_buyers"]
    assert 1 <= len(ranked) <= 5 and out["evidence"]
    shares = [b["share_noncompetitive"] for b in ranked]
    assert shares == sorted(shares, reverse=True)
    for b in ranked:
        if b["flagged"]:
            assert b["ratio_to_baseline"] >= nc["flag_ratio_to_baseline"] and b["n_noncompetitive"] >= nc["min_noncompetitive_awards"]
    assert_evidence_exists(out)


def test_noncompetitive_years_filter_and_bad_input():
    y = tools.detect_noncompetitive_method(buyer=BUYER, years=[2023, 2024])
    all_years = tools.detect_noncompetitive_method(buyer=BUYER)
    assert y["result"]["buyer_stats"]["n_awards"] < all_years["result"]["buyer_stats"]["n_awards"]
    assert is_error(tools.detect_noncompetitive_method(buyer="Zzzz Not A Buyer"))
    assert is_error(tools.detect_noncompetitive_method(years=[2031]))


# --------------------------------------------------------------------------- supplier_concentration
def test_concentration_for_a_buyer():
    out = tools.supplier_concentration(buyer=BUYER, limit=20)
    assert_envelope(out)
    r = out["result"]
    sup = r["suppliers"]
    assert 1 <= len(sup) <= 20 and sum(s["share_count"] for s in sup) <= 1.0001
    assert [s["share_value"] for s in sup] == sorted((s["share_value"] for s in sup), reverse=True)
    assert 0 < r["hhi_value"] <= 10_000 and r["top_share_value"] == sup[0]["share_value"]
    assert any("supplier name" in w for w in out["warnings"])          # coverage is always disclosed
    assert len(out["evidence"]) >= len(sup) and all(s["largest_awards"] for s in sup)
    assert_evidence_exists(out)


def test_concentration_ranking_and_flag_rule():
    out = tools.supplier_concentration(limit=10)
    sc = load_rules()["supplier_concentration"]
    buyers = out["result"]["buyers"]
    assert buyers and out["evidence"]
    for b in buyers:
        assert b["flagged"] == (b["n_awards_named"] >= sc["min_awards"]
                                and max(b["top_share_count"], b["top_share_value"]) >= sc["flag_share"])
    assert_evidence_exists(out)


def test_concentration_edge_cases():
    assert is_error(tools.supplier_concentration(buyer="Zzzz Not A Buyer"))
    assert is_error(tools.supplier_concentration(buyer=BUYER, years=[1990]))
    few = rows("SELECT buyer_name FROM award_facts WHERE supplier_norm IS NOT NULL "
               "GROUP BY 1 HAVING COUNT(*) BETWEEN 2 AND 5 LIMIT 1")
    out = tools.supplier_concentration(buyer=few[0]["buyer_name"])
    assert out["result"]["flagged"] is False and any("unstable" in w for w in out["warnings"])


# --------------------------------------------------------------------------- resources / rules
def test_release_record_for_citation(known_award):
    rec = tools.release_record(known_award["ocid"])
    assert rec["ocid"] == known_award["ocid"] and rec["tender"]["buyer_name"]
    assert any(a["award_id"] == known_award["award_id"] for a in rec["awards"])
    blob = json.dumps(rec, default=str).lower()
    assert not any(w in blob for w in ("email", "phone", "telephone", "contactpoint"))  # no personal data
    assert "error" in tools.release_record("ocds-does-not-exist")
    assert "error" in tools.release_record("  ")


def test_rules_are_labelled_provisional_and_overridable(monkeypatch, tmp_path):
    assert load_rules()["status"] == PROVISIONAL
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"splitting": {"window_days": 14}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(f))
    merged = load_rules()
    assert merged["splitting"]["window_days"] == 14 and merged["splitting"]["min_awards"] == 3


def test_every_tool_is_documented_in_red_flags():
    from mcp_server.registry import TOOLS          # derived from the registry: adding a tool never edits this test
    doc = open("docs/red_flags.md", encoding="utf-8").read()
    for name in TOOLS:
        assert name in doc, f"{name} missing from docs/red_flags.md"
