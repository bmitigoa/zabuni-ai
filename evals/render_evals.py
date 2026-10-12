"""Write EVALS.md from the recorded results:   python -m evals.render_evals

Reads evals/results/cases.json (python -m evals.run_cases) and evals/results/variance_<provider>.json (python -m evals.variance), and
the pricing in mcp_server/rules.py. Nothing is typed in by hand except the explanatory text: every number comes from a result file.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "evals" / "results"
sys.path.insert(0, str(ROOT))
from mcp_server.rules import load_rules  # noqa: E402

QUERY_TEXT = {"Q1": "Review Makueni County Government awards 2022-2023 for splitting and price anomalies",
              "Q2": "Check Kenya Rural Roads Authority for non-competitive procurement and supplier concentration in 2023"}


def load(name: str) -> dict[str, Any] | None:
    path = RESULTS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def esc(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "/")


def cases_section(data: dict[str, Any]) -> list[str]:
    rs = data["results"]
    tally = {s: sum(r["status"] == s for r in rs) for s in ("PASS", "FAIL", "ERROR")}
    out = [f"## 1. Cases ({len(rs)}): {tally['PASS']} pass, {tally['FAIL']} fail, {tally['ERROR']} error", "",
           f"Run on {data['run_at']} against the frozen snapshot with the real `zabuni-mcp` server (`python -m evals.run_cases`). "
           "Detection cases (D) test each rule on a known positive and a known negative with no model involved. Agent cases (A) test the agent's guards and "
           "the human gate with a scripted stand-in for the LLM, so they are deterministic and test **our code, not a model** (model behaviour is in section 4). "
           "False-alarm cases (F) are practitioner-style situations where a flag probably has a lawful explanation: **a raised flag is a FAIL on purpose**, "
           "because the rules cannot tell lots, frameworks or emergencies from splitting. Failures are kept, not tuned away.", "",
           "| ID | Category | Case | Expectation | Result | What happened |", "|---|---|---|---|---|---|"]
    for r in rs:
        out.append(f"| {r['id']} | {r['category']} | {esc(r['title'])} | {esc(r['expectation'])} | **{r['status']}** | {esc(r['observed'])[:330]} |")
    fails = [r for r in rs if r["status"] != "PASS"]
    if fails:
        out += ["", "**What the failures mean.** F1-F3 show the limit of rule-based detection on this data: the awards really do match the rules (several awards to one "
                "contractor within 30 days under a threshold; a high direct-procurement share), and nothing in the published data says whether the awards are separate road "
                "sections, call-offs of a framework or an emergency. The system does what it can: it raises the flag, states on the card the lawful explanations to rule out "
                "(`flag_caveats` in `rules.py`), and leaves the decision to a human. Whether the expectation in each F case is right is itself a hypothesis for a practitioner "
                "to confirm (`docs/user_engagement.md`)."]
    return out


def spread(values: list[float], fmt: str = "{:g}") -> str:
    return f"{fmt.format(min(values))} / {fmt.format(statistics.median(values))} / {fmt.format(max(values))}"


def variance_section(provider: str, data: dict[str, Any] | None) -> list[str]:
    if not data:
        return [f"### {provider}: not run", ""]
    runs = data["runs"]
    out = [f"### {provider} (`{data['model']}`)", "",
           "| Query | Runs (failed) | Identical plan (tool set) | Distinct plans | Flagged-award overlap (mean Jaccard) | Flags min / median / max | Tokens min / median / max | Seconds min / median / max | Est. cost USD min / median / max |",
           "|---|---|---|---|---|---|---|---|---|"]
    for q in sorted({r["query"] for r in runs}):
        ok = [r for r in runs if r["query"] == q and r.get("ok")]
        bad = [r for r in runs if r["query"] == q and not r.get("ok")]
        if not ok:
            out.append(f"| {q} | {len(bad)} ({len(bad)}) | all failed | | | | | | |")
            continue
        s = data["summary"][q]
        out.append(f"| {q} | {s['runs']} ({s['failed']}) | {s['plan_identical_share']:.0%} | {s['distinct_plans']} | {s['flagged_overlap_jaccard_mean']} | "
                   f"{spread([r['n_flags'] for r in ok])} | {spread([r['tokens'] for r in ok])} | {spread([r['seconds'] for r in ok])} | "
                   f"{spread([r['cost_usd'] for r in ok], '{:.4f}')} |")
    out += ["", "Per run:", "", "| Query | Run | Plan (tools) | Tool calls | Flags (high/med/low) | Tokens | Seconds | Fallbacks |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(runs, 1):
        if r.get("ok"):
            sev = r["severities"]
            out.append(f"| {r['query']} | {i} | {', '.join(t.replace('detect_', '').replace('compute_', '') for t in r['plan_tools'])} | {r['tool_calls']} | "
                       f"{r['n_flags']} ({sev['high']}/{sev['medium']}/{sev['low']}) | {r['tokens']} | {r['seconds']} | {r['fallbacks']} |")
        else:
            out.append(f"| {r['query']} | {i} | FAILED | | | | {r['seconds']} | {esc(r.get('error', ''))[:120]} |")
    return out + [""]


def cost_section(groq: dict[str, Any] | None, ollama: dict[str, Any] | None) -> list[str]:
    rules = load_rules()["agent"]["pricing"]
    out = ["## 5. Cost per run", "",
           f"Prices are the Groq pay-as-you-go list in `rules.py` (retrieved {rules['as_of']}); the **free tier itself costs nothing**, so the figures are what the same tokens "
           "would cost on a paid plan. Ollama runs locally: cost 0 (electricity not counted). Tokens and seconds are medians of the runs in section 4 (each run = plan, "
           "investigation, summary; the review step adds about 1,000-1,500 tokens when a flag is queried).", "",
           "| Provider / model | Runs | Median tokens | Median est. cost per run | Median seconds | Cost for 1,000 runs |", "|---|---|---|---|---|---|"]
    for name, data in (("Groq", groq), ("Ollama", ollama)):
        if not data:
            continue
        ok = [r for r in data["runs"] if r.get("ok")]
        if not ok:
            continue
        tok, cost, sec = (statistics.median(r[k] for r in ok) for k in ("tokens", "cost_usd", "seconds"))
        out.append(f"| {name} `{data['model']}` | {len(ok)} | {tok:,.0f} | ${cost:.4f} | {sec:,.0f} | ${cost * 1000:,.2f} |")
    return out + [""]


def main() -> None:
    cases, groq, ollama = load("cases.json"), load("variance_groq.json"), load("variance_ollama.json")
    out = ["# EVALS", "", "Real runs only: every number below was produced by the scripts in `evals/` on the frozen PPRA snapshot (8 Oct 2026) and is stored in `evals/results/`. "
           "Reproduce: `python -m evals.run_cases`, `python -m evals.variance --provider ollama --runs 5`, `python -m evals.variance --provider groq --runs 2`, "
           "`python -m evals.render_evals`. Failures are kept visible.", ""]
    if cases:
        out += cases_section(cases) + [""]
    out += ["## 2. The intentional failure: \"hello\" produced 40 flags", "",
            "The request `hello` (no buyer, supplier or period) made the Day 4 agent scan all of Kenya and raise **40 flags in 53 tool calls**; the local model had invented the years 2022-2024. "
            "Remediation (Day 5): the scope is grounded in the request text, a request with no scope ends with a question and **runs no tool**, and at most 10 flags are raised per run. "
            "Re-run for real on the same model: **0 tool calls, 0 flags, 1,264 tokens**. Full before/after, with the tests that pin it: [`evals/intentional_failure.md`](evals/intentional_failure.md) "
            "(agent test: case A2 above).", "",
            "## 3. What the cases do not show", "",
            "* The agent cases use a scripted model, so they cannot say how often a real model plans well; section 4 measures that on two requests only.",
            "* There is **no ground truth** for 'a real problem': positives and negatives are *rule* positives and negatives chosen from the data, not audit findings.",
            "* Thresholds are provisional; changing one changes these results (a test keeps `docs/red_flags.md` in step with `rules.py`).", "",
            "## 4. Run-to-run variance", "",
            "Two requests, each run repeatedly with the real model, no human review step:", ""]
    out += [f"* **{q}**: {text}" for q, text in QUERY_TEXT.items()] + [""]
    out += variance_section("Ollama", ollama) + variance_section("Groq", groq)
    out += ["Reading the tables: *identical plan* = share of runs whose set of planned tools equals the most common set; *overlap* = mean Jaccard similarity of the flagged-award sets between runs "
            "(1.0 = the same awards every time). The investigation is deterministic given a plan (the tools are exact SQL and a seeded model), so flag spread comes from plan differences; "
            "tokens and seconds vary with the model and, on Groq, with rate-limit waits. Samples are small (5 and 2 runs per request): they show the spread, they do not bound it.", ""]
    out += cost_section(groq, ollama)
    (ROOT / "EVALS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("EVALS.md written")


if __name__ == "__main__":
    main()
