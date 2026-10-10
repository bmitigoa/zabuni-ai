"""search_awards: exploration tool. Finds awards and the ocid/award_id values the other tools need."""
from __future__ import annotations

from typing import Any

from mcp_server.common import (ToolInputError, base_cte, clean_date, clean_text, envelope, ev, norm, rows, rules)
from mcp_server.registry import tool
from mcp_server.toolkit import award_row, clamp

SORTS = ("newest", "largest")


@tool()
def search_awards(buyer: str | None = None, supplier: str | None = None, title_keyword: str | None = None,
                  date_from: str | None = None, date_to: str | None = None, limit: int = 20,
                  sort_by: str = "newest") -> dict[str, Any]:
    """Find awards in the frozen PPRA Kenya OCDS snapshot. Use this first to explore and to get ocid/award_id values.

    At least one filter is required. `buyer` and `supplier` are case-insensitive partial company/organisation
    names (matched on normalised names); `title_keyword` is a word or phrase in the award title;
    `date_from`/`date_to` are YYYY-MM-DD bounds on the contract signing date (the snapshot has no award decision
    date; awards with no valid date are excluded when a date filter is used). `limit` is 1-[[limits.search_max]]. `sort_by` is `newest` (default) or `largest` (by amount, valid amounts first): use `largest` to pick
    the awards most worth price-checking. Returns matching awards with amount in KES, procurement method and the total
    match count. Each row is backed by an evidence item pointing at its OCDS award. Awards with amount <= 0 or above
    [[data_cleaning.max_valid_amount_kes|kes]] are shown but marked amount_valid=false (probable data-entry errors;
    excluded from statistics elsewhere).
    """
    cfg = rules()
    params = {"buyer": buyer, "supplier": supplier, "title_keyword": title_keyword,
              "date_from": date_from, "date_to": date_to, "limit": limit, "sort_by": sort_by}
    sort = (sort_by or "newest").strip().lower()
    if sort not in SORTS:
        raise ToolInputError(f"sort_by must be one of {list(SORTS)}.")
    b, s, kw = clean_text(buyer), clean_text(supplier), clean_text(title_keyword)
    d_from, d_to = clean_date(date_from, "date_from"), clean_date(date_to, "date_to")
    if not any([b, s, kw, d_from, d_to]):
        raise ToolInputError("Provide at least one filter: buyer, supplier, title_keyword, date_from or date_to.")
    limit_max = int(cfg["limits"]["search_max"])
    limit = clamp(limit, 1, limit_max)
    conds, args = [], []
    for value, col, label in [(b, "buyer_norm", "buyer"), (s, "supplier_norm", "supplier")]:
        if value:
            n = norm(value)
            if not n:
                raise ToolInputError(f"{label} {value!r} has no searchable characters.")
            conds.append(f"{col} LIKE ?")
            args.append(f"%{n}%")
    if kw:
        conds.append("contains(lower(title), lower(?))")
        args.append(kw)
    if d_from:
        conds.append("award_date >= CAST(? AS TIMESTAMP)")
        args.append(d_from)
    if d_to:
        conds.append("award_date < CAST(? AS TIMESTAMP) + INTERVAL 1 DAY")
        args.append(d_to)
    where = " AND ".join(conds)
    cte = base_cte(valid_only=False)
    order = ("amount DESC NULLS LAST, award_date DESC NULLS LAST" if sort == "largest"
             else "award_date DESC NULLS LAST, raw_amount DESC")
    total = rows(f"WITH {cte} SELECT COUNT(*) AS n FROM a WHERE {where}", args)[0]["n"]
    found = rows(f"WITH {cte} SELECT * FROM a WHERE {where} ORDER BY {order} LIMIT {limit}", args)
    warnings = []
    if total == 0:
        warnings.append("No awards matched. Try fewer filters or a shorter keyword (names are matched as partial text).")
    if total > limit:
        warnings.append(f"Showing {len(found)} of {total} matches; narrow the filters or raise limit (max {limit_max}).")
    out_rows, evidence = [], []
    for r in found:
        row = award_row(r)
        row["amount_valid"] = r["amount"] is not None
        out_rows.append(row)
        evidence.append(ev(r["ocid"], r["award_id"], "award_amount", r["raw_amount"]))
    params.update(limit=limit, date_from=d_from, date_to=d_to, sort_by=sort)
    return envelope({"total_matches": total, "returned": len(out_rows), "awards": out_rows}, evidence, params, warnings)
