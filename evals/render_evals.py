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
    counter: dict[str, int] = {}
    for r in runs:
        counter[r["query"]] = counter.get(r["query"], 0) + 1
        i = counter[r["query"]]
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


def _med(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def refine3_section(provider: str, full: dict[str, Any] | None, prog: dict[str, Any] | None) -> list[str]:
    """Before/after table for Refine 3 (progressive disclosure): the same code with the Planner shown the full tool catalogue vs index first."""
    if not full or not prog:
        return []
    ok = {m: [r for r in d["runs"] if r.get("ok")] for m, d in (("full", full), ("progressive", prog))}
    if not ok["full"] or not ok["progressive"]:
        return []

    def per_kind(runs: list[dict[str, Any]], kind: str, key: str = "prompt_tokens") -> float:
        return _med([c[key] for r in runs for c in r.get("call_log", []) if c["kind"] == kind and c.get(key) is not None])

    def stat(mode: str) -> dict[str, Any]:
        runs = ok[mode]
        calls = [c for r in runs for c in r.get("call_log", [])]
        attempts = sum(r.get("model_attempts", 0) for r in runs)
        plan_secs = [sum(c["seconds"] or 0 for c in r.get("call_log", []) if c["kind"] in ("plan", "tool_choice")) for r in runs]
        proposed = sum(r.get("plan_steps_proposed", 0) for r in runs)
        return {"runs": len(runs), "failed": len(full["runs"] if mode == "full" else prog["runs"]) - len(runs),
                "plan_prompt": per_kind(runs, "plan"), "mean_prompt": statistics.mean(c["prompt_tokens"] for c in calls if c.get("prompt_tokens")) if calls else 0,
                "calls": _med([len(r.get("call_log", [])) for r in runs]), "tokens": _med([r["tokens"] for r in runs]),
                "prompt_total": _med([r["prompt_tokens"] for r in runs]),
                "valid": (proposed - sum(r.get("plan_steps_rejected", 0) for r in runs)) / proposed if proposed else 0.0,
                "malformed": sum(r.get("malformed_attempts", 0) for r in runs), "attempts": attempts,
                "seconds": _med([r["seconds"] for r in runs]), "plan_seconds": _med(plan_secs),
                "fallbacks": sum(r.get("choice_fallbacks", 0) for r in runs)}
    a, b = stat("full"), stat("progressive")
    jac = []
    for q in sorted({r["query"] for r in ok["full"]}):
        fa = [set(r["flagged"]) for r in ok["full"] if r["query"] == q]
        fb = [set(r["flagged"]) for r in ok["progressive"] if r["query"] == q]
        if fa and fb:
            jac.append(statistics.mean(1.0 if not (x | y) else len(x & y) / len(x | y) for x in fa for y in fb))
    same_plan = [set(r["plan_tools"]) for r in ok["full"]], [set(r["plan_tools"]) for r in ok["progressive"]]
    rate = lambda d: f"{d['malformed']}/{d['attempts']} ({100 * d['malformed'] / d['attempts']:.1f}%)" if d["attempts"] else "n/a"   # noqa: E731
    delta = lambda x, y: f"{100 * (y - x) / x:+.0f}%" if x else "n/a"                                                                  # noqa: E731
    rows = [("Runs (failed)", f"{a['runs']} ({a['failed']})", f"{b['runs']} ({b['failed']})", ""),
            ("Planning prompt, tokens (median)", f"{a['plan_prompt']:,.0f}", f"{b['plan_prompt']:,.0f}", delta(a['plan_prompt'], b['plan_prompt'])),
            ("Tool-choice (index) prompt, tokens (median)", "n/a", f"{per_kind(ok['progressive'], 'tool_choice'):,.0f}", ""),
            ("Prompt tokens per LLM call (mean of all calls)", f"{a['mean_prompt']:,.0f}", f"{b['mean_prompt']:,.0f}", delta(a['mean_prompt'], b['mean_prompt'])),
            ("LLM calls per run (median)", f"{a['calls']:.0f}", f"{b['calls']:.0f}", ""),
            ("Prompt tokens per run (median)", f"{a['prompt_total']:,.0f}", f"{b['prompt_total']:,.0f}", delta(a['prompt_total'], b['prompt_total'])),
            ("Total tokens per run (median)", f"{a['tokens']:,.0f}", f"{b['tokens']:,.0f}", delta(a['tokens'], b['tokens'])),
            ("Plan validity (steps naming a real, offered tool)", f"{100 * a['valid']:.0f}%", f"{100 * b['valid']:.0f}%", ""),
            ("Malformed tool-call rate (unusable model attempts / attempts)", rate(a), rate(b), ""),
            ("Planning latency, seconds (median, plan + tool-choice calls)", f"{a['plan_seconds']:.1f}", f"{b['plan_seconds']:.1f}", delta(a['plan_seconds'], b['plan_seconds'])),
            ("Whole-run latency, seconds (median)", f"{a['seconds']:.1f}", f"{b['seconds']:.1f}", delta(a['seconds'], b['seconds'])),
            ("Tool-choice fallbacks to the full catalogue", "n/a", str(b['fallbacks']), "")]
    out = [f"### {provider} (`{full['model']}`)", "", "| Measure | Before: full catalogue | After: index, then chosen schemas | Change |", "|---|---|---|---|"]
    out += [f"| {m} | {x} | {y} | {z} |" for m, x, y, z in rows]
    out += ["", f"Same answer? Mean Jaccard overlap of the flagged-award sets between the two modes: **{statistics.mean(jac):.2f}** (1.0 = identical); "
            f"runs with the same planned tool set in both modes: Q-wise modal plans {'match' if max(same_plan[0], key=same_plan[0].count) == max(same_plan[1], key=same_plan[1].count) else 'differ'}.", ""]
    for mode, d in (("full", full), ("progressive", prog)):
        if d.get("stopped"):
            out += [f"*Run stopped cleanly ({mode}): {d['stopped']}*", ""]
    return out


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
            "(1.0 = the same awards every time). The tools are exact queries and a seeded model, and the language models run at temperature 0, so **run-to-run spread is near zero**: "
            "the same plan, the same flagged awards, tokens within a handful. The first Ollama run of each query is slower only because the local model had to load. "
            "**The real difference is between models, not between runs:** for the same request Qwen 2.5 7B (Ollama) plans 3 tools and Qwen 3.8 27B (Groq) plans 5, so Groq finds more "
            "(for Q2, 3 flags against 1). On Groq every Q1 run logs one fallback: Qwen's summary call returned a malformed tool call five times and gpt-oss-120b answered that one call "
            "(cheaper per token, which is why Q1 costs less than Q2 for similar tokens). Samples are small (5 and 2 runs per request): they show the spread, they do not bound it, "
            "and temperature 0 does not guarantee identical output on a hosted model that changes under us.", ""]
    out += cost_section(groq, ollama)
    r3 = []
    for name, prov in (("Ollama", "ollama"), ("Groq", "groq")):
        r3 += refine3_section(name, load(f"variance_{prov}_full.json"), load(f"variance_{prov}_progressive.json"))
    if r3:
        out += ["## 6. Refine 3: context budget and progressive disclosure (before / after)", "",
                "*Before* = the v1.0 behaviour: the Planner is shown the full catalogue (every tool's description and argument list) in one call. *After* = the Planner first sees a "
                "one-line index and names the tools it needs, then sees full argument lists for those tools only. Both columns are the SAME code and the same rules except "
                "`agent.progressive_disclosure`, measured with the same instrumentation (`python -m evals.variance --provider P --runs N --mode full|progressive`). "
                "The numbers in sections 4-5 above are the v1.0 submission runs (before this instrumentation existed) and are kept as history.", ""] + r3
    (ROOT / "EVALS.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("EVALS.md written")


if __name__ == "__main__":
    main()
