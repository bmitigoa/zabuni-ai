"""Hosted-demo guard: limits live runs per visitor per hour and refuses politely when the free-tier daily quota is used.

The Streamlit app (Day 5) calls `DemoGuard.check(visitor_id)` before starting a run and shows `Decision.message` instead of
crashing. State is in memory (one process per Space); a visitor id is an opaque random session id, never a person's data.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

from agent.usage import UsageLedger

SECONDS_PER_HOUR = 3600


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str | None       # "rate" | "quota" | None
    message: str | None      # friendly text for the visitor


class DemoGuard:
    def __init__(self, rules: dict[str, Any], ledger: UsageLedger, provider: str, model: str,
                 clock: Callable[[], float] = time.time) -> None:
        self.cfg, self.rules, self.ledger = rules["demo"], rules, ledger
        self.provider, self.model, self.clock = provider, model, clock
        self._runs: dict[str, deque[float]] = defaultdict(deque)

    def check(self, visitor_id: str) -> Decision:
        """Allow the run (and count it) or say why not. A local Ollama model has no daily quota to run out of."""
        now = self.clock()
        recent = self._runs[visitor_id]
        while recent and now - recent[0] >= SECONDS_PER_HOUR:
            recent.popleft()
        if len(recent) >= int(self.cfg["runs_per_visitor_per_hour"]):
            return Decision(False, "rate", self.cfg["rate_message"])
        left = self.ledger.remaining(self.rules, self.provider, self.model, now)
        if left is not None and left < int(self.cfg["expected_run_tokens"]):
            return Decision(False, "quota", self.cfg["quota_message"])
        recent.append(now)
        return Decision(True, None, None)
