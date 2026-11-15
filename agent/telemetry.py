"""OpenTelemetry tracing: one trace per session, run -> LLM call -> tool call, written to a local file (and optionally the console).

The audit log (logs/*.jsonl) stays the record of truth. This module adds a second view of the same facts in the standard OpenTelemetry
shape, so any OTel tool can read it, with no collector and no network: spans go to `logs/traces.jsonl` (one span per line, OTel's own JSON)
and, if rules `telemetry.console` or the environment variable ZABUNI_OTEL_CONSOLE=1 says so, to stderr.

Design: nothing in the agent is instrumented call by call. `AuditLog` already sees every tool call and every LLM call at the moment it
finishes, with its duration, so it hands them to the `RunTracer`, which creates a finished span for each (start = end - duration) as a
child of the session's `run` span. One hook, no timing code scattered through the nodes, and the spans cannot disagree with the log.

Span attributes follow the OpenTelemetry GenAI names where one exists (gen_ai.system, gen_ai.request.model, gen_ai.usage.input_tokens,
gen_ai.usage.output_tokens) and use the `zabuni.*` prefix for the rest (cost, fallback, call kind, step reason, evidence count).
If the opentelemetry packages are missing or rules `telemetry.enabled` is false, `RunTracer.create` returns a no-op tracer.
"""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter, SpanExportResult
    from opentelemetry.trace import Status, StatusCode
    AVAILABLE = True
except ImportError:                                           # tracing is optional: the agent runs without it
    AVAILABLE = False
    SpanExporter = object                                     # type: ignore[assignment,misc]

NS_PER_S = 1_000_000_000
TRACE_FILE = "traces.jsonl"
SERVICE = "zabuni-ai"


