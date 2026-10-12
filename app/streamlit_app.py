"""Zabuni AI web app (Streamlit):  streamlit run app/streamlit_app.py

Decision support for procurement reviewers: describe what to look into (buyer, supplier, years), watch the agent plan and call its
tools, read the flags it raises with their cited OCDS records, then approve, reject or query each flag with a reason. Only approved
flags enter the evaluation memo, which can be downloaded. The tool never recommends or awards a tender.

All the work is done by `app/engine.py` (background jobs around the same agent code the command line uses); this file only draws.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import bootstrap, cards, engine  # noqa: E402
from app.examples import EXAMPLES  # noqa: E402
from app.memo_pdf import memo_to_pdf  # noqa: E402
from agent.settings import SettingsError, load_settings  # noqa: E402
from mcp_server.records import release_record  # noqa: E402
from mcp_server.rules import load_rules  # noqa: E402

BANNER = "Decision support only: this tool never recommends or awards a tender. A human reviewer decides every flag."
POLL_SECONDS = 1.5               # how often the page refreshes while a job runs
EVIDENCE_SHOWN = 6               # cited records listed before "show all"
SEVERITY_COLOUR = {"high": "red", "medium": "orange", "low": "blue"}
STATUS_LABEL = {"pending_review": "awaiting your decision", "approved": "approved", "rejected": "rejected",
                "clarification_requested": "question sent to the agent"}
DECISION_CHOICES = {"No decision yet": None, "Approve": "approve", "Reject": "reject", "Clarify (ask the agent)": "clarify"}


# ----------------------------------------------------------------------------- first start: secrets and data
def export_secrets() -> None:
    """Make Streamlit secrets (GROQ_API_KEY, HF_TOKEN, ...) visible to the settings code; the environment still wins."""
    try:
        secrets = st.secrets
        secrets.get("GROQ_API_KEY")               # touching it raises if no secrets file exists (local runs)
    except Exception:  # noqa: BLE001
        secrets = None
    bootstrap.export_secrets_to_env(secrets)


@st.cache_resource(show_spinner=False)
def prepare_data(_progress=None):
    return bootstrap.ensure_database(_progress)


def ensure_data_ready() -> bool:
    """Show 'Preparing data' while the first start fetches the snapshot and builds the database; False if it failed."""
    if bootstrap.database_ready():
        return True
    with st.status("Preparing data... (first start only, about a minute)", expanded=True) as status:
        try:
            prepare_data(lambda message: st.write(message))
        except Exception as exc:  # noqa: BLE001 - shown to the user; BootstrapError messages never contain a secret
            status.update(label="The data could not be prepared", state="error")
            st.error(str(exc))
            return False
        status.update(label="Data ready", state="complete", expanded=False)
    return True


# ----------------------------------------------------------------------------- cached resources
@st.cache_resource
def get_settings():
    return load_settings()


@st.cache_resource
def get_rules():
    return load_rules()


@st.cache_resource
def get_guard():
    return engine.make_guard(get_settings(), get_rules())


def video_url() -> str | None:
    return os.environ.get("DEMO_VIDEO_URL") or None


# ----------------------------------------------------------------------------- small pieces
def banner() -> None:
    st.warning(BANNER, icon=":material/gavel:")


def quota_message(text: str) -> None:
    st.error(text, icon=":material/hourglass_top:")
    url = video_url()
    if url:
        st.link_button("Watch the recorded demo video", url)


def record_page(ocid: str) -> None:
    """The stored OCDS record a flag cites (what the `ocds://release/{ocid}` resource returns)."""
    st.title("Stored OCDS record")
    st.caption("Rebuilt from the frozen PPRA Kenya snapshot. Only organisation names, amounts, dates and methods are stored; "
               "no contact details or individuals' names.")
    st.json(release_record(ocid))
    st.markdown("[Back to the app](./)")


def set_example(example: dict) -> None:
    st.session_state.f_buyer, st.session_state.f_supplier = example["buyer"], example["supplier"]
    st.session_state.f_years, st.session_state.f_text = example["years"], example["text"]


def init_state() -> None:
    ss = st.session_state
    ss.setdefault("visitor_id", uuid.uuid4().hex)
    ss.setdefault("job", None)
    ss.setdefault("view", None)          # the last finished job's result
    ss.setdefault("run_id", None)
    ss.setdefault("absorbed", True)
    ss.setdefault("tokens_used", 0)
    ss.setdefault("models_used", [])      # the models that actually answered in this run (a fallback adds one)
    ss.setdefault("fallbacks", [])
    ss.setdefault("f_buyer", "")
    ss.setdefault("f_supplier", "")
    ss.setdefault("f_years", [])
    ss.setdefault("f_text", "")


def absorb_finished_job() -> None:
    """Move a finished job's result into the session once."""
    ss = st.session_state
    job = ss.job
    if job is not None and not job.running and not ss.absorbed:
        ss.view = job.result
        ss.run_id = job.run_id
        ss.absorbed = True
        ss.tokens_used = (job.result.get("usage") or {}).get("tokens", ss.tokens_used)
        ss.models_used = job.result.get("models_used") or ss.models_used
        ss.fallbacks = ss.fallbacks + job.result.get("fallbacks", [])


