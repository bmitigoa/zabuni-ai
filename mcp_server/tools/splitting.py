"""detect_splitting: several awards from one buyer to one supplier, each just below a threshold, together above it."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from mcp_server.common import (ToolInputError, base_cte, buyer_problem, clean_text, clean_years, envelope, ev,
                               evidence_cap, frame, norm, rows, rules, year_filter)
from mcp_server.registry import tool
from mcp_server.toolkit import clamp, date_text, make_flag, ranking_limit, severity_hint

SECONDS_PER_DAY = 86_400.0
EPOCH = pd.Timestamp("1970-01-01")
EVIDENCE_PER_AWARD = 2          # award_amount + contract_date_signed


def _split_clusters(g: pd.DataFrame, thresholds: list[float], near: float, window: int, min_n: int) -> list[dict[str, Any]]:
    """Find clusters inside one (buyer, supplier) group already sorted by date (column `_day` = days since epoch):
    >= min_n awards within `window` days whose amounts are all in [near*T, T) for some threshold T and whose total
    reaches T. Numpy-only inner loop: this runs for thousands of groups in a whole-dataset scan."""
    amt, day = g["amount"].to_numpy(dtype=float), g["_day"].to_numpy(dtype=float)
    used = np.zeros(len(g), dtype=bool)
    out = []
    for T in thresholds:
        idx = np.flatnonzero((amt >= near * T) & (amt < T) & ~used)
        i = 0
        while i < len(idx):
            j = i
            while j + 1 < len(idx) and day[idx[j + 1]] - day[idx[i]] <= window:
                j += 1
            sel = idx[i:j + 1]
            if len(sel) >= min_n and amt[sel].sum() >= T:
                used[sel] = True
                out.append({"threshold_kes": T, "sub": g.iloc[sel]})
                i = j + 1
            else:
                i += 1
    return out


@tool()
def detect_splitting(buyer: str | None = None, supplier: str | None = None, window_days: int | None = None,
                     min_awards: int | None = None, thresholds_kes: list[float] | None = None,
                     years: list[int] | None = None, limit: int = 10) -> dict[str, Any]:
    """Look for possible contract splitting: several awards from the SAME buyer to the SAME supplier within a short
    window, each just below an approval threshold, whose combined total exceeds that threshold.

    `buyer` (organisation name; must identify one buyer) and/or `supplier` (partial name) narrow the scan; with
    neither, all buyers are scanned and the largest clusters returned. `window_days` (default [[splitting.window_days]],
    at most [[splitting.window_days_max]]), `min_awards` (default [[splitting.min_awards]]), `thresholds_kes` (default
    list of illustrative KES thresholds, provisional and unverified: [[splitting.thresholds_kes|list]]), `years` (list
    of calendar years) and `limit` (1-[[limits.ranking_max]] clusters) are configurable. An award is
    'just below' threshold T when [[splitting.near_below_ratio]]*T <= amount < T. Only awards with a named supplier, a
    valid date and a valid amount are examined (coverage is reported in warnings). The snapshot has no item data, so
    this cannot confirm the awards were for the same goods; a cluster is a prompt for human review, not proof of
    splitting. Each cluster lists its awards (ocid, award_id, date, amount), each award is cited in evidence, and each
    cluster adds an entry to result.flags.
    """
    cfg = rules()
    sp = cfg["splitting"]
    window = clamp(window_days if window_days is not None else sp["window_days"], 1, int(sp["window_days_max"]))
    min_n = clamp(min_awards if min_awards is not None else sp["min_awards"],
                  int(sp["min_awards_floor"]), int(sp["min_awards_max"]))
    thresholds = sorted({float(t) for t in (thresholds_kes or sp["thresholds_kes"]) if t and float(t) > 0})
    if not thresholds:
        raise ToolInputError("thresholds_kes must contain at least one positive number.")
    limit = ranking_limit(limit)
    yrs = clean_years(years, cfg["data_cleaning"])
    params = {"buyer": buyer, "supplier": supplier, "window_days": window, "min_awards": min_n,
              "thresholds_kes": thresholds, "years": yrs, "near_below_ratio": sp["near_below_ratio"], "limit": limit}
    warnings: list[str] = []
    conds, args = [], []
    b = clean_text(buyer)
    if b:
        bn, problem = buyer_problem(b, params)
        if problem:
            return problem
        conds.append("buyer_norm = ?"); args.append(bn)
        params["buyer_resolved"] = bn
    s = clean_text(supplier)
    if s:
        sn = norm(s)
        if not sn:
            raise ToolInputError(f"supplier {s!r} has no searchable characters.")
        conds.append("supplier_norm LIKE ?"); args.append(f"%{sn}%")
    if not (b or s):
        warnings.append("No buyer or supplier given: scanned all buyers; showing the largest clusters only.")
    scope = (" AND " + " AND ".join(conds) if conds else "") + year_filter(yrs)
    cte = base_cte(valid_only=True)
    cov = rows(f"WITH {cte} SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE supplier_norm IS NULL) AS no_supplier, "
               f"COUNT(*) FILTER (WHERE award_date IS NULL) AS no_date FROM a WHERE TRUE{scope}", args)[0]
    if cov["n"] == 0:
        warnings.append("No valid awards in scope (check buyer/supplier/years).")
        return envelope({"clusters": [], "total_clusters": 0, "flags": []}, [], params, warnings)
    if cov["no_supplier"] or cov["no_date"]:
        warnings.append(f"Of {cov['n']} valid-amount awards in scope, {cov['no_supplier']} have no supplier name and "
                        f"{cov['no_date']} have no valid date; they cannot be checked.")
    df = frame(f"WITH {cte} SELECT ocid, award_id, award_date, amount, title, buyer_name, buyer_norm, supplier_name, "
               f"supplier_norm FROM a WHERE supplier_norm IS NOT NULL AND award_date IS NOT NULL{scope}", args)
    # The same contract is sometimes published twice under different ocids: collapse exact duplicates
    # (same buyer, supplier, title, amount, date) so they are not counted as separate split awards.
    before = len(df)
    df = df.assign(_t=df["title"].fillna("").str.lower().str.strip()).drop_duplicates(
        ["buyer_norm", "supplier_norm", "_t", "amount", "award_date"]).drop(columns="_t")
    if before - len(df):
        warnings.append(f"{before - len(df)} exact-duplicate award records (same buyer, supplier, title, amount and "
                        "date) were collapsed before clustering; they look like duplicate publications of one contract.")
    df = df.assign(_day=(df["award_date"] - EPOCH).dt.total_seconds() / SECONDS_PER_DAY
                   ).sort_values(["buyer_norm", "supplier_norm", "award_date"])
    clusters = []
    big = df[df.groupby(["buyer_norm", "supplier_norm"])["ocid"].transform("size") >= min_n]
    for _, g in big.groupby(["buyer_norm", "supplier_norm"]):
        for c in _split_clusters(g, thresholds, sp["near_below_ratio"], window, min_n):
            sub = c["sub"]
            clusters.append({
                "buyer": sub["buyer_name"].iloc[0], "supplier": sub["supplier_name"].iloc[0],
                "threshold_kes": c["threshold_kes"], "n_awards": len(sub),
                "total_kes": float(sub["amount"].sum()),
                "total_over_threshold": round(float(sub["amount"].sum()) / c["threshold_kes"], 2),
                "span_days": int((sub["award_date"].max() - sub["award_date"].min()).days),
                "first_date": date_text(sub["award_date"].min()),
                "last_date": date_text(sub["award_date"].max()),
                "awards": [{"ocid": r.ocid, "award_id": r.award_id, "title": r.title,
                            "award_date": date_text(r.award_date), "amount_kes": float(r.amount)}
                           for r in sub.itertuples()],
            })
    clusters.sort(key=lambda c: -c["total_kes"])
    total = len(clusters)
    kept, evidence, flags = [], [], []
    cap = evidence_cap()
    for c in clusters[:limit]:
        if len(evidence) + EVIDENCE_PER_AWARD * c["n_awards"] > cap:
            warnings.append("Evidence cap reached; remaining clusters omitted. Narrow the scan (buyer/supplier/years).")
            break
        kept.append(c)
        cluster_ev = []
        for a in c["awards"]:
            cluster_ev.append(ev(a["ocid"], a["award_id"], "award_amount", a["amount_kes"]))
            cluster_ev.append(ev(a["ocid"], a["award_id"], "contract_date_signed", a["award_date"]))
        evidence += cluster_ev
        flags.append(make_flag(
            "detect_splitting",
            f"{c['n_awards']} awards from {c['buyer'].strip()} to {c['supplier']} within {c['span_days']} days, each just "
            f"below KES {c['threshold_kes']:,.0f}, totalling KES {c['total_kes']:,.0f} "
            f"({c['total_over_threshold']}x the threshold)",
            severity_hint(c["total_over_threshold"], 1.0), cluster_ev))
    if total == 0:
        warnings.append("No splitting pattern found with these parameters (this is not proof that none exists).")
    elif total > len(kept):
        warnings.append(f"Showing {len(kept)} of {total} clusters (largest first).")
    return envelope({"total_clusters": total, "clusters": kept, "flags": flags}, evidence, params, warnings)
