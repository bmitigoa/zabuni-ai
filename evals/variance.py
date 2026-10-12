"""Run-to-run variance:  python -m evals.variance --provider ollama --runs 5   (or --provider groq --runs 2)

The same two requests are run N times each with the real model, no human review step (each run ends at the approval gate). For every
run it records the plan (tool names), tool calls, flags raised, which awards were flagged, tokens, estimated cost and wall-clock
seconds, then the spread across runs. Results are appended to evals/results/variance_<provider>.json after every run, so a long
local run can be stopped and resumed (finished runs are kept).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.graph import new_run_id, run_agent                     # noqa: E402
from agent.llm import LLMError                                    # noqa: E402
from agent.session import log_run_usage, open_session            # noqa: E402
from agent.settings import DEFAULT_MODELS, load_settings         # noqa: E402

QUERIES = [
    {"id": "Q1", "text": "Review Makueni County Government awards 2022-2023 for splitting and price anomalies",
     "hint": {"buyer": "Makueni County Government", "supplier": None, "years": [2022, 2023]}},
    {"id": "Q2", "text": "Check Kenya Rural Roads Authority for non-competitive procurement and supplier concentration in 2023",
     "hint": {"buyer": "Kenya Rural Roads Authority", "supplier": None, "years": [2023]}},
]
RESULTS = ROOT / "evals" / "results"


def flagged_keys(flags: list[dict[str, Any]]) -> list[str]:
    """Stable identifiers of what was flagged: award ids for award-level flags, the buyer for buyer-level ones."""
    keys = []
    for f in flags:
        subject = (f.get("detail") or {}).get("subject") or {}
        if subject.get("awards"):
            keys += [f"{a['ocid']}|{a['award_id']}" for a in subject["awards"]]
        else:
            keys.append(f"{subject.get('kind')}:{subject.get('name')}|{f['rule']}")
    return sorted(set(keys))


async def one_run(settings, query: dict[str, Any]) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="zabuni_var_"))
    os.environ["ZABUNI_CHECKPOINT_DB"] = str(work / "checkpoints.sqlite")
    env = {"ZABUNI_FLAGS_DB": str(work / "flags.duckdb")}                       # a fresh flags store: no memory of earlier runs
    run_id = new_run_id()
    started = time.time()
    record: dict[str, Any] = {"query": query["id"], "run_id": run_id}
    fallbacks: list[dict[str, Any]] = []

    def echo(kind: str, rec: dict[str, Any]) -> None:
        if kind == "llm_fallback":
            fallbacks.append(rec)
    try:
        async with open_session(settings, run_id, echo=echo, extra_env=env) as s:
            state = await run_agent(s.ctx, query["text"], s.checkpointer, query["hint"])
            usage = log_run_usage(s, time.time() - started)
            flags = state.get("flags_created", [])
            record |= {"ok": True, "plan_tools": [st["tool"] for st in state.get("plan", {}).get("steps", [])],
                       "tool_calls": usage["tool_calls"], "n_flags": len(flags), "flagged": flagged_keys(flags),
                       "severities": {sev: sum(f["severity"] == sev for f in flags) for sev in ("high", "medium", "low")},
                       "tokens": usage["tokens"], "prompt_tokens": usage["prompt_tokens"], "completion_tokens": usage["completion_tokens"],
                       "cost_usd": usage["cost_usd"], "seconds": round(time.time() - started, 1), "llm_calls": usage["llm_calls"],
                       "fallbacks": len(fallbacks), "models": usage["models"], "scope_request": bool(state.get("scope_request")),
                       "capped": len(state.get("capped", [])), "merged_notes": [n for n in state.get("notes", []) if "merged" in n]}
    except (LLMError, Exception) as exc:  # noqa: BLE001 - a failed run is data too
        record |= {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}", "seconds": round(time.time() - started, 1)}
    return record


def jaccard(a: set[str], b: set[str]) -> float:
    return 1.0 if not (a | b) else len(a & b) / len(a | b)


def summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for q in sorted({r["query"] for r in runs}):
        ok = [r for r in runs if r["query"] == q and r.get("ok")]
        bad = [r for r in runs if r["query"] == q and not r.get("ok")]
        if not ok:
            out[q] = {"runs": len(bad), "failed": len(bad)}
            continue
        plans = [tuple(sorted(set(r["plan_tools"]))) for r in ok]
        modal = max(set(plans), key=plans.count)
        sets = [set(r["flagged"]) for r in ok]
        pairs = [jaccard(sets[i], sets[j]) for i in range(len(sets)) for j in range(i + 1, len(sets))]
        spread = lambda key: [min(r[key] for r in ok), sorted(r[key] for r in ok)[len(ok) // 2], max(r[key] for r in ok)]   # noqa: E731
        out[q] = {"runs": len(ok) + len(bad), "failed": len(bad), "plan_identical_share": plans.count(modal) / len(plans),
                  "distinct_plans": len(set(plans)), "modal_plan": list(modal), "flagged_overlap_jaccard_mean": round(sum(pairs) / len(pairs), 2) if pairs else None,
                  "flags": spread("n_flags"), "tokens": spread("tokens"), "seconds": spread("seconds"), "cost_usd": spread("cost_usd"),
                  "tool_calls": spread("tool_calls"), "fallbacks": sum(r["fallbacks"] for r in ok)}
    return out


async def amain(provider: str, runs: int) -> None:
    settings = load_settings()
    model = DEFAULT_MODELS[provider] if provider != settings.provider else settings.model
    settings = dataclasses.replace(settings, provider=provider, model=model)
    path = RESULTS / f"variance_{provider}.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"provider": provider, "model": model, "runs": []}
    for i in range(runs):
        for q in QUERIES:
            if sum(1 for r in saved["runs"] if r["query"] == q["id"]) > i:
                continue                                              # resume: this repetition is already recorded
            rec = await one_run(settings, q)
            saved["runs"].append(rec)
            saved["summary"] = summarise(saved["runs"])
            RESULTS.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"{provider} {q['id']} run {i + 1}/{runs}: " + (f"{rec['n_flags']} flags, {rec['tokens']} tokens, {rec['seconds']} s, plan {rec['plan_tools']}"
                                                              if rec.get("ok") else rec["error"]), flush=True)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["ollama", "groq"], default="ollama")
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    asyncio.run(amain(args.provider, args.runs))


if __name__ == "__main__":
    main()
