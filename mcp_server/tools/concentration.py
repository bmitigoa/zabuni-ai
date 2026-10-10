"""supplier_concentration: how concentrated a buyer's awards are among suppliers (shares + HHI)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from mcp_server.common import (base_cte, buyer_problem, clean_text, clean_years, envelope, ev, frame, rules, year_filter)
from mcp_server.registry import tool
from mcp_server.toolkit import make_flag, ranking_limit, severity_hint

PERCENT = 100          # HHI is defined on shares expressed in percent (0-10,000)


def _concentration(sub: pd.DataFrame) -> dict[str, Any]:
    """Per-supplier counts, values and shares for one buyer's supplier-named awards, plus HHI by value and by count."""
    n = len(sub)
    val_total = float(sub["amount"].sum())
    g = sub.groupby("supplier_norm").agg(supplier=("supplier_name", "first"), n=("ocid", "size"),
                                         value=("amount", "sum")).reset_index()
    g["share_count"] = g["n"] / n
    g["share_value"] = g["value"] / val_total if val_total else 0.0
    g = g.sort_values(["share_value", "n"], ascending=False)
    return {"n_awards": n, "n_suppliers": len(g), "total_value_kes": val_total,
            "hhi_value": round(float(((g["share_value"] * PERCENT) ** 2).sum()), 0) if val_total else None,
            "hhi_count": round(float(((g["share_count"] * PERCENT) ** 2).sum()), 0),
            "table": g}


