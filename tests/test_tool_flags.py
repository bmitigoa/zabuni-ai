"""Agent-facing tool outputs: result.flags (uniform across tools), widen_options (machine-readable recovery), sort_by."""
import pytest

from mcp_server import tools
from mcp_server.common import rows

BUYER = "Makueni County Government"
# A real, frozen award that the price benchmark flags: KenGen fuel, 78.8x the median of 12 comparable awards.
FLAGGED_OCID, FLAGGED_AWARD = "ocds-5whusi-4943", "20181030162550-2542"


def check_flag(flag, evidence_pool):
    assert set(flag) == {"rule", "summary", "severity_hint", "ocids", "evidence"}
    assert flag["evidence"] and flag["severity_hint"] in ("medium", "high")
    assert set(flag["ocids"]) == {e["ocid"] for e in flag["evidence"]}
    assert all(e in evidence_pool for e in flag["evidence"])            # a flag only cites evidence the tool returned


def test_price_benchmark_emits_a_flag_with_target_buyer_and_supplier_fields():
    out = tools.compute_price_benchmark(ocid=FLAGGED_OCID, award_id=FLAGGED_AWARD)
    res = out["result"]
    assert res["flagged"] and len(res["flags"]) == 1
    check_flag(res["flags"][0], out["evidence"])
    assert res["flags"][0]["rule"] == "compute_price_benchmark" and FLAGGED_OCID in res["flags"][0]["ocids"]
    assert "buyer" in res["target"] and "supplier" in res["target"]


def test_unflagged_results_have_no_flags():
    out = tools.compute_price_benchmark(title_keyword="fuel", category="goods", amount=1000)
    assert out["result"]["flagged"] is False and out["result"]["flags"] == []


def test_splitting_noncompetitive_and_concentration_emit_flags_that_cite_their_own_evidence():
    s = tools.detect_splitting(buyer=BUYER, limit=2)
    n = tools.detect_noncompetitive_method(limit=5)
    c = tools.supplier_concentration(limit=5)
    for out, rule in ((s, "detect_splitting"), (n, "detect_noncompetitive_method"), (c, "supplier_concentration")):
        flags = out["result"]["flags"]
        assert flags, f"{rule}: expected flags on this data"
        for f in flags:
            assert f["rule"] == rule
            check_flag(f, out["evidence"])
    assert len(s["result"]["flags"]) == len(s["result"]["clusters"])    # one flag per cluster


def test_ranking_flags_exist_only_for_flagged_entries():
    n = tools.detect_noncompetitive_method(limit=20)["result"]
    assert len(n["flags"]) == sum(1 for b in n["ranked_buyers"] if b["flagged"])
    c = tools.supplier_concentration(limit=20)["result"]
    assert len(c["flags"]) == sum(1 for b in c["buyers"] if b["flagged"])


def test_insufficient_comparables_returns_machine_readable_widen_options():
    out = tools.compute_price_benchmark(title_keyword="zzqxv nothing", category="goods", buyer_type="health", years=[2019])
    res = out["result"]
    assert res["insufficient_comparables"]
    ops = res["widen_options"]
    assert ops and all(o["op"] in ("drop", "set") and "arg" in o for o in ops)
    assert [o["arg"] for o in ops] == ["buyer_type", "years", "title_keyword", "category"]   # least damaging first
    assert len(res["widen_suggestions"]) == len(ops)


def test_auto_keyword_cohort_can_be_widened_with_a_set_operation():
    base = tools.compute_price_benchmark(ocid=FLAGGED_OCID, award_id=FLAGGED_AWARD)["result"]
    assert base["cohort"]["keywords_source"] == "auto"
    wide = tools.compute_price_benchmark(ocid=FLAGGED_OCID, award_id=FLAGGED_AWARD, auto_keywords=False)["result"]
    assert wide["cohort"]["keywords_source"] == "none" and wide["cohort"]["n"] > base["cohort"]["n"]


def test_search_sort_by_largest_and_its_validation():
    big = tools.search_awards(title_keyword="fuel", sort_by="largest", limit=10)["result"]["awards"]
    amounts = [a["amount_kes"] for a in big]
    assert amounts == sorted(amounts, reverse=True)
    new = tools.search_awards(buyer=BUYER, sort_by="newest", limit=10)["result"]["awards"]
    dates = [a["award_date"] for a in new if a["award_date"]]
    assert dates == sorted(dates, reverse=True)
    assert "sort_by must be one of" in tools.search_awards(buyer=BUYER, sort_by="random")["result"]["error"]
    top = rows("SELECT MAX(award_amount) AS m FROM award_facts WHERE title ILIKE '%fuel%' AND award_amount <= 1e10")[0]["m"]
    assert amounts[0] == top
