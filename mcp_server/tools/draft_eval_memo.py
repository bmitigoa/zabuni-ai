"""draft_eval_memo: build the evaluation memo from APPROVED flags only and store it (a substantive write/generate tool).

The memo lists each approved finding with its cited OCDS records, the reviewer's decision and reason (by role), the data
limitations, any note about how the run ended (budget, quota, errors), and the statement that it recommends and awards
nothing. Flags that are rejected, pending or awaiting clarification are never part of it: they are only counted.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from mcp_server import flagstore
from mcp_server.common import ToolInputError, clean_text, envelope, rules
from mcp_server.registry import tool

TIMESTAMP_CHARS = 19        # 'YYYY-MM-DD HH:MM:SS'


def _decision_history(con: Any, flag_id: str) -> list[tuple[Any, ...]]:
    return con.execute("SELECT decided_at, decision, reason, reviewer_role FROM flag_decisions WHERE flag_id = ? "
                       "ORDER BY decided_at", [flag_id]).fetchall()


def build_memo(approved: list[dict[str, Any]], counts: dict[str, int], con: Any, title: str, run_id: str | None,
               notes: list[str], memo_cfg: dict[str, Any], today: str) -> str:
    """Render the memo Markdown. Pure of side effects; every finding comes from an approved flag."""
    listed = int(memo_cfg["evidence_listed_per_flag"])
    out = [f"# Evaluation memo: {title}", "", f"> {memo_cfg['disclaimer']}", ""]
    out += [f"Date: {today}" + (f" | Run: `{run_id}`" if run_id else ""), ""]
    n = len(approved)
    reviewed = sum(counts.values())
    out += ["## Summary", ""]
    if n:
        by_rule: dict[str, int] = {}
        for f in approved:
            by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
        out.append(f"{n} finding(s) were approved by human reviewers out of {reviewed} flag(s) considered "
                   f"({counts.get('rejected', 0)} rejected, {counts.get('pending_review', 0) + counts.get('clarification_requested', 0)} "
                   "not yet decided; those are not part of this memo). Approved findings by check: "
                   + ", ".join(f"{r} x{c}" for r, c in sorted(by_rule.items())) + ".")
    else:
        out.append(f"No finding was approved ({reviewed} flag(s) considered). This memo therefore contains no findings; that is "
                   "not evidence that nothing is wrong.")
    if notes:
        out += ["", "## How this run ended", ""] + [f"- {x}" for x in notes]
    out += ["", "## Approved findings", ""]
    for i, f in enumerate(approved, 1):
        out += [f"### {i}. {f['rule']} (severity {f['severity']}), flag `{f['flag_id']}`", "", f["explanation"], "",
                "Cited OCDS records:"]
        for e in f["evidence"][:listed]:
            out.append(f"- ocid `{e['ocid']}`, award `{e['award_id']}`, {e['field']} = {e['value']}")
        if len(f["evidence"]) > listed:
            out.append(f"- ... and {len(f['evidence']) - listed} more cited record(s) stored with the flag")
        out += ["", "Reviewer decisions:"]
        for decided_at, decision, reason, role in _decision_history(con, f["flag_id"]):
            out.append(f"- {str(decided_at)[:TIMESTAMP_CHARS]} UTC, {role}: **{decision}**. Reason: {reason}")
        for c in flagstore.fetch_clarifications(con, f["flag_id"]):
            out.append(f"- Clarification round {c['round']}: question \"{c['question']}\"; answer: "
                       f"{c['response'] or '(not answered)'}")
        out.append("")
    if not approved:
        out += ["(none)", ""]
    out += ["## Data limitations", ""] + [f"- {x}" for x in memo_cfg["limitations"]]
    out += ["", "## Statement", "", memo_cfg["disclaimer"], ""]
    return "\n".join(out)


@tool(read_only=False, role="memo_writer")
def draft_eval_memo(run_id: str | None = None, flag_ids: list[str] | None = None, run_notes: list[str] | None = None,
                    title: str | None = None) -> dict[str, Any]:
    """Draft the evaluation memo from APPROVED flags only, and store it.

    Select the flags of a run with `run_id`, or explicitly with `flag_ids` (at least one is required). Only flags whose
    status is [[flags.status_approved]] enter the memo; rejected, pending and clarification-requested flags are only
    counted, never listed. Each finding shows its cited OCDS records (up to [[memo.evidence_listed_per_flag]]), the
    reviewer decisions with reasons (by role) and any clarification. `run_notes` are sentences about how the run ended
    (budget stop, quota, tool errors) and appear in the memo. The memo always states the data limitations and that it does
    not recommend or award any tender. Returns the memo Markdown to save (for example through the Filesystem MCP server).
    """
    cfg = rules()
    fl, memo_cfg = cfg["flags"], cfg["memo"]
    params = {"run_id": run_id, "flag_ids": flag_ids, "run_notes": run_notes, "title": title}
    ids = [i.strip() for i in (flag_ids or []) if i and i.strip()]
    rid = clean_text(run_id)
    if not ids and not rid:
        raise ToolInputError("Provide run_id or flag_ids: the memo needs to know which flags to consider.")
    where, args = [], []
    if ids:
        where.append(f"flag_id IN ({', '.join('?' for _ in ids)})"); args += ids
    else:
        where.append("run_id = ?"); args.append(rid)
    notes = [n.strip() for n in (run_notes or []) if n and n.strip()]
    with flagstore.connect() as con:
        rows_ = con.execute(f"SELECT {', '.join(flagstore.COLUMNS)} FROM flags WHERE {' AND '.join(where)} "
                            "ORDER BY created_at, flag_id", args).fetchall()
        flags = [flagstore.row_to_flag(r) for r in rows_]
        counts: dict[str, int] = {}
        for f in flags:
            counts[f["status"]] = counts.get(f["status"], 0) + 1
        approved = [f for f in flags if f["status"] == fl["status_approved"]]
        text_title = clean_text(title) or (f"run {rid}" if rid else "selected flags")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        markdown = build_memo(approved, counts, con, text_title, rid, notes, memo_cfg, today)
        memo_id = "memo_" + uuid.uuid4().hex[:flagstore.ID_HEX_CHARS]
        digest = hashlib.sha1(markdown.encode("utf-8")).hexdigest()
        con.execute("INSERT INTO memos VALUES (?, ?, ?, ?, ?, ?)",
                    [memo_id, rid, datetime.now(timezone.utc).replace(tzinfo=None),
                     json.dumps([f["flag_id"] for f in approved]), markdown, digest])
    warnings = []
    if not flags:
        warnings.append("No flags matched: the memo contains no findings.")
    elif not approved:
        warnings.append("No flag is approved: the memo contains no findings.")
    excluded = len(flags) - len(approved)
    if excluded:
        warnings.append(f"{excluded} flag(s) are not approved and were left out of the memo (only counted).")
    result = {"memo_id": memo_id, "markdown": markdown, "sha1": digest, "n_approved": len(approved),
              "n_considered": len(flags), "counts": counts, "flag_ids": [f["flag_id"] for f in approved]}
    evidence = [e for f in approved for e in f["evidence"]]
    return envelope(result, evidence, params, warnings)
