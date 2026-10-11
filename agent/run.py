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

from agent.graph import new_run_id, pause_payload, run_agent
from agent.llm import LLMError
from agent.mcp_client import ToolCallFailed
from agent.report import build_summary_md
from agent.session import log_run_usage, open_session
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
        elif kind == "llm_fallback":
            print(f"  ! MODEL FALLBACK {rec['from_model']} -> {rec['to_model']}: {rec['reason'][:100]}", flush=True)
        elif kind in ("synth_fallback", "plan_rejected_steps", "args_pruned", "budget_stop", "rejected_flag_suppressed", "flags_capped", "scope_request",
                      "mcp_reconnect"):
            print(f"  ({kind}: {rec})", flush=True)


async def amain(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.run", description="Zabuni AI: investigate a procurement request.")
    ap.add_argument("query", help='e.g. "Investigate Makueni County Government 2022-2024"')
    ap.add_argument("--provider", choices=["groq", "ollama"], help="override LLM_PROVIDER")
    ap.add_argument("--model", help="override MODEL")
    ap.add_argument("--max-steps", type=int, help="override the step cap (rules agent.max_steps)")
    ap.add_argument("--buyer", help="scope: buyer name (overrides what the model extracts)")
    ap.add_argument("--supplier", help="scope: supplier name")
    ap.add_argument("--years", help="scope: years, e.g. 2022,2023")
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
    async with open_session(settings, run_id, echo=Printer().echo, max_steps=args.max_steps) as s:
        rules = s.rules
        print(f"Run {run_id} | model chain {s.llm.names} | servers {s.backend.servers} | step cap {rules['agent']['max_steps']}"
              f" | token budget {rules['agent']['max_total_tokens']}")
        print(f"Request: {args.query}")
        try:
            hint = {"buyer": args.buyer, "supplier": args.supplier,
                    "years": [int(y) for y in args.years.split(",") if y.strip()] if args.years else None}
            state = await run_agent(s.ctx, args.query, s.checkpointer, hint)
        except LLMError as exc:                           # a model/provider problem: say what to do, no traceback
            usage = log_run_usage(s, time.time() - started)
            print(f"\nRun failed: {exc}", file=sys.stderr)
            if settings.provider == "ollama" and "Connect" in str(exc):
                print(f"Is Ollama running at {settings.ollama_base_url}? Start it and run `ollama pull {settings.model}`.",
                      file=sys.stderr)
            print(f"Tokens used before the failure: {usage['tokens']}. The audit log is in {settings.logs_dir}.", file=sys.stderr)
            return 1
        usage = log_run_usage(s, time.time() - started)
        if state.get("scope_request"):
            print(f"\nSCOPE NEEDED: {state['scope_request']}\n  (no tool was run; {usage['tokens']} tokens used for planning)")
            return 3
        paused = pause_payload(state)
        print("\nRESULT")
        print(f"  summary: {state.get('summary')}")
        for reason in state.get("stop_reasons", []):
            print(f"  stopped early: {reason}")
        for f in state.get("flags_created", []):
            new = "created" if f["created"] else "already existed"
            print(f"  FLAG {f['flag_id']} [{f['severity']}] {f['rule']} ({new}, {f['status']}): {f['explanation'][:140]}")
        if not state.get("flags_created"):
            print("  no flags created")
        if state.get("suppressed"):
            print(f"  {len(state['suppressed'])} flag(s) rejected by a reviewer earlier were not raised again")
        quota = usage["remaining_daily_quota_estimate"]
        print(f"  usage: {usage['llm_calls']} LLM calls, {usage['tokens']} tokens (est. cost ${usage['cost_usd']:.4f}), "
              f"{usage['tool_calls']} tool calls, {usage['seconds']:.0f} s"
              + (f" | est. daily quota left: {quota}" if quota is not None else "") + f" | audit log: {settings.logs_dir}")
        if not args.no_save:
            run = {"model": s.llm.name, "llm_calls": usage["llm_calls"], "tokens": usage["tokens"],
                   "tool_calls": usage["tool_calls"], "seconds": usage["seconds"]}
            md = build_summary_md(state, run, int(rules["agent"]["report_awards_listed"]))
            try:
                path = await s.backend.save_text(f"summary_{run_id}.md", md, rules["agent"]["filesystem_write_tool_hint"])
                print(f"  draft saved via the Filesystem MCP server: {path}")
            except ToolCallFailed as exc:
                print(f"  could not save the draft: {exc}", file=sys.stderr)
        if paused:
            print(f"\nPAUSED for human review: {len(paused['flags'])} flag(s) are pending_review. Nothing enters the memo "
                  f"until a reviewer decides.\n  Review:  python -m agent.review {run_id}")
        elif state.get("memo_path"):
            print(f"  memo: {state['memo_path']}")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