class FileSpanExporter(SpanExporter):                         # type: ignore[misc]
    """Appends each finished span as one line of OpenTelemetry JSON. Thread-safe; never raises into the agent."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def export(self, spans: Any) -> Any:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self.path.open("a", encoding="utf-8") as f:
                for span in spans:
                    f.write(span.to_json(indent=None) + "\n")
            return SpanExportResult.SUCCESS
        except OSError:                                       # a full disk must not stop an investigation
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def _ns(iso: str | None) -> int | None:
    """An audit-log timestamp ('2026-11-15T16:25:14.061Z') as epoch nanoseconds."""
    if not iso:
        return None
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * NS_PER_S)


class NullTracer:
    """Same interface, does nothing (tracing off or the packages are not installed)."""
    enabled = False

    def start_run(self, *a: Any, **k: Any) -> None: ...
    def annotate(self, *a: Any, **k: Any) -> None: ...
    def end_run(self, *a: Any, **k: Any) -> None: ...
    def tool_call(self, *a: Any, **k: Any) -> None: ...
    def audit_event(self, *a: Any, **k: Any) -> None: ...


class RunTracer:
    """Creates the run span and the child spans for one session. Construct with `RunTracer.create(...)`."""
    enabled = True

    def __init__(self, exporters: list[Any], primary_model: str | None = None, query_chars: int = 200) -> None:
        self.provider = TracerProvider(resource=Resource.create({"service.name": SERVICE, "service.version": "1.1"}))
        for exporter in exporters:
            self.provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.tracer = self.provider.get_tracer("zabuni.agent")
        self.primary_model, self.query_chars = primary_model, int(query_chars)
        self.run_span: Any = None
        self._ctx: Any = None
        self.totals = {"llm_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
                       "malformed_attempts": 0, "fallbacks": 0, "tool_errors": 0}

    # ------------------------------------------------------------------ construction
    @classmethod
    def create(cls, logs_dir: Path, cfg: dict[str, Any], env_console: bool = False, primary_model: str | None = None,
               extra_exporters: list[Any] | None = None) -> "RunTracer | NullTracer":
        """The tracer for rules `telemetry` (enabled, file, console, query_chars); a NullTracer when disabled or unavailable."""
        if not AVAILABLE or not cfg.get("enabled", True):
            return NullTracer()
        exporters: list[Any] = [FileSpanExporter(Path(logs_dir) / cfg.get("file", TRACE_FILE))]
        if cfg.get("console") or env_console:
            exporters.append(ConsoleSpanExporter(out=sys.stderr, formatter=lambda span: span.to_json(indent=None) + "\n"))
        exporters += extra_exporters or []
        return cls(exporters, primary_model, int(cfg.get("query_chars", 200)))

    # ------------------------------------------------------------------ the run span
    def start_run(self, run_id: str, phase: str, query: str | None = None, model: str | None = None) -> None:
        attrs: dict[str, Any] = {"zabuni.run_id": run_id, "zabuni.phase": phase}
        if model:
            attrs["gen_ai.request.model"] = model
        if query:
            attrs["zabuni.query"] = query[: self.query_chars]
        self.run_span = self.tracer.start_span("run", attributes=attrs)
        self._ctx = trace.set_span_in_context(self.run_span)

    def annotate(self, **attrs: Any) -> None:
        """Add attributes to the run span (for example the request text once it is known)."""
        if self.run_span is not None:
            self.run_span.set_attributes({k: v for k, v in attrs.items() if v is not None})

    def end_run(self, error: str | None = None) -> None:
        """Close the run span with the session totals; flush and shut the provider down."""
        if self.run_span is None:
            return
        t = self.totals
        self.run_span.set_attributes({"zabuni.llm_calls": t["llm_calls"], "zabuni.tool_calls": t["tool_calls"],
                                      "gen_ai.usage.input_tokens": t["prompt_tokens"], "gen_ai.usage.output_tokens": t["completion_tokens"],
                                      "zabuni.cost_usd": round(t["cost_usd"], 6), "zabuni.malformed_attempts": t["malformed_attempts"],
                                      "zabuni.fallbacks": t["fallbacks"], "zabuni.tool_errors": t["tool_errors"]})
        if error:
            self.run_span.set_status(Status(StatusCode.ERROR, error[:200]))
        self.run_span.end()
        self.run_span = None
        self.provider.force_flush()
        self.provider.shutdown()

    # ------------------------------------------------------------------ child spans (called by AuditLog)
    def tool_call(self, record: dict[str, Any]) -> None:
        end = _ns(record.get("timestamp")) or 0
        start = end - int(float(record.get("duration_ms") or 0) * 1_000_000)
        summary = record.get("output_summary") or {}
        attrs = {"mcp.server": record.get("server"), "mcp.tool.name": record.get("tool"), "zabuni.run_id": record.get("run_id"),
                 "zabuni.tool.args_bytes": len(json.dumps(record.get("inputs"), default=str)),
                 "zabuni.tool.evidence_count": summary.get("evidence_count", 0),
                 "zabuni.step.reason": ((record.get("step") or {}).get("reason") or "")[:160],
                 "zabuni.tool.output_truncated": bool(record.get("output_truncated"))}
        span = self.tracer.start_span(f"tool.call {record.get('tool')}", context=self._ctx, start_time=start, attributes=attrs)
        self.totals["tool_calls"] += 1
        if record.get("error"):
            self.totals["tool_errors"] += 1
            span.set_status(Status(StatusCode.ERROR, str(record["error"])[:200]))
        span.end(end_time=end)

    def audit_event(self, type_: str, record: dict[str, Any]) -> None:
        """Turn the audit events that matter into spans or span events; others are ignored (they stay in the JSONL log)."""
        if type_ == "llm_call":
            self._llm_call(record)
        elif type_ == "llm_fallback" and self.run_span is not None:
            self.totals["fallbacks"] += 1
            self.run_span.add_event("llm_fallback", {"from_model": record.get("from_model") or "", "to_model": record.get("to_model") or "",
                                                     "cause": record.get("cause") or "", "sticky": bool(record.get("sticky"))})
        elif type_ == "llm_malformed" and self.run_span is not None:
            self.totals["malformed_attempts"] += 1
            self.run_span.add_event("llm_malformed", {"kind": record.get("kind") or "", "model": record.get("model") or ""})
        elif type_ in ("step_cap", "budget_stop", "synth_skipped", "scope_request", "untrusted_data", "correction_rejected") and self.run_span is not None:
            self.run_span.add_event(type_, {k: str(v)[:120] for k, v in record.items() if k in ("reason", "source", "redirected", "max_steps")})

    def _llm_call(self, record: dict[str, Any]) -> None:
        end = _ns(record.get("timestamp")) or 0
        start = end - int(float(record.get("seconds") or 0) * NS_PER_S)
        provider, _, model = str(record.get("model") or "").partition(":")
        prompt, completion = int(record.get("prompt_tokens") or 0), int(record.get("completion_tokens") or 0)
        cost = float(record.get("cost_usd") or 0.0)
        attrs = {"gen_ai.system": provider, "gen_ai.request.model": model, "gen_ai.usage.input_tokens": prompt, "gen_ai.usage.output_tokens": completion,
                 "zabuni.llm.kind": record.get("kind"), "zabuni.cost_usd": cost,
                 "zabuni.llm.fallback": bool(self.primary_model and record.get("model") != self.primary_model)}
        if record.get("est_remaining_daily_tokens") is not None:
            attrs["zabuni.est_remaining_daily_tokens"] = int(record["est_remaining_daily_tokens"])
        span = self.tracer.start_span(f"llm.call {record.get('kind')}", context=self._ctx, start_time=start, attributes=attrs)
        span.end(end_time=end)
        t = self.totals
        t["llm_calls"] += 1
        t["prompt_tokens"] += prompt
        t["completion_tokens"] += completion
        t["cost_usd"] += cost