# ----------------------------------------------------------------------------- sections
def ask_form(rules: dict) -> None:
    st.subheader("1. What should the agent look into?")
    st.caption("Name a buyer, a supplier or years. A request with none of these is not run: every tool would scan all of Kenya.")
    cols = st.columns(len(EXAMPLES))
    for col, ex in zip(cols, EXAMPLES):
        col.button(ex["label"], on_click=set_example, args=(ex,), use_container_width=True, key=f"ex_{ex['label']}")
    dc = rules["data_cleaning"]
    busy = st.session_state.job is not None and st.session_state.job.running
    with st.form("ask"):
        c1, c2, c3 = st.columns([3, 3, 2])
        c1.text_input("Buyer (organisation)", key="f_buyer", placeholder="e.g. Makueni County Government")
        c2.text_input("Supplier (company)", key="f_supplier", placeholder="e.g. Wavecrest Limited")
        c3.multiselect("Years", list(range(dc["min_year"], dc["max_year"] + 1)), key="f_years")
        st.text_area("What to look for (optional)", key="f_text", height=70,
                     placeholder="e.g. Review awards for contract splitting and price anomalies.")
        go = st.form_submit_button("Run investigation", type="primary", disabled=busy)
    if go:
        start_run(rules)


def start_run(rules: dict) -> None:
    ss = st.session_state
    query, hint = engine.build_query(ss.f_buyer, ss.f_supplier, ss.f_years, ss.f_text)
    if not (hint["buyer"] or hint["supplier"] or hint["years"]):
        st.info(rules["agent"]["scope_request_message"])
        return
    decision = get_guard().check(ss.visitor_id)
    if not decision.allowed:
        quota_message(decision.message)
        return
    ss.view, ss.run_id, ss.absorbed = None, None, False
    ss.models_used, ss.fallbacks = [], []
    ss.job = engine.start_run(get_settings(), ss.visitor_id, query or "Review the awards in this scope.", hint)
    st.rerun()


def progress(job: "engine.Job") -> None:
    title = "The agent is working" if job.kind == "run" else "Applying your decisions"
    with st.status(f"{title} ({time.time() - job.started:.0f} s)", expanded=True, state="running" if job.running else "complete"):
        lines = [engine.summarise_event(k, r) for k, r in list(job.events)]
        for line in [x for x in lines if x][-40:]:
            st.markdown(f"- {line}")


def usage_row(usage: dict | None, model: str | None) -> None:
    if not usage:
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("LLM tokens (whole run)", f"{usage['tokens']:,}")
    c2.metric("Tool calls", usage["tool_calls"])
    c3.metric("Estimated cost", f"${usage['cost_usd']:.4f}", help="Estimate from a dated price list; the free tier and local models cost nothing.")
    used = [engine.short_model(m) for m in st.session_state.models_used] or [engine.short_model(model or usage.get("model"))]
    c4.metric("Model(s) used", ", ".join(used))


def record_link(ocid: str) -> str:
    return f"[`{ocid}`](?record={quote(ocid, safe='')})"


def evidence_lines(items: list[dict]) -> list[str]:
    return [f"- {record_link(e['ocid'])} · award `{e['award_id']}` · {e['field']} = {e['shown']}" for e in items]


