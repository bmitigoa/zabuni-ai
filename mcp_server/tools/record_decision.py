"""record_decision: store a human reviewer's decision on a flag (a write tool).

Decisions are the gate between "the agent found something" and "it may enter the memo": approve, reject or clarify,
with a reason, recorded by reviewer ROLE (never a name). History is append-only; the flag's status is updated.
Only a flag that is pending review (or awaiting clarification) can be decided: a rejected or approved flag is final here.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from mcp_server import flagstore
from mcp_server.common import ToolInputError, clean_text, envelope, rules
from mcp_server.registry import tool

DECISION_TO_STATUS = {"approve": "status_approved", "reject": "status_rejected", "clarify": "status_clarify"}


@tool(read_only=False, role="decision_writer")
def record_decision(flag_id: str, decision: str, reason: str, reviewer_role: str,
                    run_id: str | None = None) -> dict[str, Any]:
    """Record a human reviewer's decision on a flag. Only a reviewer (never the agent) may decide.

    `decision` is one of [[review.decisions|list]]: approve (the flag may enter the memo), reject (it is remembered and
    not re-raised) or clarify (the agent re-investigates with `reason` as the reviewer's question; at most
    [[review.max_clarify_rounds]] rounds). `reason` is required (at least [[review.min_reason_chars]] characters).
    `reviewer_role` is one of [[review.roles|list]]: reviewers are recorded by role, never by name. Works only on a flag
    that is pending review or awaiting clarification. The decision history is append-only.
    """
    cfg = rules()
    rv, fl = cfg["review"], cfg["flags"]
    params = {"flag_id": flag_id, "decision": decision, "reviewer_role": reviewer_role, "run_id": run_id}
    dec = (decision or "").strip().lower()
    if dec not in rv["decisions"]:
        raise ToolInputError(f"decision must be one of {rv['decisions']}.")
    why = (reason or "").strip()
    if len(why) < int(rv["min_reason_chars"]):
        raise ToolInputError(f"a reason is required (at least {rv['min_reason_chars']} characters).")
    role = (reviewer_role or "").strip().lower()
    if role not in rv["roles"]:
        raise ToolInputError(f"reviewer_role must be one of {rv['roles']} (reviewers are recorded by role, not by name).")
    fid = clean_text(flag_id)
    if not fid:
        raise ToolInputError("flag_id is empty.")
    open_states = [fl["status_pending"], fl["status_clarify"]]
    with flagstore.connect() as con:
        flag = flagstore.fetch_flag(con, fid)
        if flag is None:
            raise ToolInputError(f"No flag {fid!r} is stored.")
        if flag["status"] not in open_states:
            raise ToolInputError(f"Flag {fid} is already {flag['status']}; only a flag that is {' or '.join(open_states)} can be decided.")
        if dec == "clarify" and flag["clarification_rounds"] >= int(rv["max_clarify_rounds"]):
            raise ToolInputError(f"Flag {fid} has used its {rv['max_clarify_rounds']} clarification round(s): approve or reject it.")
        status_after = fl[DECISION_TO_STATUS[dec]]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        decision_id = "dec_" + uuid.uuid4().hex[:flagstore.ID_HEX_CHARS]
        con.execute("INSERT INTO flag_decisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [decision_id, fid, run_id, now, dec, why, role, status_after])
        con.execute("UPDATE flags SET status = ?, reviewer = ?, decision_reason = ?, decided_at = ? WHERE flag_id = ?",
                    [status_after, role, why, now, fid])
        if dec == "clarify":
            rounds = flag["clarification_rounds"] + 1
            con.execute("UPDATE flags SET clarification_rounds = ? WHERE flag_id = ?", [rounds, fid])
            con.execute("INSERT INTO flag_clarifications (clarification_id, flag_id, run_id, round, asked_at, question) "
                        "VALUES (?, ?, ?, ?, ?, ?)", ["clar_" + uuid.uuid4().hex[:flagstore.ID_HEX_CHARS], fid, run_id, rounds, now, why])
    params.update(decision=dec, reviewer_role=role)
    result = {"decision_id": decision_id, "flag_id": fid, "decision": dec, "status": status_after, "reviewer_role": role}
    return envelope(result, flag["evidence"], params, [])
