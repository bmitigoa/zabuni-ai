"""The card the reviewer reads, as data (app/cards.py), the model-fallback sentence, the CLI view and the memo wording."""
from agent.review import show_flag
from app import cards, engine
from mcp_server import tools
from mcp_server.fmt import kes

FLAGGED = {"ocid": "ocds-1", "award_id": "a1", "buyer": "Makueni County Government", "supplier": "FIRST ASSURANCE COMPANY",
           "date": "2022-12-14", "amount_kes": 197527657.0, "title": "PROVISION OF STAFF MEDICAL COVER"}


def flag(**over):
    base = {"flag_id": "flag_x", "rule": "compute_price_benchmark", "severity": "high", "status": "pending_review", "explanation": "Summary text.",
            "evidence": [{"ocid": "ocds-1", "award_id": "a1", "field": "award_amount", "value": 197527657.0},
                         {"ocid": "ocds-2", "award_id": "b1", "field": "award_amount", "value": 22595837.8},
                         {"ocid": "ocds-3", "award_id": "c1", "field": "award_amount", "value": 24000000.0}],
            "clarifications": [],
            "detail": {"subject": {"kind": "award", "name": None, "awards": [FLAGGED]},
                       "signals": [{"rule": "compute_price_benchmark", "summary": "s", "metric": "8.7x the median of 96 similar awards",
                                    "threshold": "ratio to median >= 3x (provisional)",
                                    "comparison": {"label": "similar awards", "n": 96, "median_kes": 22669244.0, "buyers": 40, "years": [2019, 2025],
                                                   "basis": "title words: staff, medical; category services"}},
                                   {"rule": "detect_price_anomalies", "summary": "s2", "metric": "71.0x the median of 4,659 comparable awards",
                                    "threshold": "outlier and >= 3x (provisional)",
                                    "comparison": {"label": "awards of the same category and buyer type", "n": 4659, "median_kes": 2780390.0,
                                                   "buyers": 81, "years": [2022, 2023], "basis": "category works"}}],
                       "hints": ["Possible data-entry error: verify the amount against the source document before relying on this flag."],
                       "comparison_note": "The multiples differ because the two checks compare the award with different sets of awards."}}
    base.update(over)
    return base


def test_the_flagged_award_is_shown_apart_from_the_comparison_set_with_money_formatted():
    c = cards.card(flag(), {"buyer": "Makueni County Government", "years": [2022, 2023]})
    assert c["flagged"] == [{"line": "Makueni County Government · FIRST ASSURANCE COMPANY · 2022-12-14 · KES 197,527,657 — PROVISION OF STAFF MEDICAL COVER",
                             "ocid": "ocds-1"}]
    assert [e["ocid"] for e in c["own_evidence"]] == ["ocds-1"] and [e["ocid"] for e in c["comparison_evidence"]] == ["ocds-2", "ocds-3"]
    assert c["own_evidence"][0]["shown"] == "KES 197,527,657" and c["comparison_evidence"][0]["shown"] == "KES 22,595,837.80"
    assert ".0" not in c["own_evidence"][0]["shown"] and c["subject_kind"] == "award"


def test_each_signal_states_its_comparison_and_whether_it_is_wider_than_the_request():
    c = cards.card(flag(), {"buyer": "Makueni County Government", "years": [2022, 2023]})
    first, second = c["signals"]
    assert first["title"] == "Price benchmark" and first["comparison_wider"] is True                # 40 buyers, years 2019-2025
    assert "Compared against: 96 similar awards (40 buyers, years 2019-2025), median KES 22,669,244" in first["comparison"]
    assert second["comparison_wider"] is True                                                         # 81 buyers although one was requested
    assert c["comparison_note"].startswith("The multiples differ") and c["provisional"] is True and len(c["hints"]) == 1


def test_a_comparison_inside_the_requested_scope_is_not_called_wider():
    narrow = flag()
    narrow["detail"]["signals"] = [{**narrow["detail"]["signals"][0], "comparison": {"label": "awards", "n": 30, "buyers": 1, "years": [2023, 2023]}}]
    assert cards.card(narrow, {"buyer": "Makueni County Government", "years": [2022, 2023]})["signals"][0]["comparison_wider"] is False


def test_a_buyer_level_flag_has_no_flagged_award_and_every_cited_record_is_an_example():
    f = flag(detail={"subject": {"kind": "buyer", "name": "Kenya Rural Roads Authority", "awards": []},
                     "signals": [{"rule": "detect_noncompetitive_method", "summary": "s", "metric": "2.1x", "threshold": "x (provisional)",
                                  "comparison": None}], "hints": []})
    c = cards.card(f, {})
    assert c["flagged"] == [] and c["subject_name"] == "Kenya Rural Roads Authority" and len(c["own_evidence"]) == 3 and c["comparison_evidence"] == []


def test_an_old_flag_without_detail_still_renders():
    f = flag(detail=None)
    c = cards.card(f, {})
    assert c["signals"][0]["metric"] == "Summary text." and c["provisional"] is False and c["flagged"] == []


