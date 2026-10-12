"""Run the evaluation cases for real and write evals/results/cases.json:   python -m evals.run_cases

Three kinds of case, all executed on the frozen OCDS snapshot with the real zabuni-mcp server:
  * detection: each rule on a KNOWN positive and a KNOWN negative (the tools themselves, no model involved);
  * agent logic: the guards and the human gate, driven by a scripted stand-in for the LLM (deterministic: they test the
    agent's code, not any model; model behaviour is measured separately by evals/variance.py);
  * false alarms: practitioner-style cases where a flag is likely to have a lawful explanation. A raised flag is a FAIL here
    on purpose (the tools cannot tell lots, frameworks or emergencies from splitting); the card's caveat is reported as the mitigation.

Nothing is curated to pass: a case that fails stays in the table with what actually happened.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp_server import tools                                                         # noqa: E402
from mcp_server.rules import load_rules                                             # noqa: E402

OUT = ROOT / "evals" / "results" / "cases.json"
BUYER = "Makueni County Government"
CASES: list[tuple[str, str, str, str, Callable[[], tuple[bool, str]]]] = []


def case(cid: str, category: str, title: str, expectation: str):
    def deco(fn):
        CASES.append((cid, category, title, expectation, fn))
        return fn
    return deco


def flags_of(out: dict[str, Any]) -> list[dict[str, Any]]:
    return out["result"].get("flags", [])


# ----------------------------------------------------------------------------- detection rules: known positives and negatives
@case("D1", "detection", "Splitting: positive", f"{BUYER} 2022-2023 raises the 3-award cluster to DOUBLE KEY SYSTEMS (each just under KES 5M, 13 days)")
def d1():
    f = [x for x in flags_of(tools.detect_splitting(buyer=BUYER, years=[2022, 2023], limit=10)) if "DOUBLE KEY" in x["summary"]]
    return bool(f) and len(f[0]["detail"]["subject"]["awards"]) == 3, f[0]["summary"][:110] if f else "no DOUBLE KEY cluster found"


@case("D2", "detection", "Splitting: negative", f"{BUYER} 2019 raises nothing")
def d2():
    n = len(flags_of(tools.detect_splitting(buyer=BUYER, years=[2019], limit=10)))
    return n == 0, f"{n} flags"


@case("D3", "detection", "Price benchmark: positive", "the steel-drums award (KES 8.9M) is flagged with >= 10 comparables")
def d3():
    r = tools.compute_price_benchmark(ocid="ocds-5whusi-GMC/K/D/056/2023/2024", award_id="20240703171733-91111")["result"]
    return bool(r["flagged"]) and r["cohort"]["n"] >= 10, f"{r['target']['ratio_to_median']}x the median of {r['cohort']['n']} comparables"


@case("D4", "detection", "Price benchmark: negative", "an award at 0.43x its comparables' median is not flagged")
def d4():
    r = tools.compute_price_benchmark(ocid="ocds-5whusi-1366280-23/24", award_id="20240708123046-91956")["result"]
    return not r["flagged"], f"{r['target']['ratio_to_median']}x the median of {r['cohort']['n']} comparables, flagged={r['flagged']}"


@case("D5", "detection", "Price anomaly (Isolation Forest): positive", f"{BUYER} 2022-2023: the staff-medical-cover award is flagged as an outlier")
def d5():
    f = [x for x in flags_of(tools.detect_price_anomalies(buyer=BUYER, years=[2022, 2023], limit=5)) if x["ocids"][0] == "ocds-5whusi-42686"]
    return bool(f), (f[0]["detail"]["signals"][0]["metric"] if f else "not flagged")


@case("D6", "detection", "Price anomaly: negative", f"{BUYER} 2019 raises nothing (cohorts too small to model)")
def d6():
    out = tools.detect_price_anomalies(buyer=BUYER, years=[2019])
    return not flags_of(out), "; ".join(out["warnings"][:1])


@case("D7", "detection", "Non-competitive method: positive", "Kenya Power & Lighting Company 2023: direct+restricted share >= 2x the national baseline")
def d7():
    r = tools.detect_noncompetitive_method(buyer="Kenya Power & Lighting Company", years=[2023])["result"]
    return bool(flags_of({"result": r})), f"{r['buyer_stats']['ratio_to_baseline']}x the baseline, {r['buyer_stats']['n_noncompetitive']} awards"


@case("D8", "detection", "Non-competitive method: negative", f"{BUYER} 2023 (0.11x the baseline) is not flagged")
def d8():
    r = tools.detect_noncompetitive_method(buyer=BUYER, years=[2023])["result"]
    return not flags_of({"result": r}), f"{r['buyer_stats']['ratio_to_baseline']}x the baseline"


@case("D9", "detection", "Supplier concentration: positive", "the national ranking raises at least one buyer whose top supplier holds >= 25 %")
def d9():
    fl = flags_of(tools.supplier_concentration(limit=10))
    return bool(fl), (fl[0]["summary"][:120] if fl else "no flag")


@case("D10", "detection", "Supplier concentration: negative", f"{BUYER} 2023 (top supplier 0.17 % of awards) is not flagged")
def d10():
    r = tools.supplier_concentration(buyer=BUYER, years=[2023])["result"]
    return not r.get("flags"), f"top supplier share by count {r['suppliers'][0]['share_count']:.2%}"


# ----------------------------------------------------------------------------- agent logic (scripted stand-in for the model)
def _agent():
    """Imports and helpers shared by the agent cases; fresh private databases per case."""
    from tests.fakes import FakeLLM
    from tests.test_review_flow import Session, make_llm, plan_for
    from agent.state import ClarifyAnswer, ClarifyPlan, FindingDraft, Plan, PlanStep, SynthOutput
    work = Path(tempfile.mkdtemp(prefix="zabuni_eval_"))
    os.environ["ZABUNI_FLAGS_DB"] = str(work / "flags.duckdb")
    return work, FakeLLM, Session, make_llm, plan_for, (ClarifyAnswer, ClarifyPlan, FindingDraft, Plan, PlanStep, SynthOutput)


def decisions(ids, decision, reason="checked the cited records", role="internal_auditor"):
    return [{"flag_id": i, "decision": decision, "reason": reason, "reviewer_role": role} for i in ids]


@case("A1", "agent", "Citation guard", "a model that cites an invented ocid, in a finding or in the summary, cannot get it into a stored flag")
def a1():
    from agent.graph import pause_payload
    work, FakeLLM, Session, make_llm, plan_for, (_, _, FindingDraft, Plan, _, SynthOutput) = _agent()
    import json as _json

    def lying(system, user):
        items = _json.loads(user.split("Candidate flags:\n", 1)[1].split("\n\nNotes:", 1)[0])
        c = items[0]
        return SynthOutput(summary="The supplier also won ocds-9zzz-INVENTED last year.", findings=[
            FindingDraft(candidate_id=c["id"], severity="high", ocids=["ocds-9zzz-FAKE-1"], explanation="Cites a made-up ocid."),
            FindingDraft(rule="search_awards", severity="high", ocids=["ocds-9zzz-FAKE-2"], explanation="An invented extra finding with nothing behind it.")])
    llm = make_llm(SynthOutput=lying) if False else FakeLLM({**make_llm().script, SynthOutput: lying})

    async def go():
        async with Session(work, llm) as s:
            return await s.run()
    state = asyncio.run(go())
    stored = _json.dumps(state["flags_created"])
    ok = len(state["dropped"]) == 2 and "ocds-9zzz" not in stored and "INVENTED" not in state["summary"] and bool(state["flags_created"])
    return ok, f"{len(state['dropped'])} drafts dropped, 0 invented ocids stored, {len(state['flags_created'])} tool-proposed flags kept"


@case("A2", "agent", "Scope guard", "a request with no buyer, supplier or period runs no tool, even if the model invents a scope")
def a2():
    work, FakeLLM, Session, make_llm, plan_for, (_, _, _, Plan, _, _) = _agent()
    llm = FakeLLM({**make_llm().script, Plan: plan_for(("detect_splitting", {}), buyer=None, years=(2022, 2023, 2024))})

    async def go():
        async with Session(work, llm) as s:
            return await s.run("hello")
    state = asyncio.run(go())
    ok = bool(state.get("scope_request")) and state["results"] == [] and not (work / "logs" / "tool_calls.jsonl").exists()
    return ok, f"scope requested, 0 tool calls, {llm.calls} LLM call"


@case("A3", "agent", "Budget stop", "when the token budget is spent the run stops cleanly and the memo says so")
def a3():
    from tests.test_review_flow import TokenFake
    work, FakeLLM, Session, make_llm, plan_for, (_, _, _, Plan, _, SynthOutput) = _agent()
    base = make_llm().script
    llm = TokenFake({**base}, per_call=1200)

    async def go():
        async with Session(work, llm, rules_edit=lambda r: r["agent"].update(max_total_tokens=1000, synth_reserve_tokens=0, followups=[])) as s:
            return await s.run()
    state = asyncio.run(go())
    ok = state["results"] == [] and "The run stopped early" in state["memo_markdown"] and any("token budget" in r for r in state["stop_reasons"])
    return ok, "stopped before any tool call; memo states the stop"


@case("A4", "agent", "Rejected-flag memory", "a flag a reviewer rejected is not raised again in a later run on the same scope")
def a4():
    from agent.graph import pause_payload
    work, FakeLLM, Session, make_llm, plan_for, _ = _agent()

    async def first():
        async with Session(work, make_llm(), run_id="eval_one") as s:
            ids = [f["flag_id"] for f in pause_payload(await s.run())["flags"]]
            await s.resume(decisions(ids[:1], "reject", "framework agreement, lawful") + decisions(ids[1:], "approve"))
            return ids

    async def second():
        async with Session(work, make_llm(), run_id="eval_two") as s:
            return await s.run()
    ids = asyncio.run(first())
    state = asyncio.run(second())
    new = {f["flag_id"] for f in state["flags_created"]}
    return ids[0] not in new and len(state["suppressed"]) == 1, f"{len(ids)} flags in run 1, {len(new)} in run 2; the rejected one was suppressed"


@case("A5", "agent", "Clarify loop", "a reviewer's question re-investigates that flag, attaches an answer and pauses again before the memo")
def a5():
    from agent.graph import pause_payload
    work, FakeLLM, Session, make_llm, plan_for, (ClarifyAnswer, ClarifyPlan, _, Plan, PlanStep, SynthOutput) = _agent()
    llm = make_llm()
    llm.script = {**llm.script, ClarifyPlan: ClarifyPlan(steps=[PlanStep(tool="search_awards", args={"buyer": BUYER, "limit": 2}, reason="look")]),
                  ClarifyAnswer: ClarifyAnswer(response="The re-check found the buyer's awards; nothing more was visible in them.")}

    async def go():
        async with Session(work, llm) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            second = await s.resume(decisions(ids[:1], "clarify", "Is this the only such award?") + decisions(ids[1:], "approve"))
            third = await s.resume(decisions(ids[:1], "approve", "answer read"))
            return ids, second, third
    ids, second, third = asyncio.run(go())
    again = pause_payload(second)
    ok = again is not None and [f["flag_id"] for f in again["flags"]] == ids[:1] and bool(again["flags"][0]["clarifications"]) and third["review_status"] == "memo_drafted"
    return ok, "paused again with the agent's answer attached, then drafted the memo after approval"


@case("A6", "agent", "Duplicate-flag merge", "a price benchmark and a price anomaly on the SAME award become one card with two signals")
def a6():
    work, FakeLLM, Session, make_llm, plan_for, _ = _agent()
    llm = make_llm(steps=[("detect_price_anomalies", {"buyer": BUYER, "years": [2022, 2023], "limit": 5}),
                          ("compute_price_benchmark", {"ocid": "ocds-5whusi-42686", "award_id": "20250205150132-43031"})])

    async def go():
        async with Session(work, llm, rules_edit=lambda r: r["agent"].update(followups=[])) as s:
            return await s.run(f"Review {BUYER} awards 2022 2023")
    state = asyncio.run(go())
    mine = [f for f in state["flags_created"] if f["ocid"] == "ocds-5whusi-42686"]
    return len(mine) == 1 and len(mine[0]["detail"]["signals"]) == 2, f"{len(mine)} card, {len(mine[0]['detail']['signals']) if mine else 0} signals"


@case("A7", "agent", "Scope check on flagged awards", "a flagged award dated outside the requested years is not raised")
def a7():
    from agent.graph import run_agent
    work, FakeLLM, Session, make_llm, plan_for, _ = _agent()
    llm = make_llm(steps=[("compute_price_benchmark", {"ocid": "ocds-5whusi-42686", "award_id": "20250205150132-43031"})])

    async def go():
        async with Session(work, llm, rules_edit=lambda r: r["agent"].update(followups=[])) as s:
            return await run_agent(s.ctx, "Review awards in 2019", s.saver, {"years": [2019]})
    state = asyncio.run(go())
    return state["flags_created"] == [], "; ".join(n for n in state["notes"] if "outside" in n)[:140]


@case("A8", "agent", "Flag cap", "at most max_flags_per_run flags are raised, strongest first, and the memo says so")
def a8():
    from agent.graph import pause_payload
    work, FakeLLM, Session, make_llm, plan_for, _ = _agent()

    async def go():
        async with Session(work, make_llm(), rules_edit=lambda r: r["agent"].update(max_flags_per_run=2)) as s:
            state = await s.run()
            ids = [f["flag_id"] for f in pause_payload(state)["flags"]]
            return state, await s.resume(decisions(ids, "approve"))
    state, final = asyncio.run(go())
    return len(state["flags_created"]) == 2 and "Flag cap:" in final["memo_markdown"], f"{len(state['flags_created'])} flags raised, {len(state['capped'])} capped"


# ----------------------------------------------------------------------------- practitioner-style false alarms (a raised flag FAILS the case)
def _caveat(rule: str) -> str:
    return load_rules()["flag_caveats"][rule][:60]


@case("F1", "false alarm", "Road-section lots (KRRA)", "routine-maintenance contracts for DIFFERENT road sections awarded to one contractor are lots of a programme, not splitting")
def f1():
    fl = [f for f in flags_of(tools.detect_splitting(buyer="Kenya Rural Roads Authority", years=[2023], limit=10)) if "LOIDONGO" in f["summary"].upper()]
    titles = [a["title"] for a in fl[0]["detail"]["subject"]["awards"]] if fl else []
    return not fl, (f"FLAGGED (false alarm): {len(titles)} awards with different road names {titles}; card caveat: '{_caveat('detect_splitting')}...'" if fl else "not flagged")


@case("F2", "false alarm", "Repeat supply under a framework (KPLC)", "repeat supply of tools and equipment to one supplier may be call-offs under a framework agreement")
def f2():
    fl = [f for f in flags_of(tools.detect_splitting(supplier="Pureplus Solutions", limit=10)) if "Pureplus" in f["summary"] or "PUREPLUS" in f["summary"].upper()]
    return not fl, (f"FLAGGED (false-alarm risk): {fl[0]['summary'][:110]}; the tool cannot see a framework agreement" if fl else "not flagged")


@case("F3", "false alarm", "Direct procurement by a single-source buyer (Kenya Airport Authority)", "a high direct-procurement share can be lawful (emergency, single source)")
def f3():
    r = tools.detect_noncompetitive_method(buyer="Kenya Airport Authority")["result"]
    flagged = bool(r.get("flags"))
    caveat = bool(r["flags"] and r["flags"][0]["detail"]["signals"][0].get("caveat")) if flagged else False
    return not flagged, (f"FLAGGED ({r['buyer_stats']['ratio_to_baseline']}x the baseline); caveat on the flag: {caveat}" if flagged else "not flagged")


def run_all() -> list[dict[str, Any]]:
    results = []
    for cid, category, title, expectation, fn in CASES:
        started = time.time()
        try:
            ok, observed = fn()
            status, note = ("PASS" if ok else "FAIL"), observed
        except Exception as exc:  # noqa: BLE001 - a crashing case is a failing case, with its error shown
            status, note = "ERROR", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-400:]}"
        results.append({"id": cid, "category": category, "title": title, "expectation": expectation, "status": status,
                        "observed": note, "seconds": round(time.time() - started, 1)})
        print(f"{cid:4} {status:5} {title}: {note[:100]}", flush=True)
    return results


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    results = run_all()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"run_at": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}, indent=1, ensure_ascii=False), encoding="utf-8")
    tally = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "ERROR")}
    print(f"\n{tally} -> {OUT}")


if __name__ == "__main__":
    main()
