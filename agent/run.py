"""Command-line entry point:  python -m agent.run "Investigate Makueni County Government 2022-2024"

Prints the plan, every tool call, follow-ups / recoveries / corrections and the flags created, then saves a draft
summary to outputs/ through the Filesystem MCP server. Tool calls are logged to logs/tool_calls.jsonl.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
import time
from typing import Any

from agent.audit import AuditLog
from agent.graph import new_run_id, run_agent
from agent.llm import create_llm
from agent.mcp_client import McpBackend, ToolCallFailed
from agent.nodes import AgentContext
from agent.report import build_summary_md
from agent.settings import DEFAULT_MODELS, SettingsError, load_settings

ARGS_WIDTH = 110


def _short(obj: Any, width: int = ARGS_WIDTH) -> str:
    text = json.dumps(obj, ensure_ascii=False, default=str)
    return text if len(text) <= width else text[: width - 3] + "..."


class Printer:
    """Live console output for audit events and tool calls."""

    def echo(self, kind: str, rec: dict[str, Any]) -> None:
        if kind == "tool_call":
            s = rec["output_summary"] or {}
            tail = f"error: {rec['error']}" if rec["error"] else \
                f"{s.get('evidence_count', s.get('chars', '?'))} evidence, flags={s.get('flags', '-')}"
            print(f"  -> {rec['tool']} {_short(rec['inputs'])}  [{rec['duration_ms']:.0f} ms] {tail}", flush=True)
        elif kind == "plan":
            print(f"\nPLAN: {rec['objective']}", flush=True)
            print(f"  tools available to the planner (discovered via list_tools): {', '.join(rec['available_tools'])}")
            for i, s in enumerate(rec["steps"], 1):
                print(f"  {i}. {s['tool']} {_short(s['args'])}\n     why: {s['reason']}", flush=True)
            print("\nINVESTIGATION", flush=True)
        elif kind == "follow_up":
            print(f"  + FOLLOW-UP after {rec['trigger']}: {rec['tool']} {_short(rec['args'])}  ({rec['reason']})", flush=True)
        elif kind == "recovery":
            print(f"  ~ RECOVERY {rec['tool']}: widened ({rec['op']['op']} {rec['op']['arg']}); comparables "
                  f"{rec['comparables_before']} -> {rec['comparables_after']}", flush=True)
        elif kind == "correction":
            print(f"  ! CORRECTION {rec['tool']}: {rec['error'][:100]} -> retry with {_short(rec['new_args'])}", flush=True)
        elif kind == "step_cap":
            print(f"  # STEP CAP reached ({rec['max_steps']}): {len(rec['skipped_steps'])} planned step(s) skipped", flush=True)
        elif kind == "citation_guard":
            print(f"  x CITATION GUARD dropped a finding: {rec['reason']} {rec.get('ocids') or ''}", flush=True)
        elif kind == "scope_injected":
            print(f"  (scope added to the {rec['tool']} step: {', '.join(rec['injected'])})", flush=True)
        elif kind in ("synth_fallback", "plan_rejected_steps"):
            print(f"  ({kind}: {rec})", flush=True)


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.run", description="Zabuni AI: investigate a procurement request.")
    ap.add_argument("query", help='e.g. "Investigate Makueni County Government 2022-2024"')
    ap.add_argument("--provider", choices=["groq", "ollama"], help="override LLM_PROVIDER")
    ap.add_argument("--model", help="override MODEL")
    ap.add_argument("--max-steps", type=int, help="override the step cap (rules agent.max_steps)")
    ap.add_argument("--no-save", action="store_true", help="do not write the draft summary to outputs/")
    args = ap.parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if args.provider or args.model:
        provider = args.provider or settings.provider
        model = args.model or (DEFAULT_MODELS[provider] if provider != settings.provider else settings.model)
        settings = dataclasses.replace(settings, provider=provider, model=model)
    started = time.time()
    run_id = new_run_id()
    printer = Printer()
    async with McpBackend(settings) as backend:
        rules = await backend.read_resource_json("rules://ppada")
        if args.max_steps:
            rules["agent"]["max_steps"] = args.max_steps
        audit = AuditLog(run_id, settings.logs_dir, rules["agent"]["audit_output_max_chars"], echo=printer.echo)
        backend.audit, backend.tool_timeout_s = audit, rules["agent"]["tool_timeout_s"]
        llm = create_llm(settings, rules)
        tools = await backend.tools()
        ctx = AgentContext(backend=backend, llm=llm, audit=audit, rules=rules, tools=tools)
        print(f"Run {run_id} | model {llm.name} | servers {backend.servers} | step cap {rules['agent']['max_steps']}")
        print(f"Request: {args.query}")
        state = await run_agent(ctx, args.query)
        run = {"model": llm.name, "llm_calls": llm.calls, "tokens": llm.tokens, "tool_calls": ctx.calls,
               "seconds": time.time() - started}
        print("\nRESULT")
        print(f"  summary: {state.get('summary')}")
        if state.get("stopped_reason"):
            print(f"  stopped early: {state['stopped_reason']}")
        for f in state.get("flags_created", []):
            new = "created" if f["created"] else "already existed"
            print(f"  FLAG {f['flag_id']} [{f['severity']}] {f['rule']} ({new}, {f['status']}): {f['explanation'][:140]}")
        if not state.get("flags_created"):
            print("  no flags created")
        print(f"  review status: {state.get('review_status')}  (Day 4 adds the human approval gate)")
        print(f"  usage: {run['llm_calls']} LLM calls, {run['tokens']} tokens, {run['tool_calls']} tool calls, "
              f"{run['seconds']:.0f} s | audit log: {settings.logs_dir / 'tool_calls.jsonl'}")
        if not args.no_save:
            md = build_summary_md(state, run, int(rules["agent"]["report_awards_listed"]))
            try:
                path = await backend.save_text(f"summary_{run_id}.md", md, rules["agent"]["filesystem_write_tool_hint"])
                print(f"  draft saved via the Filesystem MCP server: {path}")
            except ToolCallFailed as exc:
                print(f"  could not save the draft: {exc}", file=sys.stderr)
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
