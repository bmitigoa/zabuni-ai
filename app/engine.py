"""The web app's engine: runs the agent and the review in background threads and lets the UI poll them.

Streamlit re-runs the whole script on every interaction, and the agent is async and slow (seconds on Groq, minutes on a
local model), so each run or review is a `Job`: a thread with its own event loop that collects the audit events as they happen
and ends with a plain result dict. The UI never touches asyncio. Nothing here decides anything about a tender: the same
`run_agent` / `resume_agent` as the command line is used, and a review is the same `decisions` list the CLI builds.

Each visitor gets a private flags database (`ZABUNI_FLAGS_DB` passed to that run's MCP server only), so one visitor's rejections
never suppress another's flags and a hosted demo shares nothing but the read-only OCDS data.
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.demo_guard import DemoGuard
from agent.graph import new_run_id, pause_payload, pending_payload, resume_agent, run_agent
from agent.llm import LLMError, LLMQuotaError
from agent.mcp_client import ToolCallFailed
from agent.session import log_run_usage, open_session
from agent.settings import ROOT, Settings
from agent.usage import UsageLedger

VISITOR_RE = re.compile(r"[^a-zA-Z0-9_-]")
APP_DATA_ENV = "ZABUNI_APP_DATA"
QUERY_MAX_CHARS = 400            # a request is a sentence or two; longer input is cut


def app_data_dir() -> Path:
    import os
    return Path(os.environ.get(APP_DATA_ENV) or ROOT / "data" / "app")


def visitor_flags_db(visitor_id: str) -> Path:
    """The private flags database of one visitor (the id is an opaque random token, sanitised for use in a file name)."""
    return app_data_dir() / "flags" / f"{VISITOR_RE.sub('', visitor_id)[:40] or 'anonymous'}.duckdb"


def build_query(buyer: str, supplier: str, years: list[int], free_text: str) -> tuple[str, dict[str, Any]]:
    """The request sentence and the explicit scope hint built from the form fields.

    Free text (what to look for) is combined with the fields; the hint goes to the agent unchanged so the scope guard is
    satisfied by what the user typed, not by what a model guessed."""
    parts = []
    text = (free_text or "").strip()[:QUERY_MAX_CHARS]
    if text:
        parts.append(text)
    scope_bits = []
    if buyer.strip():
        scope_bits.append(f"buyer {buyer.strip()}")
    if supplier.strip():
        scope_bits.append(f"supplier {supplier.strip()}")
    if years:
        scope_bits.append("years " + ", ".join(str(y) for y in sorted(years)))
    if scope_bits:
        parts.append("Scope: " + "; ".join(scope_bits) + ".")
    query = " ".join(parts) or ""
    hint = {"buyer": buyer.strip() or None, "supplier": supplier.strip() or None, "years": sorted(years) or None}
    return query, hint


@dataclass
class Job:
    """One background run or review. Poll `status`, read `events` while running and `result` when done."""
    kind: str                                    # "run" | "review"
    run_id: str
    status: str = "running"                      # running | paused | done | scope | error
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    result: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    thread: threading.Thread | None = None

    def echo(self, kind: str, rec: dict[str, Any]) -> None:
        self.events.append((kind, rec))

    @property
    def running(self) -> bool:
        return self.status == "running"


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, LLMQuotaError):
        return f"The language model's quota is used up: {exc}"
    if isinstance(exc, (LLMError, ToolCallFailed)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _finish(job: Job, session: Any, state: dict[str, Any], started: float) -> None:
    usage = log_run_usage(session, time.time() - started)
    payload = pause_payload(state)
    job.result = {"state": {k: v for k, v in state.items() if k != "__interrupt__"}, "payload": payload, "usage": usage,
                  "model": session.llm.name, "models_used": usage["models"] or [session.llm.name],
                  "fallbacks": [rec for kind, rec in job.events if kind == "llm_fallback"], "rules_review": session.rules["review"]}
    if state.get("scope_request"):
        job.status = "scope"
    else:
        job.status = "paused" if payload else "done"


def _start(job: Job, settings: Settings, visitor_id: str, work: Any) -> Job:
    def target() -> None:
        started = time.time()
        try:
            asyncio.run(work(job, started))
        except BaseException as exc:  # noqa: BLE001 - a thread must never die silently: the UI shows the error
            job.result = {"error": _error_text(exc)}
            job.status = "error"
    job.thread = threading.Thread(target=target, name=f"job-{job.run_id}", daemon=True)
    job.thread.start()
    return job


def start_run(settings: Settings, visitor_id: str, query: str, hint: dict[str, Any] | None, max_steps: int | None = None) -> Job:
    """Start an investigation in the background; it ends paused for review (or asking for a scope, or with an error)."""
    job = Job("run", new_run_id())
    env = {"ZABUNI_FLAGS_DB": str(visitor_flags_db(visitor_id))}

    async def work(j: Job, started: float) -> None:
        async with open_session(settings, j.run_id, echo=j.echo, max_steps=max_steps, extra_env=env) as s:
            state = await run_agent(s.ctx, query, s.checkpointer, hint)
            _finish(j, s, state, started)
    return _start(job, settings, visitor_id, work)


def start_review(settings: Settings, visitor_id: str, run_id: str, decisions: list[dict[str, Any]]) -> Job:
    """Resume a paused run with the reviewer's decisions (the contract `agent.review` uses); may pause again (clarify)."""
    job = Job("review", run_id)
    env = {"ZABUNI_FLAGS_DB": str(visitor_flags_db(visitor_id))}

    async def work(j: Job, started: float) -> None:
        async with open_session(settings, run_id, echo=j.echo, resume=True, extra_env=env) as s:
            state = await resume_agent(s.ctx, decisions, s.checkpointer)
            _finish(j, s, state, started)
    return _start(job, settings, visitor_id, work)


