"""One agent session: the MCP servers, the rules, the audit log, the LLM chain with its usage ledger, and the SQLite
checkpointer, opened together and closed together. `agent.run` starts a run in a session; `agent.review` reopens a
session with the SAME run id to resume the paused run."""
from __future__ import annotations

import os
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from agent.audit import AuditLog
from agent.graph import saved_state, sqlite_checkpointer
from agent.llm import create_llm
from agent.mcp_client import McpBackend
from agent.nodes import AgentContext
from agent.settings import ROOT, Settings
from agent.usage import UsageLedger

LEDGER_FILE = "llm_usage.jsonl"
CHECKPOINT_ENV = "ZABUNI_CHECKPOINT_DB"


@dataclass
class Session:
    settings: Settings
    backend: McpBackend
    rules: dict[str, Any]
    audit: AuditLog
    llm: Any
    ledger: UsageLedger
    ctx: AgentContext
    checkpointer: Any


def checkpoint_path(rules: dict[str, Any]) -> Path:
    """Where paused runs are saved (rules agent.checkpoint_db, or the ZABUNI_CHECKPOINT_DB override used by tests)."""
    return Path(os.environ.get(CHECKPOINT_ENV) or ROOT / rules["agent"]["checkpoint_db"])


@asynccontextmanager
async def open_session(settings: Settings, run_id: str, echo: Callable[[str, dict[str, Any]], None] | None = None,
                        max_steps: int | None = None, use_filesystem: bool = True, resume: bool = False,
                        extra_env: dict[str, str] | None = None) -> AsyncIterator[Session]:
    """Open everything a run needs. The Groq key stays in this process: only ZABUNI_* variables reach the MCP server."""
    async with AsyncExitStack() as stack:
        backend = await stack.enter_async_context(McpBackend(settings, use_filesystem=use_filesystem, extra_env=extra_env))
        rules = await backend.read_resource_json("rules://ppada")
        if max_steps:
            rules["agent"]["max_steps"] = max_steps
        backend.reconnects = int(rules["agent"]["mcp_reconnects"])
        audit = AuditLog(run_id, settings.logs_dir, rules["agent"]["audit_output_max_chars"], echo=echo)
        backend.audit, backend.tool_timeout_s = audit, rules["agent"]["tool_timeout_s"]
        ledger = UsageLedger(settings.logs_dir / LEDGER_FILE)
        llm = create_llm(settings, rules, ledger)
        llm.attach(audit, ledger, run_id)
        tools = await backend.tools()
        checkpointer = await stack.enter_async_context(sqlite_checkpointer(checkpoint_path(rules)))
        ctx = AgentContext(backend=backend, llm=llm, audit=audit, rules=rules, tools=tools)
        if resume:                      # the budget belongs to the RUN: carry its earlier tool calls and tokens into this session
            prior = await saved_state(ctx, checkpointer) or {}
            ctx.calls = int(prior.get("steps_done") or 0)
            ctx.token_base = int(ledger.run_totals(run_id)["total_tokens"])
        yield Session(settings, backend, rules, audit, llm, ledger, ctx, checkpointer)


def log_run_usage(s: Session, seconds: float) -> dict[str, Any]:
    """Write the RUN's tokens, estimated cost and the estimated remaining daily quota to the audit log; return them.

    Totals come from the usage ledger by run id, so a run that was paused and resumed in another process reports both
    phases together (`seconds` is this session's only)."""
    provider, _, model = s.llm.name.partition(":")
    totals = s.ledger.run_totals(s.audit.run_id)
    usage = {"model": s.llm.name, "llm_calls": totals["llm_calls"], "prompt_tokens": totals["prompt_tokens"],
             "completion_tokens": totals["completion_tokens"], "tokens": totals["total_tokens"],
             "cost_usd": round(totals["cost_usd"] or 0.0, 6), "tool_calls": s.ctx.calls, "seconds": round(seconds, 1),
             "remaining_daily_quota_estimate": s.ledger.remaining(s.rules, provider, model)}
    s.audit.event("run_usage", **usage)
    return usage