@tool()
def supplier_concentration(buyer: str | None = None, years: list[int] | None = None,
                           limit: int = 10) -> dict[str, Any]:
    """Show how concentrated a buyer's awards are among suppliers (dependence on one or a few suppliers).

    `buyer` is an organisation name that must identify one buyer; omit it to rank buyers (with >=
    [[supplier_concentration.min_awards]] supplier-named awards) by their largest supplier's share. `years` is a list of
    calendar years ([[data_cleaning.min_year]]-[[data_cleaning.max_year]]; awards with no valid date are then
    excluded); `limit` (1-[[limits.ranking_max]]) caps the suppliers listed per buyer or the buyers ranked. Shares are
    computed only over awards that have a supplier name (coverage is reported) and by both award count and value (KES,
    valid amounts only). Also returns Herfindahl-Hirschman indices (0-10,000; higher = more concentrated).
    flagged=true means the top supplier holds >= [[supplier_concentration.flag_share|pct]] by count or value among >=
    [[supplier_concentration.min_awards]] named awards; it adds an entry to result.flags. Concentration can have
    legitimate causes (specialised goods, framework agreements): a prompt for human review. Each listed supplier is
    backed by evidence pointing at its largest awards.
    """
    cfg = rules()
    sc, ev_cfg = cfg["supplier_concentration"], cfg["evidence"]
    yrs = clean_years(years, cfg["data_cleaning"])
    limit = ranking_limit(limit)
    params = {"buyer": buyer, "years": yrs, "limit": limit, "min_awards": sc["min_awards"], "flag_share": sc["flag_share"]}
    warnings = ["Years filter excludes awards with no valid date."] if yrs else []
    cte = base_cte(valid_only=False)
    b = clean_text(buyer)
    args: list[Any] = []
    scope = year_filter(yrs)
    if b:
        bn, problem = buyer_problem(b, params)
        if problem:
            return problem
        params["buyer_resolved"] = bn
        scope += " AND buyer_norm = ?"; args.append(bn)
    df = frame(f"WITH {cte} SELECT ocid, award_id, buyer_norm, buyer_name, supplier_norm, supplier_name, amount, raw_amount "
               f"FROM a WHERE TRUE{scope}", args)
    if df.empty:
        warnings.append("No awards in scope.")
        return envelope({"buyers": [], "flags": []} if not b else {"buyer": b, "suppliers": [], "flags": []},
                        [], params, warnings)
    named = df[df["supplier_norm"].notna()].copy()
    named["amount"] = named["amount"].fillna(0.0)

    def judge(c: dict[str, Any]) -> tuple[float, float, bool]:
        t = c["table"]
        top_count, top_value = float(t["share_count"].max()), float(t["share_value"].max())
        return top_count, top_value, bool(c["n_awards"] >= sc["min_awards"]
                                          and (top_count >= sc["flag_share"] or top_value >= sc["flag_share"]))

    def supplier_rows(sub: pd.DataFrame, table: pd.DataFrame, k: int, per: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows_out, evidence = [], []
        for r in table.head(k).itertuples():
            mine = sub[sub["supplier_norm"] == r.supplier_norm].sort_values("raw_amount", ascending=False).head(per)
            cites = [ev(x.ocid, x.award_id, "award_amount", x.raw_amount) for x in mine.itertuples()]
            evidence += cites
            rows_out.append({"supplier": r.supplier, "n_awards": int(r.n), "share_count": round(float(r.share_count), 4),
                             "value_kes": float(r.value), "share_value": round(float(r.share_value), 4),
                             "largest_awards": [{"ocid": c["ocid"], "award_id": c["award_id"], "amount_kes": c["value"]}
                                                for c in cites]})
        return rows_out, evidence

    def flag_for(name: str, c: dict[str, Any], top_c: float, top_v: float, top: dict[str, Any],
                 cites: list[dict[str, Any]]) -> dict[str, Any]:
        return make_flag(
            "supplier_concentration",
            f"{name}: top supplier {top['supplier']} holds {top_c:.0%} of {c['n_awards']} named awards by count and "
            f"{top_v:.0%} by value",
            severity_hint(max(top_c, top_v), float(sc["flag_share"])), cites)

    per_supplier = int(ev_cfg["supplier_awards"])
    if b:
        sub = named[named["buyer_norm"] == bn]
        if sub.empty:
            warnings.append("This buyer has no supplier-named awards in scope.")
            return envelope({"buyer": b, "suppliers": [], "flags": []}, [], params, warnings)
        c = _concentration(sub)
        top_c, top_v, flagged = judge(c)
        coverage = len(sub) / max(1, int((df["buyer_norm"] == bn).sum()))
        warnings.append(f"Shares cover only the {coverage:.0%} of this buyer's awards that have a supplier name.")
        if c["n_awards"] < sc["min_awards"]:
            warnings.append(f"Only {c['n_awards']} named awards (< {sc['min_awards']}): shares are unstable and not flagged.")
        sup, evidence = supplier_rows(sub, c["table"], limit, per_supplier)
        name = sub["buyer_name"].iloc[0].strip()
        flags = [flag_for(name, c, top_c, top_v, sup[0], evidence[:per_supplier])] if flagged and evidence else []
        return envelope({"buyer": name, "n_awards_named": c["n_awards"],
                         "n_suppliers": c["n_suppliers"], "total_value_kes": c["total_value_kes"],
                         "hhi_value": c["hhi_value"], "hhi_count": c["hhi_count"],
                         "top_share_count": round(top_c, 4), "top_share_value": round(top_v, 4),
                         "flagged": flagged, "suppliers": sup, "flags": flags}, evidence, params, warnings)
    ranked = []
    for _, sub in named.groupby("buyer_norm"):
        if len(sub) < sc["min_awards"]:
            continue
        c = _concentration(sub)
        top_c, top_v, flagged = judge(c)
        ranked.append((max(top_c, top_v), sub, c, top_c, top_v, flagged))
    ranked.sort(key=lambda t: -t[0])
    out, evidence, flags = [], [], []
    for _, sub, c, top_c, top_v, flagged in ranked[:limit]:
        sup, ev_items = supplier_rows(sub, c["table"], 1, per_supplier)
        evidence += ev_items
        name = sub["buyer_name"].iloc[0].strip()
        out.append({"buyer": name, "n_awards_named": c["n_awards"],
                    "top_supplier": sup[0], "top_share_count": round(top_c, 4), "top_share_value": round(top_v, 4),
                    "hhi_value": c["hhi_value"], "flagged": flagged})
        if flagged and ev_items:
            flags.append(flag_for(name, c, top_c, top_v, sup[0], ev_items))
    warnings.append(f"Ranking covers buyers with >= {sc['min_awards']} supplier-named awards ({len(ranked)} eligible).")
    return envelope({"buyers": out, "flags": flags}, evidence, params, warnings)