def test_the_run_summary_says_which_model_took_over_and_why():
    quota = {"from_model": "groq:qwen/qwen3.8-27b", "to_model": "groq:openai/gpt-oss-120b", "reason": "provider quota exhausted", "cause": "quota", "sticky": True}
    fmt = {"from_model": "groq:qwen/qwen3.8-27b", "to_model": "groq:openai/gpt-oss-120b", "reason": "malformed tool call", "cause": "unusable output", "sticky": False}
    assert engine.describe_fallback(quota) == ("Fell back to gpt-oss-120b for the rest of the run, because qwen3.8-27b hit its quota or rate limit "
                                               "(provider quota exhausted).")
    assert "for that one call only" in engine.describe_fallback(fmt) and "could not use" in engine.describe_fallback(fmt)
    assert engine.short_model("groq:qwen/qwen3.8-27b") == "qwen3.8-27b" and engine.short_model("ollama:qwen2.5:7b") == "qwen2.5:7b"


def test_the_command_line_view_shows_the_flagged_award_the_signals_and_the_hint():
    f = flag()
    f.update(n_evidence=3)
    text = show_flag(f)
    assert "flagged award: Makueni County Government | FIRST ASSURANCE COMPANY | 2022-12-14 | KES 197,527,657 | ocds-1" in text
    assert "signal: 8.7x the median of 96 similar awards [ratio to median >= 3x (provisional)] compared against 96 similar awards" in text
    assert "HINT: Possible data-entry error" in text and "award_amount = KES 197,527,657" in text and ".0" not in text.split("award_amount = ")[1][:15]


def test_the_memo_lists_the_flagged_award_and_the_provisional_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(tmp_path / "flags.duckdb"))
    sig = tools.compute_price_benchmark(ocid="ocds-5whusi-42686", award_id="20250205150132-43031")["result"]["flags"][0]
    made = tools.flag_finding(ocid=sig["ocids"][0], rule=sig["rule"], severity="high", explanation="Eight times the median of similar awards.",
                              evidence=sig["evidence"], detail=sig["detail"])
    flag_id = made["result"]["flag_id"]
    tools.record_decision(flag_id=flag_id, decision="approve", reason="checked the cited records", reviewer_role="internal_auditor")
    md = tools.draft_eval_memo(flag_ids=[flag_id])["result"]["markdown"]
    assert "Flagged award:" in md and "KES 197,527,657" in md and "Makueni County Government" in md
    assert "(provisional)" in md and "Compared against" in md and ".0\n" not in md.split("Cited OCDS records:")[1].split("Reviewer decisions")[0]
    assert kes(197527657.0) in md


def test_two_checks_on_one_award_are_stored_as_one_flag_through_the_write_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(tmp_path / "flags.duckdb"))
    a = tools.compute_price_benchmark(ocid="ocds-5whusi-42686", award_id="20250205150132-43031")["result"]["flags"][0]
    b = next(f for f in tools.detect_price_anomalies(buyer="Makueni County Government", years=[2022, 2023], limit=5)["result"]["flags"]
             if f["ocids"][0] == "ocds-5whusi-42686")
    first = tools.flag_finding(ocid="ocds-5whusi-42686", rule=a["rule"], severity="high", explanation="Benchmark flag for this award.",
                               evidence=a["evidence"], detail=a["detail"])["result"]
    second = tools.flag_finding(ocid="ocds-5whusi-42686", rule=b["rule"], severity="high", explanation="Anomaly flag for this award.",
                                evidence=b["evidence"], detail=b["detail"])["result"]
    assert first["created"] and not second["created"] and first["flag_id"] == second["flag_id"]


def test_the_page_draws_the_card_with_the_flagged_award_the_signals_the_hint_and_the_provisional_badge():
    from pathlib import Path
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    st.cache_resource.clear()
    page = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py"), default_timeout=60)
    page.session_state["view"] = {"state": {"flags_created": [flag()], "summary": "1 check run.", "scope": {"buyer": "Makueni County Government", "years": [2022, 2023]},
                                            "stop_reasons": []},
                                  "usage": {"tokens": 1234, "tool_calls": 5, "cost_usd": 0.0012, "model": "groq:qwen/qwen3.8-27b"}, "model": "groq:qwen/qwen3.8-27b"}
    page.session_state["run_id"] = "run_x"
    page.session_state["fallbacks"] = [{"from_model": "groq:qwen/qwen3.8-27b", "to_model": "groq:openai/gpt-oss-120b", "reason": "quota", "cause": "quota", "sticky": True}]
    page.session_state["models_used"] = ["groq:qwen/qwen3.8-27b", "groq:openai/gpt-oss-120b"]
    page.run()
    assert not page.exception
    text = " ".join(m.value for m in page.markdown)
    assert "Flagged award" in text and "KES 197,527,657" in text and "Price benchmark" in text and "Price anomaly" in text
    assert "provisional thresholds" in text and "wider than your request" in " ".join(c.value for c in page.caption)
    assert any("Why the multiples differ" in c.value for c in page.caption)
    assert any("verify the amount against the source document" in w.value for w in page.warning)
    assert any("Fell back to gpt-oss-120b" in w.value for w in page.warning)
    assert "gpt-oss-120b" in " ".join(m.value for m in page.sidebar.markdown)                   # the sidebar shows the model actually used
