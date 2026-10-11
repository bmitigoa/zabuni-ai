"""attach_clarification: record the agent's answer to a reviewer's question and return the flag to review (a write tool)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from mcp_server import flagstore
from mcp_server.common import ToolInputError, clean_text, envelope, rules
from mcp_server.registry import tool


@tool(read_only=False, role="clarification_writer")
def attach_clarification(flag_id: str, response: str, evidence: list[dict[str, Any]] | None = None,
                         run_id: str | None = None) -> dict[str, Any]:
    """Answer the reviewer's clarification question on a flag and put the flag back into review.

    Works only on a flag whose status is [[flags.status_clarify]] with an unanswered question. `response` is a plain
    statement of what the re-investigation found (at least [[flags.min_explanation_chars]] characters; it must not
    recommend or award anything). `evidence` is an optional list of {ocid, award_id, field, value} items from the tool
    results of the re-investigation; every cited award must exist in the OCDS data. The flag returns to
    [[flags.status_pending]] so the reviewer can decide again.
    """
    cfg = rules()
    fl = cfg["flags"]
    params = {"flag_id": flag_id, "run_id": run_id, "n_evidence": len(evidence or [])}
    text = (response or "").strip()
    if len(text) < int(fl["min_explanation_chars"]):
        raise ToolInputError(f"response must be at least {fl['min_explanation_chars']} characters.")
    new_evidence = flagstore.clean_evidence(evidence, required=False)
    if new_evidence:
        flagstore.verify_citations(new_evidence)
    fid = clean_text(flag_id)
    if not fid:
        raise ToolInputError("flag_id is empty.")
    with flagstore.connect() as con:
        flag = flagstore.fetch_flag(con, fid)
        if flag is None:
            raise ToolInputError(f"No flag {fid!r} is stored.")
        if flag["status"] != fl["status_clarify"]:
            raise ToolInputError(f"Flag {fid} is {flag['status']}; only a flag awaiting clarification can be answered.")
        open_q = con.execute("SELECT clarification_id, question FROM flag_clarifications WHERE flag_id = ? AND response IS NULL "
                             "ORDER BY round DESC LIMIT 1", [fid]).fetchone()
        if open_q is None:
            raise ToolInputError(f"Flag {fid} has no unanswered clarification question.")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        con.execute("UPDATE flag_clarifications SET response = ?, responded_at = ?, evidence_json = ? WHERE clarification_id = ?",
                    [text, now, json.dumps(new_evidence, default=str), open_q[0]])
        con.execute("UPDATE flags SET status = ? WHERE flag_id = ?", [fl["status_pending"], fid])
    result = {"flag_id": fid, "status": fl["status_pending"], "question": open_q[1], "new_evidence_items": len(new_evidence)}
    warnings = [] if new_evidence else ["The re-investigation found no additional evidence; the answer is recorded without any."]
    return envelope(result, flag["evidence"] + new_evidence, params, warnings)
