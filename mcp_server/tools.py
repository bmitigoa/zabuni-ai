"""zabuni-mcp tool implementations (plain functions; server.py registers them with FastMCP).

Every tool returns {result, evidence, params_used, warnings}. Evidence items are
{ocid, award_id, field, value} pointing at the OCDS award a statement rests on. A tool never
raises: bad input and empty results come back as an explanatory message in `warnings`.

Flags are screening indicators for a human reviewer, never findings of wrongdoing, and no tool
recommends or awards a tender. Thresholds come from mcp_server/rules.py (provisional).
"""
from __future__ import annotations

import functools
import re
import sys
from typing import Any, Callable

import numpy as np
import pandas as pd

from mcp_server.common import (MAX_EVIDENCE, ToolInputError, base_cte, buyer_problem, clean_date,
                               clean_text, clean_years, cursor, envelope, error, ev, frame, norm,
                               rows, rules, year_filter)

CATEGORIES = {"goods", "works", "services"}
BUYER_TYPES = {"education", "health", "county_government", "state_agency", "state_corporation", "other"}
STOPWORDS = {
    "supply", "supplies", "delivery", "deliver", "provision", "proposed", "services", "service", "works",
    "with", "various", "from", "assorted", "items", "tender", "contract", "financial", "year", "general",
    "limited", "that", "this", "their", "other", "within", "county", "government", "ministry",
}


