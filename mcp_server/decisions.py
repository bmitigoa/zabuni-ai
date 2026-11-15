"""Decision memory as MCP resources (read-only): what the human reviewers decided, with their reasons.

    decisions://run/{run_id}    every flag of a run with its decision history and clarification rounds
    decisions://flag/{flag_id}  the same for one flag

Each decision lists the reviewer ROLE (never a name), the decision (approve / reject / clarify), the reason, the time and the status the
flag moved to; each flag lists the ocids of the OCDS records it cites, so a reader can go from a decision to the source records
(`ocds://release/{ocid}`). The flags database is opened READ-ONLY here; a missing database is an empty answer, not an error, and
nothing is created. Why a resource and not a tool: it is context for the client (what was already decided and why), not an action.
"""
from __future__ import annotations

from typing import Any

from mcp_server import flagstore
from mcp_server.common import ToolInputError, clean_text, rules

DECISION_COLUMNS = ["decision_id", "decided_at", "reviewer_role", "decision", "reason", "status_after"]
NOTE = ("Reviewer roles, decisions and reasons as recorded by the human gate. Read-only. Rejected flags are remembered and not "
        "raised again; every cited ocid resolves with ocds://release/{ocid}.")


def _decisions(con: Any, flag_id: str) -> list[dict[str, Any]]:
    rows = con.execute(f"SELECT {', '.join(DECISION_COLUMNS)} FROM flag_decisions WHERE flag_id = ? ORDER BY decided_at, decision_id", [flag_id]).fetchall()
    return [{**dict(zip(DECISION_COLUMNS, r)), "decided_at": str(r[1])} for r in rows]


def _flag_entry(con: Any, flag: dict[str, Any]) -> dict[str, Any]:
    clar = [{"round": c["round"], "asked_at": c["asked_at"], "question": c["question"], "answer": c["response"],
             "answered_at": c["responded_at"], "new_evidence_ocids": sorted({e["ocid"] for e in c["evidence"]})}
            for c in flagstore.fetch_clarifications(con, flag["flag_id"])]
    return {"flag_id": flag["flag_id"], "rule": flag["rule"], "severity": flag["severity"], "status": flag["status"],
            "ocids": sorted({e["ocid"] for e in flag["evidence"]}), "decisions": _decisions(con, flag["flag_id"]), "clarifications": clar}


def run_decisions(run_id: str) -> dict[str, Any]:
    """All flags of a run with their decision history (capped at limits.decisions_listed_max flags, and the cap is announced)."""
    rid = clean_text(run_id, "run_id")
    if not rid:
        raise ToolInputError("run_id is empty.")
    cap = int(rules()["limits"]["decisions_listed_max"])
    with flagstore.connect(read_only=True) as con:
        if con is None:
            return {"run_id": rid, "n_flags": 0, "n_decisions": 0, "flags": [], "note": NOTE + " No flags database exists yet."}
        rows = con.execute(f"SELECT {', '.join(flagstore.COLUMNS)} FROM flags WHERE run_id = ? ORDER BY created_at, flag_id LIMIT ?", [rid, cap + 1]).fetchall()
        flags = [flagstore.row_to_flag(r) for r in rows]
        entries = [_flag_entry(con, f) for f in flags[:cap]]
    out: dict[str, Any] = {"run_id": rid, "n_flags": len(entries), "n_decisions": sum(len(e["decisions"]) for e in entries), "flags": entries, "note": NOTE}
    if len(flags) > cap:
        out["truncated"] = f"Only the first {cap} flags are listed."
    return out


def flag_decision_history(flag_id: str) -> dict[str, Any]:
    """One flag with its decision history; an unknown id is an error entry (nothing is invented)."""
    fid = clean_text(flag_id, "flag_id")
    if not fid:
        raise ToolInputError("flag_id is empty.")
    with flagstore.connect(read_only=True) as con:
        flag = flagstore.fetch_flag(con, fid) if con is not None else None
        if flag is None:
            return {"flag_id": fid, "error": f"No flag {fid!r} is stored.", "note": NOTE}
        return {**_flag_entry(con, flag), "run_id": flag["run_id"], "note": NOTE}
