"""Deterministic stand-ins for the LLM, so agent behaviour (planning, follow-ups, recovery, guard) is tested exactly."""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel

Handler = Callable[[str, str], BaseModel]


class FakeLLM:
    """Looks like agent.llm.ChatLLM. `script` maps a schema class to a handler(system, user) -> instance, or to a list
    of handlers/instances consumed in order (the last one repeats). Every prompt it receives is recorded."""

    name = "fake:deterministic"

    def __init__(self, script: dict[type, Any]) -> None:
        self.script = script
        self.prompts: dict[str, list[tuple[str, str]]] = {}
        self.calls = 0
        self.tokens = 0

    async def structured(self, schema: type[BaseModel], system: str, user: str) -> BaseModel:
        self.calls += 1
        self.prompts.setdefault(schema.__name__, []).append((system, user))
        entry = self.script[schema]
        if isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        return entry(system, user) if callable(entry) else entry
