"""LLM usage ledger: tokens, estimated cost and an ESTIMATE of the remaining daily quota.

Every LLM call appends one JSON line to logs/llm_usage.jsonl. Groq's free-tier daily token limit behaves like a rolling
24-hour window (observed), and the provider does not report the remaining quota on a normal response, so the remaining
quota is estimated from this ledger and corrected whenever the provider states its own figures (in a 429 message:
"Limit 200000, Used 196990"). The estimate only knows about usage recorded here: anything spent outside this project
(or before the ledger existed) is invisible to it, so the provider's own number always wins.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

SECONDS_PER_HOUR = 3600
TOKENS_PER_MTOK = 1_000_000
QUOTA_REPORT = re.compile(r"Limit (\d+), Used (\d+), Requested (\d+)")


def price_usd(rules: dict[str, Any], provider: str, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """Estimated cost of one call: 0 for a local Ollama model; the pay-as-you-go equivalent for Groq; None if no price is known."""
    if provider == "ollama":
        return 0.0
    price = (rules["agent"]["pricing"].get(provider) or {}).get(model)
    if not price:
        return None
    return (prompt_tokens * price["input_per_mtok"] + completion_tokens * price["output_per_mtok"]) / TOKENS_PER_MTOK


def parse_quota_report(message: str) -> dict[str, int] | None:
    """The provider's own figures from a 429 message, or None."""
    m = QUOTA_REPORT.search(message)
    return {"limit": int(m.group(1)), "used": int(m.group(2)), "requested": int(m.group(3))} if m else None


class UsageLedger:
    """Append-only JSONL ledger; reads are cheap because the file stays small (one line per LLM call)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _append(self, record: dict[str, Any]) -> None:
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue                                          # a torn line must not break accounting
        return out

    def record_call(self, *, run_id: str | None, provider: str, model: str, kind: str, prompt_tokens: int,
                    completion_tokens: int, seconds: float, cost_usd: float | None, now: float | None = None) -> None:
        self._append({"type": "call", "ts": now or time.time(), "run_id": run_id, "provider": provider, "model": model,
                      "kind": kind, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens, "seconds": round(seconds, 2), "cost_usd": cost_usd})

    def record_quota_report(self, *, provider: str, model: str, limit: int, used: int, now: float | None = None) -> None:
        self._append({"type": "quota_report", "ts": now or time.time(), "provider": provider, "model": model,
                      "limit": limit, "used": used})

    def used_in_window(self, provider: str, model: str, hours: float, now: float | None = None) -> int:
        """Estimated tokens used in the last `hours`: the latest provider report in the window plus calls recorded after it,
        else the sum of recorded calls in the window."""
        now = now or time.time()
        start = now - hours * SECONDS_PER_HOUR
        recs = [r for r in self._read() if r["provider"] == provider and r["model"] == model and r["ts"] >= start]
        reports = [r for r in recs if r["type"] == "quota_report"]
        calls = [r for r in recs if r["type"] == "call"]
        if reports:
            last = max(reports, key=lambda r: r["ts"])
            return last["used"] + sum(c["total_tokens"] for c in calls if c["ts"] > last["ts"])
        return sum(c["total_tokens"] for c in calls)

    def remaining(self, rules: dict[str, Any], provider: str, model: str, now: float | None = None) -> int | None:
        """Estimated tokens left in today's quota for a model, or None when its limit is unknown (e.g. Ollama)."""
        limit = rules["agent"]["groq_daily_token_limits"].get(model) if provider == "groq" else None
        if limit is None:
            return None
        used = self.used_in_window(provider, model, float(rules["agent"]["quota_window_hours"]), now)
        return max(0, int(limit) - used)

    def run_totals(self, run_id: str) -> dict[str, Any]:
        calls = [r for r in self._read() if r["type"] == "call" and r["run_id"] == run_id]
        costs = [c["cost_usd"] for c in calls if c["cost_usd"] is not None]
        return {"llm_calls": len(calls), "prompt_tokens": sum(c["prompt_tokens"] for c in calls),
                "completion_tokens": sum(c["completion_tokens"] for c in calls),
                "total_tokens": sum(c["total_tokens"] for c in calls), "cost_usd": round(sum(costs), 6) if costs else None,
                "models": sorted({f"{c['provider']}:{c['model']}" for c in calls})}