def read_memo(settings: Settings, run_id: str) -> str | None:
    """The saved memo of a run (the Filesystem MCP server wrote it to outputs/), or None."""
    path = Path(settings.outputs_dir) / f"memo_{run_id}.md"
    return path.read_text(encoding="utf-8") if path.is_file() else None


def make_guard(settings: Settings, rules: dict[str, Any]) -> DemoGuard:
    """One guard per process: runs per visitor per hour, and the estimated daily quota of the hosted model."""
    ledger = UsageLedger(Path(settings.logs_dir) / "llm_usage.jsonl")
    return DemoGuard(rules, ledger, settings.provider, settings.model)


def short_model(name: str | None) -> str:
    """'groq:openai/gpt-oss-120b' -> 'gpt-oss-120b'; 'groq:qwen/qwen3.8-27b' -> 'qwen3.8-27b'."""
    return (name or "").split(":", 1)[-1].split("/")[-1]


def describe_fallback(rec: dict[str, Any]) -> str:
    """One sentence for the run summary: which model took over and why (from the audit event, organisation ids already hidden)."""
    why = ("hit its quota or rate limit" if rec.get("cause") == "quota" else "kept returning output the agent could not use")
    detail = " ".join((rec.get("reason") or "").split()) if rec.get("cause") == "quota" else ""    # a format failure's text is provider noise
    detail = f" ({detail[:100]}...)" if len(detail) > 100 else (f" ({detail})" if detail else "")
    scope = "for the rest of the run" if rec.get("sticky", True) else "for that one call only"
    target = short_model(rec.get("to_model")) or "no other model"
    return f"Fell back to {target} {scope}, because {short_model(rec.get('from_model'))} {why}{detail}."


def summarise_event(kind: str, rec: dict[str, Any]) -> str | None:
    """A one-line description of an audit event for the live progress list (None = not worth showing)."""
    def short(obj: Any, n: int = 90) -> str:
        text = str(obj)
        return text if len(text) <= n else text[: n - 1] + "…"
    if kind == "plan":
        steps = "; ".join(f"{s['tool']}" for s in rec["steps"])
        return f"Plan: {short(rec['objective'], 120)} → {steps}"
    if kind == "tool_call":
        tail = f"error: {rec['error']}" if rec.get("error") else f"{(rec.get('output_summary') or {}).get('evidence_count', '?')} evidence"
        return f"Tool `{rec['tool']}` {short(rec['inputs'])} — {rec['duration_ms']:.0f} ms, {tail}"
    if kind == "follow_up":
        return f"Follow-up after {rec['trigger']}: `{rec['tool']}` ({rec['reason']})"
    if kind == "recovery":
        return f"Recovery in `{rec['tool']}`: widened ({rec['op']['op']} {rec['op']['arg']}), comparables {rec['comparables_before']} → {rec['comparables_after']}"
    if kind == "correction":
        return f"Correction in `{rec['tool']}`: {short(rec['error'], 80)}"
    if kind == "llm_call":
        return f"Model call ({rec['kind']}): {rec['total_tokens']} tokens, {rec['seconds']} s"
    if kind == "llm_fallback":
        return f"Model fallback: {rec['from_model']} → {rec['to_model']}"
    if kind in ("scope_request", "flags_capped", "budget_stop", "step_cap", "citation_guard", "rejected_flag_suppressed", "mcp_reconnect"):
        return f"{kind.replace('_', ' ').capitalize()}: {short(rec.get('message') or rec.get('reason') or rec.get('limit') or '', 110)}"
    if kind == "decision":
        return f"Decision recorded: {rec['decision']} by {rec['reviewer_role']}"
    if kind == "clarification":
        return f"Clarification answered for {rec['flag_id']}"
    if kind == "memo":
        return "Memo drafted from the approved flags"
    return None
