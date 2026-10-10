"""One live smoke test against the real LLM (Groq, open-weights). Skipped unless GROQ_API_KEY is configured.

It checks structure only (the run completes, the plan was made from discovered tools, calls were logged), never the
model's wording or conclusions. A provider outage or rate limit skips rather than fails: this is a smoke test, the
deterministic behaviour is covered by tests/test_agent.py.
"""
import asyncio
import os

import pytest

from agent.audit import AuditLog
from agent.graph import run_agent
from agent.llm import LLMError, create_llm
from agent.mcp_client import McpBackend
from agent.nodes import AgentContext
from agent.settings import DEFAULT_MODELS, SettingsError, load_settings

try:
    SETTINGS = load_settings()
except SettingsError:
    SETTINGS = None

pytestmark = pytest.mark.skipif(
    SETTINGS is None or SETTINGS.provider != "groq" or not SETTINGS.groq_api_key or os.environ.get("ZABUNI_SKIP_LIVE") == "1",
    reason="live smoke test needs LLM_PROVIDER=groq and GROQ_API_KEY (set ZABUNI_SKIP_LIVE=1 to skip on purpose)")


def test_live_model_plans_from_discovered_tools_and_every_call_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("ZABUNI_FLAGS_DB", str(tmp_path / "flags.duckdb"))
    import dataclasses
    settings = dataclasses.replace(SETTINGS, model=DEFAULT_MODELS["groq"], logs_dir=tmp_path / "logs", outputs_dir=tmp_path / "o")

    async def go():
        async with McpBackend(settings, use_filesystem=False) as backend:
            rules = await backend.read_resource_json("rules://ppada")
            rules["agent"]["max_steps"] = 4                                  # keep the smoke test small
            audit = AuditLog("run_live", settings.logs_dir, rules["agent"]["audit_output_max_chars"])
            backend.audit = audit
            ctx = AgentContext(backend=backend, llm=create_llm(settings, rules), audit=audit, rules=rules,
                               tools=await backend.tools())
            state = await run_agent(ctx, "Check supplier concentration for Makueni County Government in 2023")
            return state, ctx

    try:
        state, ctx = asyncio.run(go())
    except LLMError as exc:
        pytest.skip(f"live LLM unavailable: {exc}")
    assert state["plan"]["steps"] and all(s["origin"] == "plan" for s in state["plan"]["steps"])
    assert state["results"] and state["review_status"] == "pending_review" and ctx.calls >= 1
    lines = (settings.logs_dir / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == ctx.calls and state["summary"]
