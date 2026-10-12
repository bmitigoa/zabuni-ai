"""Terminal review of a paused run:  python -m agent.review <run_id> [--role internal_auditor]

Lists each pending flag with its evidence and asks for a decision: approve / reject / clarify. A reason is required for
every decision; the reviewer is recorded by ROLE (never by name). Clarify sends the flag back to the agent with your
question, the agent re-investigates it, and the run pauses again with the answer attached. When every flag is decided the
run resumes, drafts the memo from the APPROVED flags only, and saves it through the Filesystem MCP server.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from typing import Any, Callable

from agent.graph import pause_payload, pending_payload, resume_agent
from agent.llm import LLMError
from agent.run import Printer
from agent.session import log_run_usage, open_session
from agent.settings import SettingsError, load_settings
from mcp_server.fmt import fmt_value, kes

DECISION_KEYS = {"a": "approve", "r": "reject", "c": "clarify"}


def show_flag(flag: dict[str, Any]) -> str:
    """Plain-text view of one pending flag: the flagged award(s), the signals with their provisional thresholds, any data-entry
    hint, the cited records and earlier clarifications."""
    detail = flag.get("detail") or {}
    subject = detail.get("subject") or {}
    lines = [f"\n=== {flag['flag_id']}  [{flag['severity']}]  {flag['rule']}", f"    {flag['explanation']}"]
    for a in subject.get("awards") or []:
        lines.append(f"    flagged award: {a.get('buyer')} | {a.get('supplier')} | {a.get('date')} | "
                     f"{kes(a['amount_kes']) if a.get('amount_kes') is not None else 'no amount'} | {a['ocid']}")
    if subject.get("name"):
        lines.append(f"    flagged {subject.get('kind')}: {subject['name']}")
    for sg in detail.get("signals") or []:
        comp = sg.get("comparison") or {}
        lines.append(f"    signal: {sg.get('metric') or sg['summary']} [{sg.get('threshold') or 'threshold n/a'}]"
                     + (f" compared against {comp['n']:,} {comp['label']}" if comp.get("n") else ""))
    if detail.get("comparison_note"):
        lines.append(f"    note: {detail['comparison_note']}")
    lines += [f"    HINT: {h}" for h in detail.get("hints") or []]
    lines.append(f"    cited records ({flag['n_evidence']} evidence item(s), first {len(flag['evidence'])} shown):")
    for e in flag["evidence"]:
        lines.append(f"      ocid {e['ocid']} | award {e['award_id']} | {e['field']} = {fmt_value(e['field'], e['value'])}")
    for c in flag.get("clarifications", []):
        lines.append(f"    earlier question: {c.get('question')}\n    answer from the agent: {c.get('response')}")
    return "\n".join(lines)


def collect_decisions(payload: dict[str, Any], ask: Callable[[str], str], role: str, min_reason: int) -> list[dict[str, Any]]:
    """Ask for one decision per pending flag. `ask` is input() in the terminal and a scripted function in tests.
    Skipping a flag leaves it undecided (the run stays paused for it)."""
    out = []
    for flag in payload["flags"]:
        print(show_flag(flag))
        while True:
            key = ask("  decision [a]pprove / [r]eject / [c]larify / [s]kip: ").strip().lower()[:1]
            if key in ("s", ""):
                break
            if key not in DECISION_KEYS:
                print("  please answer a, r, c or s")
                continue
            decision = DECISION_KEYS[key]
            label = "your question for the agent" if decision == "clarify" else "reason"
            reason = ask(f"  {label} (required, at least {min_reason} characters): ").strip()
            if len(reason) < min_reason:
                print(f"  a {label} of at least {min_reason} characters is required")
                continue
            out.append({"flag_id": flag["flag_id"], "decision": decision, "reason": reason, "reviewer_role": role})
            break
    return out


async def amain(argv: list[str] | None = None, ask: Callable[[str], str] = input) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent.review", description="Review the flags of a paused Zabuni AI run.")
    ap.add_argument("run_id")
    ap.add_argument("--role", default="internal_auditor", help="your role (recorded instead of your name)")
    args = ap.parse_args(argv)
    try:
        settings = load_settings()
    except SettingsError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    started = time.time()
    async with open_session(settings, args.run_id, echo=Printer().echo, resume=True) as s:
        roles, min_reason = s.rules["review"]["roles"], int(s.rules["review"]["min_reason_chars"])
        if args.role not in roles:
            print(f"Unknown role {args.role!r}. Choose one of {roles}.", file=sys.stderr)
            return 2
        payload = await pending_payload(s.ctx, s.checkpointer)
        if payload is None:
            print(f"Run {args.run_id} is not waiting for review (unknown run, or already finished).", file=sys.stderr)
            return 1
        print(f"Reviewing run {args.run_id}: {payload.get('query')}\nYou are recorded as: {args.role}")
        state: dict[str, Any] = {}
        try:
            while payload is not None:
                decisions = collect_decisions(payload, ask, args.role, min_reason)
                if not decisions:
                    print("\nNo decisions entered; the run stays paused. Nothing enters the memo.")
                    return 0
                state = await resume_agent(s.ctx, decisions, s.checkpointer)
                payload = pause_payload(state)
                if payload is not None:
                    print("\nThe run paused again: flags remain to decide (a clarification answer may be attached).")
        except LLMError as exc:
            print(f"\nThe run could not continue: {exc}", file=sys.stderr)
            return 1
        usage = log_run_usage(s, time.time() - started)
        print(f"\nREVIEW COMPLETE. Memo: {state.get('memo_path') or '(not saved)'}")
        print(f"  decisions: {[(d['flag_id'], d['decision']) for d in state.get('decisions', [])]}")
        print(f"  run total so far: {usage['tokens']} tokens, est. cost ${usage['cost_usd']:.4f}")
    return 0


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(asyncio.run(amain()))


if __name__ == "__main__":
    main()
