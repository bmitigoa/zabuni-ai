"""detect_noncompetitive_method: a buyer's share of direct + restricted procurement vs the national baseline."""
from __future__ import annotations

from typing import Any

import pandas as pd

from mcp_server.common import (base_cte, buyer_problem, clean_text, clean_years, envelope, ev, frame, rules, year_filter)
from mcp_server.registry import tool
from mcp_server.toolkit import make_flag, ranking_limit, severity_hint


def _method_stats(df: pd.DataFrame, direct: list[str], restricted: list[str]) -> dict[str, Any]:
    """Counts and shares of direct / restricted awards (by number and by valid value) for a set of awards."""
    n = len(df)
    is_direct = df["procurement_method"].isin(direct)
    is_restricted = df["procurement_method"].isin(restricted)
    nd, nr = int(is_direct.sum()), int(is_restricted.sum())
    val = df["amount"].fillna(0.0)
    tv = float(val.sum())
    vd, vr = float(val[is_direct].sum()), float(val[is_restricted].sum())

    def share(a: float, b: float) -> float | None:
        return round(a / b, 4) if b else None

    return {"n_awards": n, "n_direct": nd, "n_restricted": nr, "n_noncompetitive": nd + nr,
            "share_direct": share(nd, n), "share_restricted": share(nr, n), "share_noncompetitive": share(nd + nr, n),
            "value_share_noncompetitive": share(vd + vr, tv), "total_value_kes": tv}


@tool()
def detect_noncompetitive_method(buyer: str | None = None, years: list[int] | None = None,
                                 limit: int = 10) -> dict[str, Any]:
    """Compare a buyer's use of non-open procurement (direct and restricted/selective) with the national baseline.

    Substitutes for a single-bidder check, which the data cannot support (no tenderers are published). `buyer` is an
    organisation name that must identify one buyer; omit it to rank the buyers (with >=
    [[noncompetitive_method.min_awards_for_ranking]] awards) whose non-competitive share is highest. `years` is a list
    of calendar years ([[data_cleaning.min_year]]-[[data_cleaning.max_year]]) applied to both the buyer and the
    baseline (awards with no valid date are then excluded); `limit` (1-[[limits.ranking_max]]) applies to the ranking.
    Returns counts and shares by number and value for the buyer vs all Kenyan buyers, the ratio to baseline, and cited
    non-open awards. flagged=true means the non-competitive share is >=
    [[noncompetitive_method.flag_ratio_to_baseline]]x the baseline with >=
    [[noncompetitive_method.min_noncompetitive_awards]] such awards; each flagged buyer adds an entry to result.flags.
    Direct/restricted procurement is lawful in defined cases under the PPADA, so a flag is a prompt for a human to check
    the justification, not a finding of wrongdoing.
    """
    cfg = rules()
    nc, ev_cfg = cfg["noncompetitive_method"], cfg["evidence"]
    direct, restricted = nc["direct_methods"], nc["restricted_methods"]
    yrs = clean_years(years, cfg["data_cleaning"])
    limit = ranking_limit(limit)
    params = {"buyer": buyer, "years": yrs, "limit": limit, "direct_methods": direct, "restricted_methods": restricted}
    warnings = ["Years filter excludes awards with no valid date."] if yrs else []
    cte = base_cte(valid_only=False)
    df = frame(f"WITH {cte} SELECT ocid, award_id, buyer_norm, buyer_name, procurement_method, amount, raw_amount "
               f"FROM a WHERE TRUE{year_filter(yrs)}")
    if df.empty:
        warnings.append("No awards in scope.")
        return envelope({"baseline": None, "flags": []}, [], params, warnings)
    baseline = _method_stats(df, direct, restricted)
    methods = df.groupby("procurement_method").size().sort_values(ascending=False)
    baseline["by_method"] = {str(k): int(v) for k, v in methods.items()}

    def judge(stats: dict[str, Any]) -> None:
        base = baseline["share_noncompetitive"]
        ratio = (stats["share_noncompetitive"] / base) if base and stats["share_noncompetitive"] is not None else None
        stats["ratio_to_baseline"] = round(ratio, 2) if ratio is not None else None
        stats["flagged"] = bool(ratio is not None and ratio >= nc["flag_ratio_to_baseline"]
                                and stats["n_noncompetitive"] >= nc["min_noncompetitive_awards"])

    def cite(sub: pd.DataFrame, k: int) -> list[dict[str, Any]]:
        top = sub[sub["procurement_method"].isin(direct + restricted)].sort_values("raw_amount", ascending=False).head(k)
        return [ev(r.ocid, r.award_id, "procurement_method", r.procurement_method) for r in top.itertuples()]

    def flag_for(name: str, stats: dict[str, Any], cites: list[dict[str, Any]]) -> dict[str, Any]:
        return make_flag(
            "detect_noncompetitive_method",
            f"{name}: direct/restricted share {stats['share_noncompetitive']:.1%} vs national "
            f"{baseline['share_noncompetitive']:.1%} ({stats['ratio_to_baseline']}x, {stats['n_noncompetitive']} awards)",
            severity_hint(stats["ratio_to_baseline"], float(nc["flag_ratio_to_baseline"])), cites)

    b = clean_text(buyer)
    if b:
        bn, problem = buyer_problem(b, params)
        if problem:
            return problem
        params["buyer_resolved"] = bn
        sub = df[df["buyer_norm"] == bn]
        if sub.empty:
            warnings.append("This buyer has no awards in the selected years.")
            return envelope({"buyer": bn, "buyer_stats": None, "baseline": baseline, "flags": []}, [], params, warnings)
        stats = _method_stats(sub, direct, restricted)
        judge(stats)
        evidence = cite(sub, int(ev_cfg["noncompetitive_buyer"]))
        name = sub["buyer_name"].iloc[0].strip()
        flags = [flag_for(name, stats, evidence)] if stats["flagged"] and evidence else []
        if stats["n_noncompetitive"] == 0:
            warnings.append("No direct or restricted awards for this buyer in scope (nothing to cite).")
        return envelope({"buyer": name, "buyer_stats": stats, "baseline": baseline,
                         "explanation": f"non-competitive share {stats['share_noncompetitive']:.1%} vs national "
                                        f"{baseline['share_noncompetitive']:.1%}", "flags": flags},
                        evidence, params, warnings)
    ranked = []
    for bn, sub in df[df["buyer_norm"].notna()].groupby("buyer_norm"):
        if len(sub) < nc["min_awards_for_ranking"]:
            continue
        st = _method_stats(sub, direct, restricted)
        judge(st)
        ranked.append((st["share_noncompetitive"], bn, sub, st))
    ranked.sort(key=lambda t: -t[0])
    out, evidence, flags = [], [], []
    for _, bn, sub, st in ranked[:limit]:
        cites = cite(sub, int(ev_cfg["noncompetitive_ranking"]))
        if not cites:
            continue
        evidence += cites
        name = sub["buyer_name"].iloc[0].strip()
        out.append({"buyer": name, **st})
        if st["flagged"]:
            flags.append(flag_for(name, st, cites))
    warnings.append(f"Ranking covers buyers with >= {nc['min_awards_for_ranking']} awards ({len(ranked)} eligible).")
    return envelope({"baseline": baseline, "ranked_buyers": out, "flags": flags}, evidence, params, warnings)
