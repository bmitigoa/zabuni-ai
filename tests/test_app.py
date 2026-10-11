"""The web app: engine jobs (real MCP servers, fake LLM), the memo PDF, and the Streamlit page via its headless test harness."""
import time
from pathlib import Path

import pytest

from agent.settings import Settings
from app import engine
from app.memo_pdf import memo_to_pdf
from tests.fakes import FakeLLM
from tests.test_review_flow import BUYER, make_llm, plan_for, synth  # noqa: F401  (synth is used through make_llm)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Private logs, outputs, checkpoint database and app data for one test; the LLM is the deterministic fake."""
    monkeypatch.setenv("ZABUNI_CHECKPOINT_DB", str(tmp_path / "cp.sqlite"))
    monkeypatch.setenv(engine.APP_DATA_ENV, str(tmp_path / "appdata"))
    monkeypatch.delenv("ZABUNI_FLAGS_DB", raising=False)
    monkeypatch.setattr("agent.session.create_llm", lambda settings, rules, ledger=None: make_llm())
    return Settings(provider="ollama", model="qwen2.5:7b", logs_dir=tmp_path / "logs", outputs_dir=tmp_path / "outputs")


def wait(job, seconds=120):
    deadline = time.time() + seconds
    while job.running and time.time() < deadline:
        time.sleep(0.5)
    assert not job.running, "the job did not finish"
    return job


# ----------------------------------------------------------------------------- pure helpers
def test_the_form_fields_become_a_request_and_an_explicit_scope():
    query, hint = engine.build_query(" Makueni County Government ", "", [2023, 2022], "Look for splitting.")
    assert query == "Look for splitting. Scope: buyer Makueni County Government; years 2022, 2023."
    assert hint == {"buyer": "Makueni County Government", "supplier": None, "years": [2022, 2023]}
    assert engine.build_query("", "", [], "")[1] == {"buyer": None, "supplier": None, "years": None}
    assert len(engine.build_query("", "", [2023], "x" * 1000)[0]) < 450


def test_each_visitor_gets_a_private_sanitised_flags_database(monkeypatch, tmp_path):
    monkeypatch.setenv(engine.APP_DATA_ENV, str(tmp_path))
    a, b = engine.visitor_flags_db("abc123"), engine.visitor_flags_db("def456")
    assert a != b and a.parent == tmp_path / "flags" and a.suffix == ".duckdb"
    assert engine.visitor_flags_db("../../etc/passwd").parent == tmp_path / "flags"          # no path traversal through the id


def test_events_are_summarised_for_the_progress_list():
    assert "Tool `search_awards`" in engine.summarise_event("tool_call", {"tool": "search_awards", "inputs": {"a": 1}, "duration_ms": 12.0,
                                                                          "error": None, "output_summary": {"evidence_count": 3}})
    assert engine.summarise_event("plan", {"objective": "o", "steps": [{"tool": "t1"}, {"tool": "t2"}]}).endswith("t1; t2")
    assert engine.summarise_event("llm_fallback", {"from_model": "a", "to_model": "b"}) == "Model fallback: a → b"
    assert engine.summarise_event("something_internal", {}) is None


def test_the_memo_pdf_is_a_real_pdf_even_with_non_latin_characters():
    memo = "# Evaluation memo\n\n> This memo does not recommend or award any tender.\n\n## Summary\n\n- ratio ≥ 3× the median — `ocds-1`\n- **bold** text …\n"
    pdf = memo_to_pdf(memo)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1500


# ----------------------------------------------------------------------------- jobs: run -> pause -> review -> memo
def test_a_run_pauses_for_review_and_a_review_job_finishes_it_with_a_memo(app_env, tmp_path):
    visitor = "visitor-one"
    job = wait(engine.start_run(app_env, visitor, "Review Makueni County Government awards 2022 2023 2024", {"buyer": BUYER, "years": [2023]}))
    assert job.status == "paused" and job.result["payload"]["flags"]
    flags = job.result["state"]["flags_created"]
    assert flags and {f["status"] for f in flags} == {"pending_review"}
    assert any(k == "tool_call" for k, _ in job.events) and any(k == "plan" for k, _ in job.events)
    assert engine.visitor_flags_db(visitor).is_file()                                           # the flags went to the visitor's own file
    assert job.result["usage"]["tool_calls"] > 0

    decisions = [{"flag_id": f["flag_id"], "decision": "approve", "reason": "checked the cited records", "reviewer_role": "committee_member"}
                 for f in flags]
    review = wait(engine.start_review(app_env, visitor, job.run_id, decisions))
    assert review.status == "done" and review.result["payload"] is None
    memo = review.result["state"]["memo_markdown"]
    assert all(f["flag_id"] in memo for f in flags) and "does not recommend or award any tender" in memo
    assert engine.read_memo(app_env, job.run_id) == memo                                         # and it was saved through the Filesystem MCP server