def flag_card(flag: dict, scope: dict) -> None:
    c = cards.card(flag, scope)
    with st.container(border=True):
        colour = SEVERITY_COLOUR.get(c["severity"], "gray")
        merged = f" · {len(c['signals'])} signals on one award" if len(c["signals"]) > 1 else ""
        st.markdown(f":{colour}[**{c['severity'].upper()}**]{merged} · {STATUS_LABEL.get(c['status'], c['status'])} · `{c['flag_id']}`"
                    + (" · :orange-badge[provisional thresholds]" if c["provisional"] else ""))
        if c["flagged"]:
            st.markdown("**Flagged " + ("award**" if len(c["flagged"]) == 1 else f"awards ({len(c['flagged'])})**"))
            for item in c["flagged"]:
                st.markdown(f"- {item['line']}  \n  {record_link(item['ocid'])}")
        elif c["subject_name"]:
            st.markdown(f"**Flagged {c['subject_kind']}:** {c['subject_name']}")
        for h in c["hints"]:
            st.warning(h, icon=":material/edit_note:")
        st.markdown("**Signals**")
        for sig in c["signals"]:
            st.markdown(f"- **{sig['title']}:** {sig['metric']}" + (f"  \n  _Threshold: {sig['threshold']}_" if sig["threshold"] else ""))
            if sig["comparison"]:
                st.caption(sig["comparison"] + ("  — **wider than your request**: it also covers buyers or years you did not name."
                                                if sig["comparison_wider"] else ""))
        if c["comparison_note"]:
            st.caption("Why the multiples differ: " + c["comparison_note"])
        for caveat in dict.fromkeys(sig["caveat"] for sig in c["signals"] if sig.get("caveat")):
            st.caption("Before you decide: " + caveat)
        st.caption("Summary: " + c["explanation"])
        own = evidence_lines(c["own_evidence"])
        if own:
            with st.expander(f"Cited records of the flagged " + ("award" if c["flagged"] else "subject") + f" ({len(own)})"):
                st.markdown("\n".join(own))
        if c["comparison_evidence"]:
            with st.expander(f"Comparison awards cited ({len(c['comparison_evidence'])}; not the flagged award)"):
                st.markdown("\n".join(evidence_lines(c["comparison_evidence"])))
        for cl in c["clarifications"]:
            st.info(f"**Question:** {cl.get('question')}  \n**Agent's answer:** {cl.get('response') or '(no answer yet)'}", icon=":material/help:")


def risk_board_and_review(rules: dict, state: dict) -> None:
    flags = state.get("flags_created") or []
    pending = [f for f in flags if f["status"] == "pending_review"]
    st.subheader("2. Risk board" + (" and your review" if pending else ""))
    if state.get("suppressed"):
        st.caption(f"{len(state['suppressed'])} flag(s) you rejected earlier were remembered and not raised again.")
    if state.get("capped"):
        st.caption(f"Flag cap: {len(state['capped'])} lower-ranked finding(s) were not raised (strongest first). Narrow the request to see them.")
    if not flags:
        st.info("No flags were raised for this scope. That is not evidence that nothing is wrong: the data has no bidder or item detail.")
        return
    rv = rules["review"]
    min_chars = int(rv["min_reason_chars"])
    decided: dict[str, tuple[str, str]] = {}
    with st.form("review"):
        for flag in flags:
            flag_card(flag, state.get("scope") or {})
            if flag["status"] == "pending_review":
                c1, c2 = st.columns([1, 3])
                choice = c1.radio("Decision", list(DECISION_CHOICES), key=f"d_{flag['flag_id']}", label_visibility="collapsed")
                reason = c2.text_area("Reason (for Clarify: your question to the agent)", key=f"r_{flag['flag_id']}", height=68)
                decided[flag["flag_id"]] = (choice, reason)
        role = st.selectbox("You are reviewing as (role; names are never recorded)", rv["roles"], index=rv["roles"].index("committee_member"))
        submit = st.form_submit_button("Submit decisions", type="primary", disabled=not pending)
    if submit:
        submit_decisions(decided, role, min_chars)


def submit_decisions(decided: dict, role: str, min_chars: int) -> None:
    decisions, problems = [], []
    for flag_id, (choice, reason) in decided.items():
        decision = DECISION_CHOICES[choice]
        if decision is None:
            continue
        if len(reason.strip()) < min_chars:
            problems.append(f"{flag_id}: a reason of at least {min_chars} characters is required.")
        else:
            decisions.append({"flag_id": flag_id, "decision": decision, "reason": reason.strip(), "reviewer_role": role})
    for p in problems:
        st.error(p)
    if problems:
        return
    if not decisions:
        st.warning("Choose Approve, Reject or Clarify for at least one flag. Flags without a decision stay pending and are not in the memo.")
        return
    ss = st.session_state
    for flag_id in decided:                      # forget the form state so a re-asked flag starts blank
        ss.pop(f"d_{flag_id}", None)
        ss.pop(f"r_{flag_id}", None)
    ss.absorbed = False
    ss.job = engine.start_review(get_settings(), ss.visitor_id, ss.run_id, decisions)
    st.rerun()


