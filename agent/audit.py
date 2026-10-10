"""Audit log: every MCP tool call is appended to logs/tool_calls.jsonl; agent decisions go to logs/agent_events.jsonl.

A tool-call record has: run_id, timestamp (UTC), server, tool, inputs, output_summary, output (the full result, cut to
`max_chars` with output_truncated=true), duration_ms and error. Hard rule 9: log every tool call.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TOOL_CALLS_FILE = "tool_calls.jsonl"
EVENTS_FILE = "agent_events.jsonl"
MS_PER_S = 1000
SUMMARY_WARNINGS = 3          # warnings kept in a one-line output summary


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def summarise_output(output: Any) -> dict[str, Any]:
    """One-line description of a tool result: tools return {result, evidence, params_used, warnings}."""
    if isinstance(output, dict) and {"result", "evidence"} <= set(output):
        res = output.get("result")
        res = res if isinstance(res, dict) else {}
        summary: dict[str, Any] = {"evidence_count": len(output.get("evidence") or []),
                                   "warnings": (output.get("warnings") or [])[:SUMMARY_WARNINGS]}
        if "error" in res:
            summary["error"] = res["error"]
        if "flags" in res:
            summary["flags"] = len(res["flags"])
        for key in ("flagged", "insufficient_comparables", "explanation", "total_matches", "total_clusters"):
            if key in res:
                summary[key] = res[key]
        return summary
    text = output if isinstance(output, str) else json.dumps(output, default=str)
    return {"chars": len(text)}


class AuditLog:
    """Thread-safe JSONL writer bound to one run."""

    def __init__(self, run_id: str, log_dir: Path, max_chars: int,
                 echo: Callable[[str, dict[str, Any]], None] | None = None) -> None:
        self.run_id = run_id
        self.dir = Path(log_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_chars = int(max_chars)
        self.echo = echo
        self._lock = threading.Lock()

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock, (self.dir / filename).open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def call(self, *, server: str, tool: str, inputs: dict[str, Any], output: Any, duration_s: float,
             error: str | None = None, step: dict[str, Any] | None = None) -> dict[str, Any]:
        """Record one tool call (success or failure)."""
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False, default=str)
        record = {"run_id": self.run_id, "timestamp": now_iso(), "server": server, "tool": tool, "inputs": inputs,
                  "output_summary": summarise_output(output) if output is not None else None,
                  "output": text[:self.max_chars] if output is not None else None,
                  "output_truncated": output is not None and len(text) > self.max_chars,
                  "duration_ms": round(duration_s * MS_PER_S, 1), "error": error, "step": step}
        self._append(TOOL_CALLS_FILE, record)
        if self.echo:
            self.echo("tool_call", record)
        return record

    def event(self, type_: str, **data: Any) -> dict[str, Any]:
        """Record an agent decision: plan, follow_up, recovery, correction, step_cap, citation_guard, ..."""
        record = {"run_id": self.run_id, "timestamp": now_iso(), "type": type_, **data}
        self._append(EVENTS_FILE, record)
        if self.echo:
            self.echo(type_, record)
        return record
