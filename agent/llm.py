"""LLM provider switch (Ollama local / Groq hosted), usage accounting, quota handling and a model fallback chain.

Open-weights models only (hard rule 2): the factory refuses a model whose name is not in a known open-weights family
(rules: agent.open_weights_families).

Defaults and why (the agent needs *reliable structured output and tool-argument generation*, not chat quality):
* Development, evals and repeat runs: Ollama `qwen2.5:7b` (local, free, no quota). Qwen 2.5 7B Instruct is one of the two
  models named in the brief and produced a valid, correctly scoped plan on the real task (docs/sample_run/model_check.md).
  On this project's CPU/iGPU machine it generates about 7 tokens/s, so prompts are kept short.
* Hosted demo, demo video and the final sample run: Groq `qwen/qwen3.8-27b`, chosen by measurement (8/8 on the planning task;
  `openai/gpt-oss-120b` 6/8; `openai/gpt-oss-20b` 0/8). The key used to build this project cannot call any Llama chat model.
* Free-tier limits are real: Groq allowed 200,000 tokens/day (a rolling window) and 1,000 output tokens/minute for the Qwen
  model. So each call is recorded in a ledger (tokens, estimated cost, estimated remaining quota); a 429 first waits
  as the provider asks, and when a model's quota is exhausted the chain falls back to a second open-weights model on Groq
  (its quota is separate) and, when that is exhausted too, stops cleanly with a clear message.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.settings import Settings
from agent.usage import UsageLedger, parse_quota_report, price_usd

T = TypeVar("T", bound=BaseModel)
RATE_LIMIT_MARKERS = ("ratelimit", "rate_limit", "429", "too many requests")
DAILY_LIMIT_MARKERS = ("tokens per day", "(tpd)", "requests per day", "(rpd)")   # a wait of minutes/hours: do not retry
RETRY_AFTER = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)s")
PERMANENT_MARKERS = ("notfounderror", "does not exist", "authenticationerror", "permissiondenied", "invalid api key",
                     "401", "403", "404")        # retrying cannot fix these: fail at once with a useful message
ORG_ID = re.compile(r"org_\w+")


class LLMError(RuntimeError):
    """The model could not produce valid output after the allowed retries."""


class LLMQuotaError(LLMError):
    """The provider's rate limit or quota stopped this model. Waiting is pointless; another model may still work."""


class LLMQuotaExhausted(LLMQuotaError):
    """Every configured model is out of quota: the run must stop cleanly."""