def memo_section(state: dict, usage: dict | None, model: str | None) -> None:
    memo = state.get("memo_markdown")
    if not memo:
        return
    st.subheader("3. Evaluation memo (approved flags only)")
    st.markdown(memo)
    c1, c2, _ = st.columns([1, 1, 4])
    run_id = st.session_state.run_id
    c1.download_button("Download Markdown", memo, file_name=f"memo_{run_id}.md", mime="text/markdown")
    try:
        c2.download_button("Download PDF", memo_to_pdf(memo), file_name=f"memo_{run_id}.pdf", mime="application/pdf")
    except Exception as exc:  # noqa: BLE001 - the Markdown download still works
        c2.caption(f"PDF unavailable: {type(exc).__name__}")
    usage_row(usage, model)


def sidebar(rules: dict) -> None:
    s = get_settings()
    with st.sidebar:
        st.markdown("### Zabuni AI")
        st.caption("Agentic decision support for public-procurement review. Data: PPRA Kenya OCDS snapshot (8 Oct 2026, frozen).")
        used = [engine.short_model(m) for m in st.session_state.models_used]
        if used:
            st.markdown("**Model used:** " + ", ".join(f"`{m}`" for m in used))
            if engine.short_model(f"{s.provider}:{s.model}") not in used:
                st.caption(f"Configured: `{s.model}` via {s.provider}")
        else:
            st.markdown(f"**Model:** `{s.model}` via {s.provider} (configured)")
        st.markdown(f"**Session tokens:** {st.session_state.tokens_used:,}")
        url = video_url()
        if url:
            st.link_button("Recorded demo video", url)
        st.markdown("[Source and README](https://github.com/bmitigoa/zabuni-ai)")
        st.caption("Screening indicators are prompts for human review, not findings of wrongdoing. "
                   f"Free-tier limits apply: {rules['demo']['runs_per_visitor_per_hour']} live runs per visitor per hour.")


# ----------------------------------------------------------------------------- page
def main() -> None:
    st.set_page_config(page_title="Zabuni AI", page_icon=":material/balance:", layout="wide")
    export_secrets()
    if not ensure_data_ready():
        return
    ocid = st.query_params.get("record")
    if ocid:
        banner()
        record_page(ocid)
        return
    init_state()
    try:
        rules = get_rules()
        get_settings()
    except SettingsError as exc:
        st.error(f"Configuration error: {exc}")
        return
    absorb_finished_job()                 # before the sidebar, so its token count is current
    sidebar(rules)
    st.title("Zabuni AI")
    st.caption("Zabuni means tender in Swahili. Value-for-money and integrity screening of published procurement awards.")
    banner()
    ask_form(rules)
    absorb_finished_job()
    job = st.session_state.job
    if job is not None and job.running:
        progress(job)
        time.sleep(POLL_SECONDS)
        st.rerun()
    view = st.session_state.view
    if job is not None and view is not None:
        with st.expander("What the agent did", expanded=False):
            lines = [engine.summarise_event(k, r) for k, r in list(job.events)]
            st.markdown("\n".join(f"- {x}" for x in lines if x) or "(nothing logged)")
    if view is None:
        return
    if "error" in view:
        text = view["error"]
        if "quota" in text.lower() or "rate limit" in text.lower():
            quota_message(rules["demo"]["quota_message"])
        else:
            st.error(text)
        return
    state = view["state"]
    if state.get("scope_request"):
        st.info(state["scope_request"])
        return
    st.subheader("Summary")
    st.write(state.get("summary") or "")
    for reason in state.get("stop_reasons", []):
        st.caption(f"The run stopped early: {reason}")
    for rec in st.session_state.fallbacks:
        st.warning(engine.describe_fallback(rec), icon=":material/swap_horiz:")
    risk_board_and_review(rules, state)
    memo_section(state, view.get("usage"), view.get("model"))
    if not state.get("memo_markdown"):
        usage_row(view.get("usage"), view.get("model"))


main()
