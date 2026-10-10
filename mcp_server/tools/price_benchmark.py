"""compute_price_benchmark: is an award's price unusual compared with comparable awards?"""
from __future__ import annotations

import re
from typing import Any

import numpy as np

from mcp_server.common import (ToolInputError, base_cte, clean_text, clean_years, envelope, ev, frame, rows, rules)
from mcp_server.registry import tool
from mcp_server.toolkit import buyer_types, categories, check_choice, make_flag, severity_hint

QUARTILES = (25, 50, 75)      # the definition of quartiles, not a tunable threshold


def _tokens(title: str | None, pb: dict[str, Any]) -> list[str]:
    """Distinctive words of a title (alphabetic, long enough, not a stop word), in order, without repeats."""
    stop = set(pb["stopwords"])
    seen: list[str] = []
    for t in re.findall(r"[a-z]+", (title or "").lower()):
        if len(t) >= int(pb["title_token_min_len"]) and t not in stop and t not in seen:
            seen.append(t)
    return seen


@tool()
def compute_price_benchmark(title_keyword: str | None = None, ocid: str | None = None, award_id: str | None = None,
                            category: str | None = None, buyer_type: str | None = None,
                            years: list[int] | None = None, amount: float | None = None,
                            auto_keywords: bool = True) -> dict[str, Any]:
    """Benchmark an award's amount against comparable awards (value-for-money / price-outlier check).

    Give `ocid` (optionally `award_id`) to test a specific award, or `title_keyword` (and/or `category`) to
    just get the price distribution, or add `amount` (KES) to see where a hypothetical amount would sit.
    `category` is [[categories|list]] (defaults to the award's own category when `ocid` is given).
    `buyer_type` narrows to one of the heuristic buyer types (from the buyer's name): education, health,
    county_government, state_agency, state_corporation or other. `years` is a list of calendar years
    ([[data_cleaning.min_year]]-[[data_cleaning.max_year]]), e.g. [2023, 2024].
    The snapshot has NO item-level data, so comparability rests on title words + category + buyer type only.
    If `ocid` is given without `title_keyword`, comparable title words are chosen automatically (reported in
    keywords_used) unless `auto_keywords` is false (then the cohort is the category only). Returns median,
    quartiles/IQR, n comparables, the award's ratio to the median ("3.2x median of 47 comparable awards"), its
    percentile rank, and the target's buyer and supplier. If fewer than [[price_benchmark.min_comparables]]
    comparables exist the result has insufficient_comparables=true, human-readable widen_suggestions and
    machine-readable widen_options (ops to apply to this call's arguments): widen the search and call again.
    flagged=true means ratio >= [[price_benchmark.flag_ratio_to_median]]x median with enough comparables; it adds an
    entry to result.flags. A flag is a prompt for human review, not proof of overpricing.
    """
    cfg = rules()
    pb = cfg["price_benchmark"]
    params = {"title_keyword": title_keyword, "ocid": ocid, "award_id": award_id, "category": category,
              "buyer_type": buyer_type, "years": years, "amount": amount, "auto_keywords": auto_keywords}
    kw, oc, aid = clean_text(title_keyword), clean_text(ocid), clean_text(award_id)
    cat = check_choice(category, categories(), "category")
    btype = check_choice(buyer_type, buyer_types(), "buyer_type")
    yrs = clean_years(years, cfg["data_cleaning"])
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
            warnings.append(f"Target amount {target['raw_amount']} is invalid (outside the valid amount range); "
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

    min_n = int(pb["min_comparables"])
    tokens, source = [], "none"
    if kw:
        tokens, source = re.findall(r"[a-z0-9]+", kw.lower()), "user"
    elif target and auto_keywords:
        cands = [(count_with([t]), t) for t in _tokens(target["title"], pb)[:int(pb["auto_keyword_candidates"])]]
        usable = sorted((n, t) for n, t in cands if n >= min_n)
        for k in range(int(pb["auto_keyword_max"]), 0, -1):
            pick = [t for _, t in usable[:k]]
            if len(pick) == k and count_with(pick) >= min_n:
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
    insufficient = n < min_n
    result: dict[str, Any] = {"cohort": {"n": n, **filters}, "target": None, "flagged": False,
                              "insufficient_comparables": insufficient, "explanation": None, "flags": []}
    evidence: list[dict[str, Any]] = []
    if n == 0:
        warnings.append("No comparable awards found. Widen the search (drop buyer_type/keyword, add years, or change category).")
    else:
        v = cohort["amount"].to_numpy(dtype=float)
        q1, med, q3 = (float(x) for x in np.percentile(v, QUARTILES))
        result["cohort"].update(median=med, p25=q1, p75=q3, iqr=q3 - q1, min=float(v.min()), max=float(v.max()))
        target_amount = (target["amount"] if target else None) or amount
        ratio = None
        if target_amount:
            ratio = float(target_amount) / med
            result["target"] = {
                "ocid": target["ocid"] if target else None, "award_id": target["award_id"] if target else None,
                "title": target["title"] if target else None, "buyer": (target["buyer_name"] or "").strip() if target else None,
                "supplier": target["supplier_name"] if target else None,
                "amount_kes": float(target_amount), "ratio_to_median": round(ratio, 2),
                "percentile_rank": round(float((v <= target_amount).mean() * 100), 1),
                "above_upper_fence": bool(target_amount > q3 + float(pb["iqr_fence_multiplier"]) * (q3 - q1)),
            }
            result["explanation"] = f"{ratio:.1f}x median of {n} comparable awards (median KES {med:,.0f})"
            result["flagged"] = bool(ratio >= pb["flag_ratio_to_median"] and not insufficient)
        else:
            result["explanation"] = f"Cohort median KES {med:,.0f} from {n} comparable awards"
        if target and target["amount"] is not None:
            evidence.append(ev(target["ocid"], target["award_id"], "award_amount", target["raw_amount"]))
        nearest = cohort.assign(d=(cohort["amount"] - med).abs()).nsmallest(int(pb["max_comparables_cited"]), "d")
        result["comparables_cited"] = [{"ocid": r.ocid, "award_id": r.award_id, "title": r.title,
                                        "buyer": r.buyer_name, "amount_kes": float(r.amount)}
                                       for r in nearest.itertuples()]
        evidence += [ev(r.ocid, r.award_id, "award_amount", float(r.amount)) for r in nearest.itertuples()]
        if result["flagged"] and evidence and target:
            result["flags"].append(make_flag(
                "compute_price_benchmark",
                f"{target['title']} ({target['ocid']}): {result['explanation']}",
                severity_hint(ratio, float(pb["flag_ratio_to_median"])), list(evidence)))
    if result["flagged"] and not evidence:
        result["flagged"] = False
    if insufficient:
        sugg, ops = [], []
        if btype:
            sugg.append("drop buyer_type"); ops.append({"op": "drop", "arg": "buyer_type"})
        if yrs:
            sugg.append("remove the years filter or add more years"); ops.append({"op": "drop", "arg": "years"})
        if kw:
            sugg.append("remove or shorten the title keyword"); ops.append({"op": "drop", "arg": "title_keyword"})
        elif source == "auto":
            sugg.append("compare on category only (auto_keywords=false)")
            ops.append({"op": "set", "arg": "auto_keywords", "value": False})
        if category and cat:
            sugg.append("drop category (cohort will mix goods/works/services)"); ops.append({"op": "drop", "arg": "category"})
        result["widen_suggestions"] = sugg or ["no filters left to relax; the cohort is as wide as the data allows"]
        result["widen_options"] = ops
        warnings.append(f"Only {n} comparable awards (< {min_n}): result is not reliable. "
                        "Widen the search and call again.")
    params.update(years=yrs, category=cat)
    return envelope(result, evidence, params, warnings)