def safe(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Turn input errors and unexpected failures into an error envelope instead of a crash."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ToolInputError as exc:
            return error(str(exc), kwargs)
        except Exception as exc:  # noqa: BLE001 - last line of defence for the agent loop
            print(f"[zabuni-mcp] {fn.__name__} failed: {exc!r}", file=sys.stderr)
            return error(f"Internal error in {fn.__name__}: {type(exc).__name__}: {exc}", kwargs)
    return wrapper


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _award_row(r: dict[str, Any]) -> dict[str, Any]:
    return {"ocid": r["ocid"], "award_id": r["award_id"], "buyer": r["buyer_name"],
            "supplier": r["supplier_name"], "title": r["title"], "category": r["category"],
            "procurement_method": r["procurement_method"], "amount_kes": r["raw_amount"],
            "award_date": r["award_date"].date().isoformat() if r["award_date"] is not None and not pd.isna(r["award_date"]) else None}


# =============================================================================== search_awards
@safe
def search_awards(buyer: str | None = None, supplier: str | None = None, title_keyword: str | None = None,
                  date_from: str | None = None, date_to: str | None = None, limit: int = 20) -> dict[str, Any]:
    """Find awards in the frozen PPRA Kenya OCDS snapshot. Use this first to explore and to get ocid/award_id values.

    At least one filter is required. `buyer` and `supplier` are case-insensitive partial company/organisation
    names (matched on normalised names); `title_keyword` is a word or phrase in the award title;
    `date_from`/`date_to` are YYYY-MM-DD bounds on the contract signing date (the snapshot has no award decision
    date; awards with no valid date are excluded when a date filter is used). `limit` is 1-50 (default 20).
    Returns matching awards (newest first) with amount in KES, procurement method and the total match count.
    Each row is backed by an evidence item pointing at its OCDS award. Awards with amount <= 0 or > KES 10 billion
    are shown but marked amount_valid=false (probable data-entry errors; excluded from statistics elsewhere).
    """
    params = {"buyer": buyer, "supplier": supplier, "title_keyword": title_keyword,
              "date_from": date_from, "date_to": date_to, "limit": limit}
    b, s, kw = clean_text(buyer), clean_text(supplier), clean_text(title_keyword)
    d_from, d_to = clean_date(date_from, "date_from"), clean_date(date_to, "date_to")
    if not any([b, s, kw, d_from, d_to]):
        raise ToolInputError("Provide at least one filter: buyer, supplier, title_keyword, date_from or date_to.")
    limit = _clamp(limit, 1, 50)
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
    total = rows(f"WITH {cte} SELECT COUNT(*) AS n FROM a WHERE {where}", args)[0]["n"]
    found = rows(f"WITH {cte} SELECT * FROM a WHERE {where} "
                 f"ORDER BY award_date DESC NULLS LAST, raw_amount DESC LIMIT {limit}", args)
    warnings = []
    if total == 0:
        warnings.append("No awards matched. Try fewer filters or a shorter keyword (names are matched as partial text).")
    if total > limit:
        warnings.append(f"Showing {len(found)} of {total} matches; narrow the filters or raise limit (max 50).")
    out_rows, evidence = [], []
    for r in found:
        row = _award_row(r)
        row["amount_valid"] = r["amount"] is not None
        out_rows.append(row)
        evidence.append(ev(r["ocid"], r["award_id"], "award_amount", r["raw_amount"]))
    params.update(limit=limit, date_from=d_from, date_to=d_to)
    return envelope({"total_matches": total, "returned": len(out_rows), "awards": out_rows}, evidence, params, warnings)


# ========================================================================= compute_price_benchmark
def _tokens(title: str | None) -> list[str]:
    seen: list[str] = []
    for t in re.findall(r"[a-z]+", (title or "").lower()):
        if len(t) >= 4 and t not in STOPWORDS and t not in seen:
            seen.append(t)
    return seen


@safe
def compute_price_benchmark(title_keyword: str | None = None, ocid: str | None = None, award_id: str | None = None,
                            category: str | None = None, buyer_type: str | None = None,
                            years: list[int] | None = None, amount: float | None = None) -> dict[str, Any]:
    """Benchmark an award's amount against comparable awards (value-for-money / price-outlier check).

    Give `ocid` (optionally `award_id`) to test a specific award, or `title_keyword` (and/or `category`) to
    just get the price distribution, or add `amount` (KES) to see where a hypothetical amount would sit.
    `category` is goods | works | services (defaults to the award's own category when `ocid` is given).
    `buyer_type` narrows to education | health | county_government | state_agency | state_corporation | other
    (heuristic, from the buyer's name). `years` is a list of calendar years (2018-2026), e.g. [2023, 2024].
    The snapshot has NO item-level data, so comparability rests on title words + category + buyer type only.
    If `ocid` is given without `title_keyword`, comparable title words are chosen automatically (reported in
    keywords_used). Returns median, quartiles/IQR, n comparables, the award's ratio to the median
    ("3.2x median of 47 comparable awards") and percentile rank. If fewer than 10 comparables exist the result has
    insufficient_comparables=true plus widen_suggestions: widen the search and call again. flagged=true means
    ratio >= 3x median with enough comparables: a prompt for human review, not proof of overpricing.
    """
    cfg = rules()
    pb = cfg["price_benchmark"]
    params = {"title_keyword": title_keyword, "ocid": ocid, "award_id": award_id, "category": category,
              "buyer_type": buyer_type, "years": years, "amount": amount}
    kw, oc, aid = clean_text(title_keyword), clean_text(ocid), clean_text(award_id)
    cat = (clean_text(category) or "").lower() or None
    btype = (clean_text(buyer_type) or "").lower() or None
    yrs = clean_years(years, cfg["data_cleaning"])
    if cat and cat not in CATEGORIES:
        raise ToolInputError(f"category must be one of {sorted(CATEGORIES)}.")
    if btype and btype not in BUYER_TYPES:
        raise ToolInputError(f"buyer_type must be one of {sorted(BUYER_TYPES)}.")
    if not (oc or kw or cat):
        raise ToolInputError("Provide ocid, title_keyword or category.")
    if amount is not None and not (amount > 0):
        raise ToolInputError("amount must be a positive number of KES.")
    warnings: list[str] = []
    target: dict[str, Any] | None = None
    cte = base_cte(valid_only=False)
    if oc:
        sql = f"WITH {cte} SELECT * FROM a WHERE ocid = ?" + (" AND award_id = ?" if aid else "")
        found = rows(sql, [oc] + ([aid] if aid else []))
        if not found:
            raise ToolInputError(f"No award found for ocid={oc!r}" + (f", award_id={aid!r}." if aid else "."))
        found.sort(key=lambda r: (r["amount"] is None, -(r["amount"] or 0)))
        target = found[0]
        if len(found) > 1:
            warnings.append(f"ocid has {len(found)} awards; benchmarking the largest ({target['award_id']}). "
                            "Pass award_id to choose another.")
        if target["amount"] is None:
            warnings.append(f"Target amount {target['raw_amount']} is invalid (<=0 or > KES 10 billion); "
                            "no position computed.")
        cat = cat or target["category"]
    # --- build the cohort filter
    conds, args = [], []
    if cat:
        conds.append("category = ?"); args.append(cat)
    if btype:
        conds.append("buyer_type = ?"); args.append(btype)
    if yrs:
        conds.append(f"yr IN ({','.join(str(y) for y in yrs)})")
    if target:
        conds.append("NOT (ocid = ? AND award_id = ?)"); args += [target["ocid"], target["award_id"]]
    scope = (" AND " + " AND ".join(conds)) if conds else ""

    def count_with(tokens: list[str]) -> int:
        extra = "".join(" AND contains(lower(title), ?)" for _ in tokens)
        return rows(f"WITH {cte} SELECT COUNT(*) AS n FROM a WHERE amount IS NOT NULL{scope}{extra}",
                    args + tokens)[0]["n"]

    tokens, source = [], "none"
    if kw:
        tokens, source = re.findall(r"[a-z0-9]+", kw.lower()), "user"
    elif target:
        cands = [(count_with([t]), t) for t in _tokens(target["title"])[:8]]
        usable = sorted((n, t) for n, t in cands if n >= pb["min_comparables"])
        for k in (2, 1):
            pick = [t for _, t in usable[:k]]
            if len(pick) == k and count_with(pick) >= pb["min_comparables"]:
                tokens, source = pick, "auto"
                break
        if not tokens:
            warnings.append("No title word is common enough to build a comparable set; cohort is the whole "
                            "category/scope, so similarity is weak.")
    extra = "".join(" AND contains(lower(title), ?)" for _ in tokens)
    cohort = frame(f"WITH {cte} SELECT ocid, award_id, amount, title, buyer_name FROM a "
                   f"WHERE amount IS NOT NULL{scope}{extra}", args + tokens)
    n = len(cohort)
    filters = {"category": cat, "buyer_type": btype, "years": yrs, "keywords_used": tokens, "keywords_source": source}
    insufficient = n < pb["min_comparables"]
    result: dict[str, Any] = {"cohort": {"n": n, **filters}, "target": None, "flagged": False,
                              "insufficient_comparables": insufficient, "explanation": None}
    evidence: list[dict[str, Any]] = []
    if n == 0:
        warnings.append("No comparable awards found. Widen the search (drop buyer_type/keyword, add years, or change category).")
    else:
        v = cohort["amount"].to_numpy(dtype=float)
        q1, med, q3 = (float(x) for x in np.percentile(v, [25, 50, 75]))
        result["cohort"].update(median=med, p25=q1, p75=q3, iqr=q3 - q1, min=float(v.min()), max=float(v.max()))
        target_amount = (target["amount"] if target else None) or amount
        if target_amount:
            ratio = float(target_amount) / med
            result["target"] = {
                "ocid": target["ocid"] if target else None, "award_id": target["award_id"] if target else None,
                "title": target["title"] if target else None, "buyer": target["buyer_name"] if target else None,
                "amount_kes": float(target_amount), "ratio_to_median": round(ratio, 2),
                "percentile_rank": round(float((v <= target_amount).mean() * 100), 1),
                "above_upper_fence": bool(target_amount > q3 + 1.5 * (q3 - q1)),
            }
            result["explanation"] = f"{ratio:.1f}x median of {n} comparable awards (median KES {med:,.0f})"
            result["flagged"] = bool(ratio >= pb["flag_ratio_to_median"] and not insufficient)
        else:
            result["explanation"] = f"Cohort median KES {med:,.0f} from {n} comparable awards"
        if target and target["amount"] is not None:
            evidence.append(ev(target["ocid"], target["award_id"], "award_amount", target["raw_amount"]))
        nearest = cohort.assign(d=(cohort["amount"] - med).abs()).nsmallest(pb["max_comparables_cited"], "d")
        result["comparables_cited"] = [{"ocid": r.ocid, "award_id": r.award_id, "title": r.title,
                                        "buyer": r.buyer_name, "amount_kes": float(r.amount)}
                                       for r in nearest.itertuples()]
        evidence += [ev(r.ocid, r.award_id, "award_amount", float(r.amount)) for r in nearest.itertuples()]
    if insufficient:
        sugg = []
        if btype: sugg.append("drop buyer_type")
        if kw or source == "auto": sugg.append("remove or shorten the title keyword")
        if yrs: sugg.append("remove the years filter or add more years")
        if cat: sugg.append("drop category (cohort will mix goods/works/services)")
        result["widen_suggestions"] = sugg or ["no filters left to relax; the cohort is as wide as the data allows"]
        warnings.append(f"Only {n} comparable awards (< {pb['min_comparables']}): result is not reliable. "
                        "Widen the search and call again.")
    if result["flagged"] and not evidence:
        result["flagged"] = False
    params.update(years=yrs, category=cat)
    return envelope(result, evidence, params, warnings)


# ================================================================================ detect_splitting
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


@safe
def detect_splitting(buyer: str | None = None, supplier: str | None = None, window_days: int | None = None,
                     min_awards: int | None = None, thresholds_kes: list[float] | None = None,
                     years: list[int] | None = None, limit: int = 10) -> dict[str, Any]:
    """Look for possible contract splitting: several awards from the SAME buyer to the SAME supplier within a short
    window, each just below an approval threshold, whose combined total exceeds that threshold.

    `buyer` (organisation name; must identify one buyer) and/or `supplier` (partial name) narrow the scan; with
    neither, all buyers are scanned and the largest clusters returned. `window_days` (default 30), `min_awards`
    (default 3), `thresholds_kes` (default list of illustrative KES thresholds, provisional and unverified),
    `years` (list of calendar years) and `limit` (1-20 clusters, default 10) are configurable. An award is 'just
    below' threshold T when 0.5*T <= amount < T. Only awards with a named supplier, a valid date and a valid amount
    are examined (coverage is reported in warnings). The snapshot has no item data, so this cannot confirm the awards
    were for the same goods; a cluster is a prompt for human review, not proof of splitting. Each cluster lists its
    awards (ocid, award_id, date, amount) and each award is cited in evidence.
    """
    cfg = rules()
    sp = cfg["splitting"]
    window = _clamp(window_days if window_days is not None else sp["window_days"], 1, 365)
    min_n = _clamp(min_awards if min_awards is not None else sp["min_awards"], 2, 50)
    thresholds = sorted({float(t) for t in (thresholds_kes or sp["thresholds_kes"]) if t and float(t) > 0})
    if not thresholds:
        raise ToolInputError("thresholds_kes must contain at least one positive number.")
    limit = _clamp(limit, 1, 20)
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
        return envelope({"clusters": [], "total_clusters": 0}, [], params, warnings)
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
    df = df.assign(_day=(df["award_date"] - pd.Timestamp("1970-01-01")).dt.total_seconds() / 86400.0
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
                "first_date": sub["award_date"].min().date().isoformat(),
                "last_date": sub["award_date"].max().date().isoformat(),
                "awards": [{"ocid": r.ocid, "award_id": r.award_id, "title": r.title,
                            "award_date": r.award_date.date().isoformat(), "amount_kes": float(r.amount)}
                           for r in sub.itertuples()],
            })
    clusters.sort(key=lambda c: -c["total_kes"])
    total = len(clusters)
    kept, evidence = [], []
    for c in clusters[:limit]:
        if len(evidence) + 2 * c["n_awards"] > MAX_EVIDENCE:
            warnings.append("Evidence cap reached; remaining clusters omitted. Narrow the scan (buyer/supplier/years).")
            break
        kept.append(c)
        for a in c["awards"]:
            evidence.append(ev(a["ocid"], a["award_id"], "award_amount", a["amount_kes"]))
            evidence.append(ev(a["ocid"], a["award_id"], "contract_date_signed", a["award_date"]))
    if total == 0:
        warnings.append("No splitting pattern found with these parameters (this is not proof that none exists).")
    elif total > len(kept):
        warnings.append(f"Showing {len(kept)} of {total} clusters (largest first).")
    return envelope({"total_clusters": total, "clusters": kept}, evidence, params, warnings)


