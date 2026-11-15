"""View the trace file without any tool:   python -m agent.traces [logs/traces.jsonl] [--run RUN_ID] [--last N]

Prints each trace (one per agent session) as a tree: the run span, then its LLM calls and tool calls in time order, with duration, tokens,
estimated cost, model and fallback marks. The file is plain OpenTelemetry JSON, one span per line, so it can also be loaded into any
OTel-compatible viewer (for example by replaying it to an OTLP collector); nothing here needs a server.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

DEFAULT_FILE = Path(__file__).resolve().parent.parent / "logs" / "traces.jsonl"
NS_PER_MS = 1_000_000


def load(path: Path) -> list[dict[str, Any]]:
    """Every span in the file (a line that is not valid JSON is skipped, not fatal)."""
    spans = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            spans.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return spans


def group(spans: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Spans by trace id, in file order."""
    traces: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in spans:
        traces[s["context"]["trace_id"]].append(s)
    return dict(traces)


def duration_ms(span: dict[str, Any]) -> float:
    from datetime import datetime
    f = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp()           # noqa: E731
    return (f(span["end_time"]) - f(span["start_time"])) * 1000


def describe(span: dict[str, Any]) -> str:
    a = span.get("attributes", {})
    bits = [f"{duration_ms(span):,.0f} ms"]
    if "gen_ai.usage.input_tokens" in a:
        bits.append(f"{a['gen_ai.usage.input_tokens']:,} in / {a.get('gen_ai.usage.output_tokens', 0):,} out tokens")
    if a.get("zabuni.cost_usd"):
        bits.append(f"${a['zabuni.cost_usd']:.4f}")
    if a.get("gen_ai.system"):
        bits.append(f"{a['gen_ai.system']}:{a.get('gen_ai.request.model')}")
    if a.get("zabuni.llm.fallback"):
        bits.append("FALLBACK model")
    if "zabuni.tool.evidence_count" in a:
        bits.append(f"{a['zabuni.tool.evidence_count']} evidence")
    if span.get("status", {}).get("status_code") == "ERROR":
        bits.append("ERROR " + str(span["status"].get("description") or ""))
    return ", ".join(bits)


def render(trace_spans: list[dict[str, Any]]) -> list[str]:
    """One trace as indented lines: the root first, children by start time."""
    ids = {s["context"]["span_id"] for s in trace_spans}
    roots = [s for s in trace_spans if s.get("parent_id") not in ids]
    kids: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in trace_spans:
        if s.get("parent_id") in ids:
            kids[s["parent_id"]].append(s)
    lines: list[str] = []

    def walk(span: dict[str, Any], depth: int) -> None:
        a = span.get("attributes", {})
        label = span["name"] + (f"  [{a['zabuni.phase']} {a['zabuni.run_id']}]" if span["name"] == "run" else "")
        lines.append(f"{'  ' * depth}{label}  -  {describe(span)}")
        for ev in span.get("events", []):
            lines.append(f"{'  ' * (depth + 1)}* event {ev['name']} {json.dumps(ev.get('attributes', {}), ensure_ascii=False)}")
        for child in sorted(kids.get(span["context"]["span_id"], []), key=lambda c: c["start_time"]):
            walk(child, depth + 1)
    for root in sorted(roots, key=lambda r: r["start_time"]):
        walk(root, 0)
        a = root.get("attributes", {})
        if root["name"] == "run":
            lines.append(f"  totals: {a.get('zabuni.llm_calls', 0)} LLM calls, {a.get('zabuni.tool_calls', 0)} tool calls, "
                         f"{a.get('gen_ai.usage.input_tokens', 0):,} in / {a.get('gen_ai.usage.output_tokens', 0):,} out tokens, "
                         f"${a.get('zabuni.cost_usd', 0):.4f}, {a.get('zabuni.malformed_attempts', 0)} malformed attempts, {a.get('zabuni.fallbacks', 0)} fallbacks")
    return lines


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default=str(DEFAULT_FILE))
    ap.add_argument("--run", help="only traces of this run id")
    ap.add_argument("--last", type=int, default=0, help="only the last N traces")
    args = ap.parse_args()
    path = Path(args.file)
    if not path.is_file():
        sys.exit(f"No trace file at {path}. Run the agent first (tracing is on by default, rules telemetry.enabled).")
    traces = list(group(load(path)).values())
    if args.run:
        traces = [t for t in traces if any(s.get("attributes", {}).get("zabuni.run_id") == args.run for s in t)]
    if args.last:
        traces = traces[-args.last:]
    for t in traces:
        print("\n".join(render(t)) + "\n")
    print(f"{len(traces)} trace(s) from {path}")


if __name__ == "__main__":
    main()
