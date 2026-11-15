"""Plan coverage:  python -m evals.plan_coverage --provider ollama --mode full|progressive   (Refine 4, quality guard)

Progressive disclosure shrinks what the Planner is shown, so it could plan with too few tools. This eval runs ONLY the Planner (no
investigation, no tool calls, no flags) on request types whose essential checks are obvious from the request itself, with the full
catalogue and with the index-first planner, and records whether every essential check is in the plan.

An essential check is a group of acceptable tools (any one satisfies it): a request that says "contract splitting" needs
`detect_splitting`; "price outliers" is satisfied by `detect_price_anomalies` or `compute_price_benchmark`. Coverage = essential checks
present / essential checks. The plan is the model's own: the follow-up rules that run during an investigation are not counted.
Results: evals/results/plan_coverage_<provider>_<mode>.json (rendered into EVALS.md section 7).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.graph import new_run_id                                 # noqa: E402
from agent.nodes import planner                                    # noqa: E402
from agent.session import open_session                             # noqa: E402
from agent.settings import DEFAULT_MODELS, load_settings          # noqa: E402

RESULTS = ROOT / "evals" / "results"
SPLIT, ANOM, BENCH = "detect_splitting", "detect_price_anomalies", "compute_price_benchmark"
NONCOMP, CONC = "detect_noncompetitive_method", "supplier_concentration"
PRICE = [ANOM, BENCH]                                               # either satisfies "price outliers"

CASES: list[dict[str, Any]] = [
    {"id": "P1", "type": "splitting", "text": "Review Makueni County Government awards 2022-2023 for contract splitting",
     "hint": {"buyer": "Makueni County Government", "years": [2022, 2023]}, "essential": [[SPLIT]]},
    {"id": "P2", "type": "non-competitive method", "text": "Check Kenya Rural Roads Authority for non-competitive procurement in 2023",
     "hint": {"buyer": "Kenya Rural Roads Authority", "years": [2023]}, "essential": [[NONCOMP]]},
    {"id": "P3", "type": "supplier concentration", "text": "Check supplier concentration at Kenya Power & Lighting Company in 2023",
     "hint": {"buyer": "Kenya Power & Lighting Company", "years": [2023]}, "essential": [[CONC]]},
    {"id": "P4", "type": "price outliers", "text": "Look for price outliers in Makueni County Government awards in 2023",
     "hint": {"buyer": "Makueni County Government", "years": [2023]}, "essential": [PRICE]},
    {"id": "P5", "type": "price comparison", "text": "Compare the prices of fuel supply awards by Kenya Ports Authority in 2022 with similar awards",
     "hint": {"buyer": "Kenya Ports Authority", "years": [2022]}, "essential": [PRICE]},
    {"id": "P6", "type": "splitting + price (Q1)", "text": "Review Makueni County Government awards 2022-2023 for splitting and price anomalies",
     "hint": {"buyer": "Makueni County Government", "years": [2022, 2023]}, "essential": [[SPLIT], [ANOM]]},
    {"id": "P7", "type": "non-competitive + concentration (Q2)", "text": "Check Kenya Rural Roads Authority for non-competitive procurement and supplier concentration in 2023",
     "hint": {"buyer": "Kenya Rural Roads Authority", "years": [2023]}, "essential": [[NONCOMP], [CONC]]},
    {"id": "P8", "type": "supplier-scoped splitting", "text": "Check awards to DOUBLE KEY SYSTEMS LTD for contract splitting",
     "hint": {"supplier": "DOUBLE KEY SYSTEMS LTD"}, "essential": [[SPLIT]]},
]


def coverage(planned: list[str], essential: list[list[str]]) -> tuple[int, list[list[str]]]:
    """(number of essential checks present, the groups that are missing). A group is present when any of its tools is planned."""
    missing = [g for g in essential if not set(g) & set(planned)]
    return len(essential) - len(missing), missing


async def one_case(settings, case: dict[str, Any]) -> dict[str, Any]:
    work = Path(tempfile.mkdtemp(prefix="zabuni_cov_"))
    os.environ["ZABUNI_CHECKPOINT_DB"] = str(work / "checkpoints.sqlite")
    run_id = new_run_id()
    rec: dict[str, Any] = {"id": case["id"], "type": case["type"], "essential": case["essential"]}
    try:
        async with open_session(settings, run_id, use_filesystem=False, extra_env={"ZABUNI_FLAGS_DB": str(work / "flags.duckdb")}) as s:
            out = await planner({"run_id": run_id, "query": case["text"], "scope_hint": case["hint"]}, s.ctx)
            planned = [st["tool"] for st in (out.get("plan") or {}).get("steps", [])]
            present, missing = coverage(planned, case["essential"])
            rec |= {"ok": True, "planned": planned, "present": present, "missing": missing, "scope_requested": bool(out.get("scope_request")),
                    "tokens": s.llm.tokens, "llm_calls": s.llm.calls, "malformed": s.llm.malformed}
    except Exception as exc:  # noqa: BLE001 - a failed case is data too
        rec |= {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    return rec


async def amain(provider: str, mode: str) -> None:
    settings = load_settings()
    model = DEFAULT_MODELS[provider] if provider != settings.provider else settings.model
    settings = dataclasses.replace(settings, provider=provider, model=model)
    rules_file = Path(tempfile.mkdtemp(prefix="zabuni_rules_")) / "rules.json"
    rules_file.write_text(json.dumps({"agent": {"progressive_disclosure": mode == "progressive"}}), encoding="utf-8")
    os.environ["ZABUNI_RULES_FILE"] = str(rules_file)
    path = RESULTS / f"plan_coverage_{provider}_{mode}.json"
    saved: dict[str, Any] = {"provider": provider, "model": model, "mode": mode, "cases": []}
    for case in CASES:
        rec = await one_case(settings, case)
        saved["cases"].append(rec)
        ok = [c for c in saved["cases"] if c.get("ok")]
        saved["summary"] = {"cases": len(saved["cases"]), "failed": len(saved["cases"]) - len(ok),
                            "essential": sum(len(c["essential"]) for c in ok), "present": sum(c["present"] for c in ok)}
        RESULTS.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{provider}/{mode} {case['id']}: " + (f"planned {rec['planned']} missing {rec['missing']}" if rec.get("ok") else rec["error"]), flush=True)
        if not rec.get("ok") and any(m in rec["error"].lower() for m in ("quota", "rate limit")):
            saved["stopped"] = f"stopped at {case['id']}: {rec['error'][:200]}"
            path.write_text(json.dumps(saved, indent=1, ensure_ascii=False), encoding="utf-8")
            print("provider quota exhausted; stopped cleanly", flush=True)
            return


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["ollama", "groq"], default="ollama")
    ap.add_argument("--mode", choices=["full", "progressive"], default="progressive")
    args = ap.parse_args()
    asyncio.run(amain(args.provider, args.mode))


if __name__ == "__main__":
    main()