def redact(text: str) -> str:
    """Remove the provider's organisation id from an error message."""
    return ORG_ID.sub("org_<hidden>", text)


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
    """One model: every agent node asks for a pydantic schema and gets a validated object back."""

    def __init__(self, chat: Any, name: str, max_retries: int, backoff_base_s: float, max_wait_s: float = 90.0, *,
                 rules: dict[str, Any] | None = None, ledger: UsageLedger | None = None) -> None:
        self.chat, self.name = chat, name
        self.provider, _, self.model = name.partition(":")
        self.max_retries, self.backoff_base_s, self.max_wait_s = int(max_retries), float(backoff_base_s), float(max_wait_s)
        self.rules, self.ledger = rules, ledger
        self.audit: Any = None
        self.run_id: str | None = None
        self.calls = self.tokens = self.prompt_tokens = self.completion_tokens = 0
        self.cost_usd = 0.0
        self.latency_s = 0.0

    def attach(self, audit: Any = None, ledger: UsageLedger | None = None, run_id: str | None = None) -> None:
        """Connect the run's audit log and the usage ledger (the CLI does this once the run id exists)."""
        self.audit, self.run_id = audit or self.audit, run_id or self.run_id
        self.ledger = ledger or self.ledger

    # ----------------------------------------------------------------- accounting
    def _record(self, kind: str, out: dict[str, Any], seconds: float) -> None:
        usage = getattr(out.get("raw"), "usage_metadata", None) or {}
        prompt, completion = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
        total = int(usage.get("total_tokens") or prompt + completion)
        if total and not (prompt or completion):
            prompt = total
        cost = price_usd(self.rules, self.provider, self.model, prompt, completion) if self.rules else None
        self.calls += 1
        self.tokens, self.prompt_tokens, self.completion_tokens = self.tokens + total, self.prompt_tokens + prompt, self.completion_tokens + completion
        self.cost_usd += cost or 0.0
        remaining = None
        if self.ledger is not None:
            self.ledger.record_call(run_id=self.run_id, provider=self.provider, model=self.model, kind=kind,
                                    prompt_tokens=prompt, completion_tokens=completion, seconds=seconds, cost_usd=cost)
            remaining = self.ledger.remaining(self.rules, self.provider, self.model) if self.rules else None
        if self.audit is not None:
            self.audit.event("llm_call", kind=kind, model=self.name, prompt_tokens=prompt, completion_tokens=completion,
                             total_tokens=total, cost_usd=cost, seconds=round(seconds, 2),
                             est_remaining_daily_tokens=remaining)

    def _note_quota(self, message: str) -> None:
        """Remember the provider's own quota figures when a 429 states them."""
        report = parse_quota_report(message)
        if report is None:
            return
        if self.ledger is not None:
            self.ledger.record_quota_report(provider=self.provider, model=self.model, limit=report["limit"], used=report["used"])
        if self.audit is not None:
            self.audit.event("quota_report", model=self.name, **report, source="provider 429 message")

    # ----------------------------------------------------------------- the call
    async def structured(self, schema: type[T], system: str, user: str, kind: str = "llm") -> T:
        """Ask for output matching `schema`. Retries on rate limits (honouring the provider's wait hint) and on invalid or
        missing output (feeding the validation error back). Raises LLMQuotaError when the model's quota or rate limit
        stops it, LLMError for anything else."""
        runnable = self.chat.with_structured_output(schema, include_raw=True)
        messages: list[Any] = [SystemMessage(content=system), HumanMessage(content=user)]
        last, rate_limited = "no attempt made", False
        for attempt in range(self.max_retries + 1):
            started = time.time()
            try:
                out = await runnable.ainvoke(messages)
            except Exception as exc:  # noqa: BLE001 - provider SDKs raise many types; classify by text
                last = f"{type(exc).__name__}: {exc}"
                low = last.lower()
                if any(m in low for m in RATE_LIMIT_MARKERS):
                    rate_limited = True
                    self._note_quota(last)
                    hint = retry_after_seconds(last)
                    if any(m in low for m in DAILY_LIMIT_MARKERS) or (hint is not None and hint > self.max_wait_s):
                        raise LLMQuotaError(f"{self.name}: provider quota exhausted ({redact(last)[:260]}). "
                                            "Waiting would take too long.") from exc
                    if attempt < self.max_retries:
                        await asyncio.sleep(max(hint + 0.5, self.backoff_base_s) if hint is not None else self.backoff_base_s * 2 ** attempt)
                    continue
                if any(m in low for m in PERMANENT_MARKERS):
                    raise LLMError(f"{self.name}: {redact(last)[:300]}. Check MODEL / the API key: "
                                   "`python -m agent.models` lists the models this key can use.") from exc
                rate_limited = False
                messages.append(HumanMessage(content=f"Your previous reply was rejected by the API ({last[:300]}). "
                                                     "Reply again with valid output only."))
                continue
            finally:
                self.latency_s += time.time() - started
            rate_limited = False
            self._record(kind, out, time.time() - started)
            parsed = out.get("parsed")
            if parsed is not None:
                return parsed
            last = f"invalid output: {out.get('parsing_error')}"
            messages.append(HumanMessage(content=f"Your previous reply was not valid ({str(last)[:300]}). "
                                                 "Reply again with output that matches the schema exactly."))
        if rate_limited:
            raise LLMQuotaError(f"{self.name}: still rate limited after {self.max_retries + 1} attempts ({redact(last)[:200]}).")
        raise LLMError(f"LLM failed after {self.max_retries + 1} attempts ({self.name}): {redact(last)}")


