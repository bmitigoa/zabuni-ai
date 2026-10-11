"""list_flags: the reviewer's view of stored flags (read-only; carries a role so the Planner never schedules it)."""
from __future__ import annotations

from typing import Any

from mcp_server import flagstore
from mcp_server.common import ToolInputError, clean_text, envelope, rules
from mcp_server.registry import tool
from mcp_server.toolkit import clamp

STATUS_KEYS = ("status_pending", "status_approved", "status_rejected", "status_clarify")


def all_statuses(fl: dict[str, Any]) -> list[str]:
    return [fl[k] for k in STATUS_KEYS]


@tool(role="flag_reader")
def list_flags(run_id: str | None = None, status: str | None = None, flag_ids: list[str] | None = None,
               limit: int = 50) -> dict[str, Any]:
    """List stored flags with their evidence, decisions and clarification history, for human review.

    Filter by `run_id`, by `status` ([[flags.status_pending]], [[flags.status_approved]], [[flags.status_rejected]] or
    [[flags.status_clarify]]) or by explicit `flag_ids`. `limit` is 1-[[limits.search_max]]. Each flag carries its cited
    OCDS records, so every listed flag is sourced. Read-only.
    """
    cfg = rules()
    fl = cfg["flags"]
    params = {"run_id": run_id, "status": status, "flag_ids": flag_ids, "limit": limit}
    st = clean_text(status)
    if st and st not in all_statuses(fl):
        raise ToolInputError(f"status must be one of {all_statuses(fl)}.")
    limit = clamp(limit, 1, int(cfg["limits"]["search_max"]))
    where, args = [], []
    if clean_text(run_id):
        where.append("run_id = ?"); args.append(run_id.strip())
    if st:
        where.append("status = ?"); args.append(st)
    ids = [i.strip() for i in (flag_ids or []) if i and i.strip()]
    if ids:
        where.append(f"flag_id IN ({', '.join('?' for _ in ids)})"); args += ids
    sql = (f"SELECT {', '.join(flagstore.COLUMNS)} FROM flags" + (" WHERE " + " AND ".join(where) if where else "")
           + f" ORDER BY created_at, flag_id LIMIT {limit}")
    flags: list[dict[str, Any]] = []
    with flagstore.connect(read_only=True) as con:
        if con is not None:
            for row in con.execute(sql, args).fetchall():
                flag = flagstore.row_to_flag(row)
                flag["clarifications"] = flagstore.fetch_clarifications(con, flag["flag_id"])
                flags.append(flag)
    warnings = [] if flags else ["No flags match (or no flags have been stored yet)."]
    evidence = [e for f in flags for e in f["evidence"]]
    params.update(status=st, limit=limit)
    return envelope({"count": len(flags), "flags": flags}, evidence, params, warnings)