# ======================================================================= detect_noncompetitive_method
def _method_stats(df: pd.DataFrame, direct: list[str], restricted: list[str]) -> dict[str, Any]:
    n = len(df)
    nd = int(df["procurement_method"].isin(direct).sum())
    nr = int(df["procurement_method"].isin(restricted).sum())
    val = df["amount"].fillna(0.0)
    tv = float(val.sum())
    vd = float(val[df["procurement_method"].isin(direct)].sum())
    vr = float(val[df["procurement_method"].isin(restricted)].sum())
    div = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {"n_awards": n, "n_direct": nd, "n_restricted": nr, "n_noncompetitive": nd + nr,
            "share_direct": div(nd, n), "share_restricted": div(nr, n), "share_noncompetitive": div(nd + nr, n),
            "value_share_noncompetitive": div(vd + vr, tv), "total_value_kes": tv}


@safe
def detect_noncompetitive_method(buyer: str | None = None, years: list[int] | None = None,
                                 limit: int = 10) -> dict[str, Any]:
    """Compare a buyer's use of non-open procurement (direct and restricted/selective) with the national baseline.

    Substitutes for a single-bidder check, which the data cannot support (no tenderers are published). `buyer` is an
    organisation name that must identify one buyer; omit it to rank the buyers (with >= 20 awards) whose
    non-competitive share is highest. `years` is a list of calendar years (2018-2026) applied to both the buyer and the
    baseline (awards with no valid date are then excluded); `limit` (1-20) applies to the ranking. Returns counts and
    shares by number and value for the buyer vs all Kenyan buyers, the ratio to baseline, and cited non-open awards.
    flagged=true means the non-competitive share is >= 2x the baseline with >= 10 such awards. Direct/restricted
    procurement is lawful in defined cases under the PPADA, so a flag is a prompt for a human to check the
    justification, not a finding of wrongdoing.
    """
    cfg = rules()
    nc = cfg["noncompetitive_method"]
    direct, restricted = nc["direct_methods"], nc["restricted_methods"]
    yrs = clean_years(years, cfg["data_cleaning"])
    limit = _clamp(limit, 1, 20)
    params = {"buyer": buyer, "years": yrs, "limit": limit, "direct_methods": direct, "restricted_methods": restricted}
    warnings = ["Years filter excludes awards with no valid date."] if yrs else []
    cte = base_cte(valid_only=False)
    df = frame(f"WITH {cte} SELECT ocid, award_id, buyer_norm, buyer_name, procurement_method, amount, raw_amount "
               f"FROM a WHERE TRUE{year_filter(yrs)}")
    if df.empty:
        warnings.append("No awards in scope.")
        return envelope({"baseline": None}, [], params, warnings)
    baseline = _method_stats(df, direct, restricted)
    noncomp = df["procurement_method"].isin(direct + restricted)
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

    b = clean_text(buyer)
    if b:
        bn, problem = buyer_problem(b, params)
        if problem:
            return problem
        params["buyer_resolved"] = bn
        sub = df[df["buyer_norm"] == bn]
        if sub.empty:
            warnings.append("This buyer has no awards in the selected years.")
            return envelope({"buyer": bn, "buyer_stats": None, "baseline": baseline}, [], params, warnings)
        stats = _method_stats(sub, direct, restricted)
        judge(stats)
        evidence = cite(sub, 10)
        if stats["n_noncompetitive"] == 0:
            warnings.append("No direct or restricted awards for this buyer in scope (nothing to cite).")
        return envelope({"buyer": sub["buyer_name"].iloc[0].strip(), "buyer_stats": stats, "baseline": baseline,
                         "explanation": f"non-competitive share {stats['share_noncompetitive']:.1%} vs national "
                                        f"{baseline['share_noncompetitive']:.1%}"}, evidence, params, warnings)
    ranked = []
    for bn, sub in df[df["buyer_norm"].notna()].groupby("buyer_norm"):
        if len(sub) < nc["min_awards_for_ranking"]:
            continue
        st = _method_stats(sub, direct, restricted)
        judge(st)
        ranked.append((st["share_noncompetitive"], bn, sub, st))
    ranked.sort(key=lambda t: -t[0])
    out, evidence = [], []
    for _, bn, sub, st in ranked[:limit]:
        c = cite(sub, 3)
        if not c:
            continue
        evidence += c
        out.append({"buyer": sub["buyer_name"].iloc[0].strip(), **st})
    warnings.append(f"Ranking covers buyers with >= {nc['min_awards_for_ranking']} awards ({len(ranked)} eligible).")
    return envelope({"baseline": baseline, "ranked_buyers": out}, evidence, params, warnings)


