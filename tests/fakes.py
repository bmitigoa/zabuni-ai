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
        self.prompt_tokens = self.completion_tokens = 0
        self.cost_usd = 0.0

    names = ["fake:deterministic"]

    def attach(self, audit=None, ledger=None, run_id=None) -> None:
        pass

    async def structured(self, schema: type[BaseModel], system: str, user: str, kind: str = "llm") -> BaseModel:
        self.calls += 1
        self.prompts.setdefault(schema.__name__, []).append((system, user))
        if schema.__name__ == "ToolChoice" and schema not in self.script:      # default: choose every tool listed in the index
            import re
            plan = self.script.get(next((k for k in self.script if k.__name__ == "Plan"), None))
            plan = plan[0] if isinstance(plan, list) and plan else plan
            scope = plan.scope if hasattr(plan, "scope") else None                    # the scripted plan's scope, as a cooperative model would report it
            return schema(tools=re.findall(r"^- (\w+):", user, re.M), **({"scope": scope} if scope is not None else {}))
        entry = self.script[schema]
        if isinstance(entry, list):
            entry = entry.pop(0) if len(entry) > 1 else entry[0]
        return entry(system, user) if callable(entry) else entry
