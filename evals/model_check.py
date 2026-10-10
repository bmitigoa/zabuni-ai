"""Empirical model check: which open-weights model on Groq plans most reliably for THIS agent?

    python -m evals.model_check [model ...] [--runs N]

Runs the real Planner prompt against the real tool catalogue (discovered over MCP) N times per query and model, with a
SINGLE attempt per call (no retries), and scores every plan: valid structure, valid tool names, schema-valid arguments,
buyer and years extracted correctly, no invented ocid/award_id, and the requested checks present. It is a real run:
results are whatever the models returned, written to docs/sample_run/model_check.md.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from agent import policy
from agent.discovery import catalogue, planning_tools
from agent.llm import LLMError, create_llm
from agent.mcp_client import McpBackend
from agent.nodes import PLANNER_SYSTEM
from agent.settings import ROOT, load_settings
from agent.state import Plan

DEFAULT_MODELS = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
OUT_FILE = ROOT / "docs" / "sample_run" / "model_check.md"
RATE_LIMIT_WAIT_S = 20
MAX_RATE_LIMIT_WAITS = 3

# (query, expected buyer substring, expected years, tool-name fragments the plan must cover)
CASES = [
    ("Investigate Makueni County Government 2022-2024", "makueni", [2022, 2023, 2024], []),
    ("Check Kenya Rural Roads Authority for contract splitting and supplier concentration in 2023",
     "kenya rural roads", [2023], ["split", "concentration"]),
]


def args_carry_scope(steps: list[Any], by_name: dict[str, Any], buyer: str, years: list[int]) -> bool:
    """Every step whose tool accepts a buyer / years / date range must actually carry the requested scope in its args.

    (The first version of this check only looked at plan.scope and let a plan with EMPTY args pass: the agent then
    scanned every buyer in Kenya. Empty args are schema-valid, so only this check catches that.)"""
    for s in steps:
        props = (by_name[s.tool].input_schema.get("properties") or {}) if s.tool in by_name else {}
        if "buyer" in props and buyer not in str(s.args.get("buyer", "")).lower():
            return False
        if "years" in props and sorted(s.args.get("years") or []) != years:
            return False
        if "date_from" in props and not (str(s.args.get("date_from", "")).startswith(str(min(years)))
                                         and str(s.args.get("date_to", "")).startswith(str(max(years)))):
            return False
    return True


def score(plan: Plan, tools: list[Any], buyer: str, years: list[int], must_cover: list[str]) -> dict[str, bool]:
    by_name = {t.name: t for t in tools}
    steps = plan.steps
    return {
        "tool_names_valid": bool(steps) and all(s.tool in by_name for s in steps),
        "args_schema_valid": bool(steps) and all(s.tool in by_name and not policy.schema_problems(s.args, by_name[s.tool].input_schema)
                                                 for s in steps),
        "scope_buyer_ok": bool(plan.scope.buyer) and buyer in plan.scope.buyer.lower(),
        "scope_years_ok": sorted(plan.scope.years or []) == years,
        "args_carry_scope": bool(steps) and args_carry_scope(steps, by_name, buyer, years),
        "no_invented_ids": not any(k in s.args for s in steps for k in ("ocid", "award_id")),
        "covers_requested": all(any(frag in s.tool for s in steps) for frag in must_cover),
    }


async def run_model(model: str, backend: McpBackend, tools: list[Any], rules: dict[str, Any], runs: int) -> dict[str, Any]:
    llm = create_llm(dataclasses.replace(load_settings(), provider="groq", model=model), rules)
    llm.max_retries = 0                                            # one attempt: measure raw reliability
    user_tail = f"\n\nTool catalogue:\n{catalogue(tools)}"
    records: list[dict[str, Any]] = []
    for query, buyer, years, cover in CASES:
        for i in range(runs):
            waits = 0
            while True:
                started = time.time()
                try:
                    plan = await llm.structured(Plan, PLANNER_SYSTEM, f"Request: {query}{user_tail}")
                    rec = {"query": query, "ok": True, "seconds": time.time() - started, **score(plan, tools, buyer, years, cover)}
                    rec["plan_steps"] = len(plan.steps)
                    break
                except LLMError as exc:
                    text = str(exc).lower()
                    if ("rate" in text and "limit" in text or "429" in text) and waits < MAX_RATE_LIMIT_WAITS:
                        waits += 1
                        await asyncio.sleep(RATE_LIMIT_WAIT_S)
                        continue
                    rec = {"query": query, "ok": False, "seconds": time.time() - started, "error": str(exc)[:200]}
                    break
            records.append(rec)
            print(f"  {model} run {len(records)}: {'ok' if rec['ok'] else 'FAIL ' + rec['error'][:90]}", flush=True)
    return {"model": model, "records": records, "tokens": llm.tokens, "calls": llm.calls}


def render(results: list[dict[str, Any]], runs: int) -> str:
    keys = ["ok", "tool_names_valid", "args_schema_valid", "args_carry_scope", "scope_buyer_ok", "scope_years_ok",
            "no_invented_ids", "covers_requested"]
    lines = ["# Model check: planning reliability on Groq (real run)", "",
             f"Planner prompt + the real tool catalogue (discovered over MCP), {len(CASES)} requests x {runs} runs per model, "
             "a **single attempt** per call (no retries). Each cell is passes / runs. Produced by `python -m evals.model_check`.", "",
             "| model | " + " | ".join(keys) + " | median s | tokens/run |", "|---|" + "---|" * (len(keys) + 2)]
    for r in results:
        recs, n = r["records"], len(r["records"])
        cells = [f"{sum(bool(x.get(k)) for x in recs)}/{n}" for k in keys]
        secs = statistics.median(x["seconds"] for x in recs) if recs else 0
        lines.append(f"| `{r['model']}` | " + " | ".join(cells) + f" | {secs:.1f} | {r['tokens'] // max(1, r['calls'])} |")
    fails = [(r["model"], x["error"]) for r in results for x in r["records"] if not x["ok"]]
    if fails:
        lines += ["", "Failures:", ""] + [f"- `{m}`: {e}" for m, e in fails[:10]]
    return "\n".join(lines) + "\n"


def notes_section(available: list[str], runs: int) -> str:
    """Honest context for the table: what the key could use, and how small the sample is."""
    llama = [m for m in available if "llama" in m and "guard" not in m]
    lines = ["", "## Notes", "",
             f"- Models available to the key at run time: {', '.join(f'`{m}`' for m in available)}.",
             ("- Llama chat models were not available to this key, so Llama could not be compared "
              "(Groq's documentation lists `llama-3.3-70b-versatile`; the API returned 404 for this account)."
              if not llama else f"- Llama models available: {', '.join(llama)}."),
             f"- Small sample ({len(CASES)} requests x {runs} runs): evidence for choosing a default, not a benchmark.",
             "- The first attempt of the Qwen run failed every call with HTTP 429 `Request too large ... output tokens per "
             "minute (OTPM): Limit 1000, Requested 2048`: an account limit, fixed by capping `max_tokens` "
             "(rules `agent.llm_max_tokens`), not a model fault."]
    return "\n".join(lines) + "\n"


async def amain(models: list[str], runs: int) -> None:
    settings = load_settings()
    async with McpBackend(settings, use_filesystem=False) as backend:
        rules = await backend.read_resource_json("rules://ppada")
        tools = planning_tools(await backend.tools())
        results = [await run_model(m, backend, tools, rules, runs) for m in models]
    from groq import Groq
    available = sorted(m.id for m in Groq(api_key=settings.groq_api_key).models.list().data)
    text = render(results, runs) + notes_section(available, runs)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(text, encoding="utf-8")
    print("\n" + text)
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "model_check.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="*", default=DEFAULT_MODELS)
    ap.add_argument("--runs", type=int, default=4)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(amain(a.models, a.runs))