# ======================================================================== supplier_concentration
def _concentration(sub: pd.DataFrame) -> dict[str, Any]:
    n = len(sub)
    val_total = float(sub["amount"].sum())
    g = sub.groupby("supplier_norm").agg(supplier=("supplier_name", "first"), n=("ocid", "size"),
                                         value=("amount", "sum")).reset_index()
    g["share_count"] = g["n"] / n
    g["share_value"] = g["value"] / val_total if val_total else 0.0
    g = g.sort_values(["share_value", "n"], ascending=False)
    return {"n_awards": n, "n_suppliers": len(g), "total_value_kes": val_total,
            "hhi_value": round(float(((g["share_value"] * 100) ** 2).sum()), 0) if val_total else None,
            "hhi_count": round(float(((g["share_count"] * 100) ** 2).sum()), 0),
            "table": g}


@safe
def supplier_concentration(buyer: str | None = None, years: list[int] | None = None,
                           limit: int = 10) -> dict[str, Any]:
    """Show how concentrated a buyer's awards are among suppliers (dependence on one or a few suppliers).

    `buyer` is an organisation name that must identify one buyer; omit it to rank buyers (with >= 30 supplier-named
    awards) by their largest supplier's share. `years` is a list of calendar years (2018-2026; awards with no valid
    date are then excluded); `limit` (1-20) caps the suppliers listed per buyer or the buyers ranked. Shares are
    computed only over awards that have a supplier name (about 73% of awards; coverage is reported) and by both
    award count and value (KES, valid amounts only). Also returns Herfindahl-Hirschman indices (0-10,000; higher =
    more concentrated). flagged=true means the top supplier holds >= 25% by count or value among >= 30 named awards.
    Concentration can have legitimate causes (specialised goods, framework agreements): a prompt for human review.
    Each listed supplier is backed by evidence pointing at its largest awards.
    """
    cfg = rules()
    sc = cfg["supplier_concentration"]
    yrs = clean_years(years, cfg["data_cleaning"])
    limit = _clamp(limit, 1, 20)
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
        return envelope({"buyers": []} if not b else {"buyer": b, "suppliers": []}, [], params, warnings)
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

    if b:
        sub = named[named["buyer_norm"] == bn]
        if sub.empty:
            warnings.append("This buyer has no supplier-named awards in scope.")
            return envelope({"buyer": b, "suppliers": []}, [], params, warnings)
        c = _concentration(sub)
        top_c, top_v, flagged = judge(c)
        coverage = len(sub) / max(1, int((df["buyer_norm"] == bn).sum()))
        warnings.append(f"Shares cover only the {coverage:.0%} of this buyer's awards that have a supplier name.")
        if c["n_awards"] < sc["min_awards"]:
            warnings.append(f"Only {c['n_awards']} named awards (< {sc['min_awards']}): shares are unstable and not flagged.")
        sup, evidence = supplier_rows(sub, c["table"], limit, 3)
        return envelope({"buyer": sub["buyer_name"].iloc[0].strip(), "n_awards_named": c["n_awards"],
                         "n_suppliers": c["n_suppliers"], "total_value_kes": c["total_value_kes"],
                         "hhi_value": c["hhi_value"], "hhi_count": c["hhi_count"],
                         "top_share_count": round(top_c, 4), "top_share_value": round(top_v, 4),
                         "flagged": flagged, "suppliers": sup}, evidence, params, warnings)
    ranked = []
    for bn2, sub in named.groupby("buyer_norm"):
        if len(sub) < sc["min_awards"]:
            continue
        c = _concentration(sub)
        top_c, top_v, flagged = judge(c)
        ranked.append((max(top_c, top_v), sub, c, top_c, top_v, flagged))
    ranked.sort(key=lambda t: -t[0])
    out, evidence = [], []
    for _, sub, c, top_c, top_v, flagged in ranked[:limit]:
        sup, ev_items = supplier_rows(sub, c["table"], 1, 3)
        evidence += ev_items
        out.append({"buyer": sub["buyer_name"].iloc[0].strip(), "n_awards_named": c["n_awards"],
                    "top_supplier": sup[0], "top_share_count": round(top_c, 4), "top_share_value": round(top_v, 4),
                    "hhi_value": c["hhi_value"], "flagged": flagged})
    warnings.append(f"Ranking covers buyers with >= {sc['min_awards']} supplier-named awards ({len(ranked)} eligible).")
    return envelope({"buyers": out}, evidence, params, warnings)


