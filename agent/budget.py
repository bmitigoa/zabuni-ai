"""Run budget: a cap on tool calls and on LLM tokens per run. The run stops cleanly and the memo says so.

Both limits live in rules (agent.max_steps, agent.max_total_tokens). `synth_reserve_tokens` is kept back while investigating
so that, when the investigation is cut short, the Synthesiser can still word the findings that were found.
Tokens are the sum over all LLM calls of the run (prompt + reply), whatever the provider; a local Ollama model has no
money cost but is still bounded in tokens because it is slow.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Budget:
    """Reads the live counters (tool calls from the agent context, tokens from the LLM) every time it is asked."""
    max_calls: int
    max_tokens: int
    synth_reserve: int
    calls: Any            # callable -> int
    tokens: Any           # callable -> int

    @classmethod
    def from_rules(cls, agent_cfg: dict[str, Any], calls: Any, tokens: Any) -> "Budget":
        return cls(int(agent_cfg["max_steps"]), int(agent_cfg["max_total_tokens"]), int(agent_cfg["synth_reserve_tokens"]),
                   calls, tokens)

    def stop_reason(self, *, keep_reserve: bool = True, count_calls: bool = True) -> str | None:
        """Why the run must stop investigating now, or None. With `keep_reserve` the Synthesiser's tokens are protected.
        The Synthesiser passes `count_calls=False`: storing the flags it accepted must not cancel the summary."""
        calls, tokens = self.calls(), self.tokens()
        if count_calls and calls >= self.max_calls:
            return f"tool-call budget reached ({calls} of {self.max_calls} calls)"
        limit = self.max_tokens - (self.synth_reserve if keep_reserve else 0)
        if tokens >= limit:
            return (f"token budget reached ({tokens} of {self.max_tokens} tokens"
                    + (f", {self.synth_reserve} kept for the summary" if keep_reserve else "") + ")")
        return None

    def memo_notice(self, reason: str | None) -> str | None:
        """A sentence for the memo when the run was cut short (None otherwise)."""
        if not reason:
            return None
        return (f"The run stopped early: {reason}. Checks that had not yet run were not performed, so the absence of a "
                "finding is not evidence that none exists.")