class FallbackLLM:
    """A chain of models. On a quota/rate-limit error the next model takes over for the rest of the run (sticky); when the
    last one is out of quota, LLMQuotaExhausted tells the agent to stop cleanly. Same interface as ChatLLM."""

    def __init__(self, chain: list[ChatLLM]) -> None:
        if not chain:
            raise ValueError("a fallback chain needs at least one model")
        self.chain, self.index = chain, 0

    @property
    def current(self) -> ChatLLM:
        return self.chain[min(self.index, len(self.chain) - 1)]

    @property
    def name(self) -> str:
        return self.current.name

    @property
    def names(self) -> list[str]:
        return [m.name for m in self.chain]

    def attach(self, audit: Any = None, ledger: UsageLedger | None = None, run_id: str | None = None) -> None:
        for m in self.chain:
            m.attach(audit, ledger, run_id)

    def _sum(self, attr: str) -> Any:
        return sum(getattr(m, attr) for m in self.chain)

    calls = property(lambda self: self._sum("calls"))
    tokens = property(lambda self: self._sum("tokens"))
    prompt_tokens = property(lambda self: self._sum("prompt_tokens"))
    completion_tokens = property(lambda self: self._sum("completion_tokens"))
    cost_usd = property(lambda self: self._sum("cost_usd"))
    latency_s = property(lambda self: self._sum("latency_s"))

    async def structured(self, schema: type[T], system: str, user: str, kind: str = "llm") -> T:
        reasons: list[str] = []
        while self.index < len(self.chain):
            model = self.chain[self.index]
            try:
                return await model.structured(schema, system, user, kind)
            except LLMQuotaError as exc:
                reasons.append(f"{model.name}: {exc}")
                self.index += 1
                if model.audit is not None:
                    nxt = self.chain[self.index].name if self.index < len(self.chain) else None
                    model.audit.event("llm_fallback", from_model=model.name, to_model=nxt, reason=redact(str(exc))[:300])
        raise LLMQuotaExhausted("Every configured model is out of quota or rate limited: " + " | ".join(reasons)
                                + " Retry after the quota resets, or switch provider (LLM_PROVIDER=ollama runs locally).")


def build_chat(provider: str, model: str, settings: Settings, agent_cfg: dict[str, Any]) -> Any:
    """The LangChain chat model for one (provider, model)."""
    if provider == "groq":
        from langchain_groq import ChatGroq
        return ChatGroq(model=model, api_key=settings.groq_api_key, temperature=agent_cfg["llm_temperature"],
                        max_retries=0, timeout=agent_cfg["llm_timeout_s"], max_tokens=agent_cfg["llm_max_tokens"])
    from langchain_ollama import ChatOllama
    return ChatOllama(model=model, base_url=settings.ollama_base_url, temperature=agent_cfg["llm_temperature"],
                      num_predict=agent_cfg["llm_max_tokens"], num_ctx=agent_cfg["ollama_num_ctx"],
                      keep_alive=agent_cfg["ollama_keep_alive"])


def create_llm(settings: Settings, rules: dict[str, Any], ledger: UsageLedger | None = None) -> FallbackLLM:
    """The configured model plus its open-weights fallbacks (Groq only; a local model has no quota to fall back from)."""
    agent_cfg = rules["agent"]
    models = [settings.model] + ([m for m in agent_cfg["llm_fallback_models"].get(settings.provider, []) if m != settings.model]
                                 if settings.provider == "groq" else [])
    chain = []
    for m in models:
        assert_open_weights(m, agent_cfg["open_weights_families"])
        chain.append(ChatLLM(build_chat(settings.provider, m, settings, agent_cfg), f"{settings.provider}:{m}",
                             agent_cfg["llm_max_retries"], agent_cfg["llm_backoff_base_s"], agent_cfg["llm_max_wait_s"],
                             rules=rules, ledger=ledger))
    return FallbackLLM(chain)