# ================================================================================ resources (data)
def release_record(ocid: str) -> dict[str, Any]:
    """Rebuild the stored OCDS record for an ocid from the database (tender, awards, suppliers).

    Only fields the loader stored are returned: organisation names, amounts, dates, methods. No contact points,
    emails, phone numbers or individuals' names exist in the database.
    """
    oc = clean_text(ocid)
    if not oc:
        return {"error": "ocid is empty."}
    t = rows("SELECT * FROM tenders WHERE ocid = ?", [oc])
    if not t:
        return {"error": f"No record stored for ocid {oc!r}."}
    aw = rows("SELECT * FROM awards WHERE ocid = ? ORDER BY award_id", [oc])
    sup = rows("SELECT award_id, supplier_id, supplier_name FROM award_suppliers WHERE ocid = ? ORDER BY award_id", [oc])
    tender = {k: v for k, v in t[0].items() if v is not None and not k.endswith("_norm")}
    awards = []
    for a in aw:
        awards.append({**{k: v for k, v in a.items() if v is not None},
                       "suppliers": [{"id": s["supplier_id"], "name": s["supplier_name"]}
                                     for s in sup if s["award_id"] == a["award_id"]]})
    return {"ocid": oc, "tender": tender, "awards": awards,
            "source": {"publisher": "PPRA Kenya (OCDS)",
                       "registry": "https://data.open-contracting.org/en/publication/147",
                       "snapshot": "full.jsonl.gz downloaded 2026-10-08 (frozen)",
                       "note": "Reconstructed from fields stored in zabuni.duckdb; contact/personal fields are never loaded."}}
