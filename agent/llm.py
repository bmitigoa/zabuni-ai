"""LLM provider switch (Groq hosted / Ollama local) and one robust `structured()` call used by every agent node.

Open-weights models only (hard rule 2). The factory refuses a model whose name is not in a known open-weights family
(rules: agent.open_weights_families), so a proprietary model cannot be configured by accident.

Why these defaults (the agent needs *reliable structured output and tool-argument generation*, not chat quality):
* Groq: `qwen/qwen3.8-27b` (Alibaba's open-weights Qwen). Chosen by measurement, not reputation: on this project's own
  planning task (real prompt, real tool catalogue, one attempt, 8 runs) it scored 8/8 on every check, versus 6/8 for
  `openai/gpt-oss-120b` (tool-call JSON parse failures) and 0/8 for `openai/gpt-oss-20b` (tool-name format the
  LangChain tool path rejects); see docs/sample_run/model_check.md and `python -m evals.model_check`. Groq's docs list
  `llama-3.3-70b-versatile` with tool use, but the key used to build this project cannot call it (HTTP 404: no Llama
  chat model is enabled for the account), so Llama could not be compared. The sample is small: treat it as evidence
  for the default, not as a benchmark. Select another model with MODEL=...
* Free-tier limits matter: Groq's on-demand tier allows 1,000 output tokens per minute for this model, so replies are
  capped (rules agent.llm_max_tokens) and rate-limit retries back off long enough to span a one-minute window.
* Ollama fallback: `qwen2.5:7b`. Qwen 2.5 7B Instruct is trained for tool/JSON use and is the strongest of the two
  models named in the brief (Llama 3.1 8B, Qwen 2.5 7B) at that size; it runs on a laptop. NOT tested on this machine:
  Ollama is not installed here.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.settings import Settings

T = TypeVar("T", bound=BaseModel)
RATE_LIMIT_MARKERS = ("ratelimit", "rate_limit", "429", "too many requests")
DAILY_LIMIT_MARKERS = ("tokens per day", "(tpd)", "requests per day", "(rpd)")   # a wait of minutes/hours: do not retry
RETRY_AFTER = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s")
PERMANENT_MARKERS = ("notfounderror", "does not exist", "authenticationerror", "permissiondenied", "invalid api key",
                     "401", "403", "404")        # retrying cannot fix these: fail at once with a useful message


class LLMError(RuntimeError):
    """The model could not produce valid output after the allowed retries."""


def retry_after_seconds(text: str) -> float | None:
    """Seconds the provider asked us to wait ("Please try again in 1m7.5s"), or None if it did not say."""
    m = RETRY_AFTER.search(text)
    if not m:
        return None
    hours, minutes, seconds = (float(x) if x else 0.0 for x in m.groups())
    return hours * 3600 + minutes * 60 + seconds


def assert_open_weights(model: str, families: list[str]) -> None:
    """Refuse a model whose name does not contain a known open-weights family name."""
    if not any(f in model.lower() for f in families):
        raise LLMError(f"Model {model!r} is not in a known open-weights family {families}. Hard rule 2: open-weights "
                       "models only. Add the family to rules agent.open_weights_families if it truly is open-weights.")


class ChatLLM:
    """Wraps a LangChain chat model; every agent node asks for a pydantic schema and gets a validated object back."""

    def __init__(self, chat: Any, name: str, max_retries: int, backoff_base_s: float, max_wait_s: float = 90.0) -> None:
        self.chat, self.name = chat, name
        self.max_retries, self.backoff_base_s, self.max_wait_s = int(max_retries), float(backoff_base_s), float(max_wait_s)
        self.calls = self.tokens = 0
        self.latency_s = 0.0

    async def structured(self, schema: type[T], system: str, user: str) -> T:
        """Ask for output matching `schema`. Retries on rate limits (with backoff) and on invalid/missing output
        (feeding the validation error back to the model). Raises LLMError when retries run out."""
        runnable = self.chat.with_structured_output(schema, include_raw=True)
        messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=user)]
        last = "no attempt made"
        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                out = await runnable.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise many types; classify by text
                last = f"{type(exc).__name__}: {exc}"
                low = last.lower()
                if any(m in low for m in RATE_LIMIT_MARKERS):
                    hint = retry_after_seconds(last)
                    if any(m in low for m in DAILY_LIMIT_MARKERS) or (hint is not None and hint > self.max_wait_s):
                        raise LLMError(f"{self.name}: provider quota exhausted ({re.sub(r'org_\w+', 'org_<hidden>', last)[:260]}). "
                                       "Waiting would take too long: retry later or use another model/provider.") from exc
                    await asyncio.sleep(max(hint + 0.5, self.backoff_base_s) if hint is not None else self.backoff_base_s * 2 ** attempt)
                    continue
                if any(m in low for m in PERMANENT_MARKERS):
                    raise LLMError(f"{self.name}: {last[:300]}. Check MODEL / the API key: "
                                   "`python -m agent.models` lists the models this key can use.") from exc
                messages.append(HumanMessage(content=f"Your previous reply was rejected by the API ({last[:300]}). "
                                                     "Reply again with valid output only."))
                continue
            finally:
                self.latency_s += time.time() - started
            self.calls += 1
            usage = getattr(out.get("raw"), "usage_metadata", None) or {}
            self.tokens += int(usage.get("total_tokens") or 0)
            parsed = out.get("parsed")
            if parsed is not None:
                return parsed
            last = f"invalid output: {out.get('parsing_error')}"
            messages.append(HumanMessage(content=f"Your previous reply was not valid ({str(last)[:300]}). "
                                                 "Reply again with output that matches the schema exactly."))
        raise LLMError(f"LLM failed after {self.max_retries + 1} attempts ({self.name}): {last}")


def create_llm(settings: Settings, rules: dict[str, Any]) -> ChatLLM:
    """Build the configured provider's chat model, wrapped for structured output."""
    agent_cfg = rules["agent"]
    assert_open_weights(settings.model, agent_cfg["open_weights_families"])
    if settings.provider == "groq":
        from langchain_groq import ChatGroq
        chat = ChatGroq(model=settings.model, api_key=settings.groq_api_key, temperature=agent_cfg["llm_temperature"],
                        max_retries=0, timeout=agent_cfg["llm_timeout_s"], max_tokens=agent_cfg["llm_max_tokens"])
    else:
        from langchain_ollama import ChatOllama
        chat = ChatOllama(model=settings.model, base_url=settings.ollama_base_url,
                          temperature=agent_cfg["llm_temperature"], num_predict=agent_cfg["llm_max_tokens"])
    return ChatLLM(chat, f"{settings.provider}:{settings.model}", agent_cfg["llm_max_retries"],
                   agent_cfg["llm_backoff_base_s"], agent_cfg["llm_max_wait_s"])