def test_a_request_without_scope_ends_as_a_scope_question_not_a_run(app_env, monkeypatch):
    def planner_without_scope(s, r, ledger=None):
        return FakeLLM({**make_llm().script, type(plan_for()): plan_for(("detect_splitting", {}), buyer=None, years=())})
    monkeypatch.setattr("agent.session.create_llm", planner_without_scope)
    job = wait(engine.start_run(app_env, "v2", "hello", None))
    assert job.status == "scope" and job.result["state"]["scope_request"] and job.result["payload"] is None


def test_a_failing_job_reports_an_error_instead_of_dying_silently(app_env, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("agent.session.create_llm", broken)
    job = wait(engine.start_run(app_env, "v3", "x", {"buyer": BUYER}))
    assert job.status == "error" and "boom" in job.result["error"]


def test_visitors_do_not_share_rejections(app_env):
    flags_by_visitor = {}
    for visitor in ("alice", "bob"):
        job = wait(engine.start_run(app_env, visitor, "Review Makueni County Government awards 2022 2023 2024", {"buyer": BUYER}))
        flags = job.result["state"]["flags_created"]
        if visitor == "alice":
            rejections = [{"flag_id": f["flag_id"], "decision": "reject", "reason": "lawful framework contract", "reviewer_role": "internal_auditor"}
                          for f in flags]
            wait(engine.start_review(app_env, visitor, job.run_id, rejections))
        flags_by_visitor[visitor] = {f["flag_id"] for f in flags}
    assert flags_by_visitor["alice"] and flags_by_visitor["bob"]                 # Bob still sees flags Alice rejected


# ----------------------------------------------------------------------------- the Streamlit page
APP = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")


@pytest.fixture
def at():
    from streamlit.testing.v1 import AppTest
    import streamlit as st
    st.cache_resource.clear()
    page = AppTest.from_file(APP, default_timeout=60)
    page.run()
    return page


def test_the_page_shows_the_decision_support_banner_and_the_form(at):
    assert not at.exception
    assert any("never recommends or awards a tender" in w.value for w in at.warning)
    assert at.text_input(key="f_buyer") is not None and at.text_input(key="f_supplier") is not None
    assert [b.label for b in at.button if b.label.startswith("Makueni")]                          # example queries


def test_submitting_without_buyer_supplier_or_years_asks_for_a_scope_and_starts_nothing(at):
    submit = [b for b in at.button if b.label == "Run investigation"][0]
    submit.click().run()
    assert any("names no buyer, supplier or period" in i.value for i in at.info)
    assert at.session_state["job"] is None


def test_an_example_fills_the_form(at):
    [b for b in at.button if b.label.startswith("Makueni")][0].click().run()
    assert at.text_input(key="f_buyer").value == "Makueni County Government"


def test_when_the_daily_quota_is_used_the_visitor_sees_a_friendly_message_with_the_video_link(monkeypatch, at):
    from agent.demo_guard import Decision

    class Denied:
        def check(self, visitor_id):
            return Decision(False, "quota", "The daily free-tier quota for the live model has been reached. Please watch the recorded demo video.")
    monkeypatch.setenv("DEMO_VIDEO_URL", "https://example.org/demo")
    monkeypatch.setattr(engine, "make_guard", lambda settings, rules: Denied())
    import streamlit as st
    st.cache_resource.clear()
    at.text_input(key="f_buyer").set_value("Makueni County Government")
    [b for b in at.button if b.label == "Run investigation"][0].click().run()
    assert any("daily free-tier quota" in e.value for e in at.error) and at.session_state["job"] is None


def test_the_record_page_shows_a_stored_award_record():
    from streamlit.testing.v1 import AppTest
    from mcp_server.common import rows
    ocid = rows("SELECT ocid FROM awards LIMIT 1")[0]["ocid"]
    page = AppTest.from_file(APP, default_timeout=60)
    page.query_params["record"] = ocid
    page.run()
    assert not page.exception and page.title[0].value == "Stored OCDS record" and page.json
