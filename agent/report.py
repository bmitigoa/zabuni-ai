"""Draft summary (Markdown) of one run. Deterministic: the banner, the log and the limitations never depend on the LLM."""
from __future__ import annotations

import json
from typing import Any

BANNER = ("**DRAFT: pending human review.** Everything below is a screening indicator produced from published OCDS data. "
          "No flag is a finding of wrongdoing, and nothing here recommends or awards a tender; the committee decides.")

LIMITATIONS = [
    "Bid documents are not published, so bid content, scoring and bidder qualifications cannot be assessed.",
    "The data has no tenderers, tender estimates or item-level detail; price comparisons use title words, category and "
    "buyer type only, and a cluster of awards may be genuinely different goods.",
    "Award dates are contract signing dates; amounts outside the valid range are excluded from statistics.",
    "Supplier-based checks cover only awards that name a supplier. All thresholds are provisional and unvalidated.",
]


def _args(args: dict[str, Any], limit: int = 140) -> str:
    text = json.dumps(args, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def build_summary_md(state: dict[str, Any], run: dict[str, Any], awards_listed: int) -> str:
    """Markdown draft from the final graph state. `run` carries model name, token usage and timing."""
    lines = [f"# Draft investigation summary: {state.get('query', '')}", "", BANNER, "",
             f"Run `{state.get('run_id')}` | model `{run.get('model')}` | {run.get('llm_calls', 0)} LLM calls, "
             f"{run.get('tokens', 0)} tokens | {run.get('tool_calls', 0)} tool calls | {run.get('seconds', 0):.0f} s", ""]
    lines += ["## Summary", "", state.get("summary", ""), ""]
    if state.get("stopped_reason"):
        lines += [f"> **Investigation stopped early:** {state['stopped_reason']}. Checks not run cannot support a statement "
                  "that nothing is wrong.", ""]
    lines += ["## Plan", "", f"Objective: {state.get('plan', {}).get('objective', '')}", ""]
    for i, s in enumerate(state.get("plan", {}).get("steps", []), 1):
        lines.append(f"{i}. `{s['tool']}` {_args(s['args'])} — {s.get('reason', '')}")
    lines += ["", "## Investigation log", ""]
    for r in state.get("results", []):
        outcome = f"ERROR: {r['error']}" if r.get("error") else "ok"
        out = r.get("output") or {}
        flagged = len(((out.get("result") or {}).get("flags") or [])) if isinstance(out, dict) else 0
        lines.append(f"- `{r['tool']}` {_args(r['args'])} ({r.get('origin')}) → {outcome}; "
                     f"{len(out.get('evidence', [])) if isinstance(out, dict) else 0} evidence items, {flagged} flag(s)")
    if state.get("notes"):
        lines += ["", "### Follow-ups, recoveries and problems", ""] + [f"- {n}" for n in state["notes"]]
    lines += ["", "## Flags (all pending human review)", ""]
    created = state.get("flags_created", [])
    if not created:
        lines.append("No flags were created.")
    for f in created:
        lines += [f"### {f['flag_id']} — {f['rule']} — severity {f['severity']} — status {f['status']}", "",
                  f["explanation"], "", "Cited OCDS records:"]
        for e in f["evidence"][:awards_listed]:
            lines.append(f"- ocid `{e['ocid']}`, award `{e['award_id']}`, {e['field']} = {e['value']}")
        if len(f["evidence"]) > awards_listed:
            lines.append(f"- … and {len(f['evidence']) - awards_listed} more evidence item(s) stored with the flag")
        lines.append("")
    if state.get("dropped"):
        lines += ["## Dropped by the citation guard", ""]
        lines += [f"- {d['reason']}" + (f" ({', '.join(d['ocids'])})" if d.get("ocids") else "") for d in state["dropped"]]
        lines.append("")
    lines += ["## Limitations", ""] + [f"- {x}" for x in LIMITATIONS]
    return "\n".join(lines) + "\n"
