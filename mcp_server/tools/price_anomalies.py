"""detect_price_anomalies: unusually high awards inside a cohort, found with an Isolation Forest (fixed seed)."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from mcp_server.common import (base_cte, buyer_problem, clean_text, clean_years, envelope, ev, frame, rules, year_filter)
from mcp_server.registry import tool
from mcp_server.toolkit import (BuyerArg, BuyerTypeArg, CategoryArg, KeywordArg, LimitArg, YearsArg, buyer_types, categories,
                                check_choice, make_flag, ranking_limit, severity_hint)

ANOMALY_LABEL = -1            # scikit-learn's label for an outlier in IsolationForest.predict


def _anomalous_rows(cohort: pd.DataFrame, pa: dict[str, Any]) -> pd.DataFrame:
    """Fit the forest on log10(amount) of one cohort and return its high-side anomalies with their ratio to the median.

    The rows are sorted first so the result never depends on database row order; `random_state` is fixed in the rules."""
    cohort = cohort.sort_values(["ocid", "award_id"]).reset_index(drop=True)
    x = np.log10(cohort["amount"].to_numpy(dtype=float)).reshape(-1, 1)
    forest = IsolationForest(n_estimators=int(pa["n_estimators"]), contamination=float(pa["contamination"]),
                             random_state=int(pa["random_state"]))
    labels = forest.fit_predict(x)
    median = float(cohort["amount"].median())
    out = cohort.assign(anomaly_score=-forest.score_samples(x), ratio=cohort["amount"] / median, median=median)
    return out[(labels == ANOMALY_LABEL) & (out["ratio"] >= float(pa["min_ratio_to_median"]))]


@tool()
def detect_price_anomalies(buyer: BuyerArg = None, category: CategoryArg = None, buyer_type: BuyerTypeArg = None,
                           years: YearsArg = None, title_keyword: KeywordArg = None, limit: LimitArg = 10) -> dict[str, Any]:
    """Find awards whose amount is statistically unusual for their cohort, using an Isolation Forest (machine learning).

    Awards are grouped into cohorts of the same procurement category and buyer type (optionally narrowed by
    `title_keyword`; `years` is a list of calendar years [[data_cleaning.min_year]]-[[data_cleaning.max_year]]).
    A cohort needs >= [[price_anomalies.min_cohort]] awards with valid amounts before the model is fitted. The model
    sees the logarithm of the amount; an award is reported only if the model calls it an outlier (expected share
    [[price_anomalies.contamination]], fixed seed, so the same data always gives the same answer) AND it is >=
    [[price_anomalies.min_ratio_to_median]]x the cohort median. `buyer` (must identify one buyer) restricts the
    report to that buyer's awards while the cohort stays the whole category, so the buyer is compared with everyone.
    Without `buyer` the highest-ratio anomalies across all cohorts are ranked (`limit` 1-[[limits.ranking_max]]).
    Each anomaly gives an explanation like "3.2x median of 47 comparable awards" and cites the award plus up to
    [[price_anomalies.comparables_cited]] comparables closest to the median; each adds an entry to result.flags.
    There is no item-level data, so a cohort mixes different goods and works: an anomaly is a prompt for a human to
    check what was bought, not proof of overpricing.
    """
    cfg = rules()
    pa = cfg["price_anomalies"]
    cat = check_choice(category, categories(), "category")
    btype = check_choice(buyer_type, buyer_types(), "buyer_type")
    yrs = clean_years(years, cfg["data_cleaning"])
    kw = clean_text(title_keyword)
    limit = ranking_limit(limit)
    params: dict[str, Any] = {"buyer": buyer, "category": cat, "buyer_type": btype, "years": yrs, "title_keyword": kw,
                              "limit": limit, "contamination": pa["contamination"], "random_state": pa["random_state"],
                              "min_cohort": pa["min_cohort"], "min_ratio_to_median": pa["min_ratio_to_median"]}
    warnings: list[str] = []
    b = clean_text(buyer)
    buyer_norm = None
    if b:
        buyer_norm, problem = buyer_problem(b, params)
        if problem:
            return problem
        params["buyer_resolved"] = buyer_norm
    conds, args = [], []
    if cat:
        conds.append("category = ?"); args.append(cat)
    if btype:
        conds.append("buyer_type = ?"); args.append(btype)
    if kw:
        conds.append("contains(lower(title), ?)"); args.append(kw.lower())
    where = "".join(f" AND {c}" for c in conds)
    df = frame(f"WITH {base_cte(valid_only=True)} SELECT ocid, award_id, buyer_name, buyer_norm, supplier_name, title, category, "
               f"buyer_type, amount FROM a WHERE category IS NOT NULL AND buyer_type IS NOT NULL{where}{year_filter(yrs)}", args)
    if df.empty:
        warnings.append("No awards with valid amounts in scope.")
        return envelope({"cohorts": [], "anomalies": [], "flags": []}, [], params, warnings)
    cohorts, anomalies = [], []
    skipped = 0
    for (c, t), group in df.groupby(["category", "buyer_type"], sort=True):
        n = len(group)
        if n < int(pa["min_cohort"]):
            skipped += 1
            continue
        found = _anomalous_rows(group, pa)
        cohorts.append({"category": c, "buyer_type": t, "n": n, "median_kes": float(group["amount"].median()),
                        "n_anomalies": len(found)})
        for r in found.itertuples():
            anomalies.append((r, group, n))
    if skipped:
        warnings.append(f"{skipped} cohort(s) had fewer than {pa['min_cohort']} awards and were not analysed.")
    if buyer_norm:
        anomalies = [a for a in anomalies if a[0].buyer_norm == buyer_norm]
    anomalies.sort(key=lambda a: (-a[0].ratio, a[0].ocid, a[0].award_id))
    anomalies = anomalies[:limit]
    out, evidence, flags = [], [], []
    for r, group, n in anomalies:
        explanation = f"{r.ratio:.1f}x median of {n - 1} comparable awards (median KES {r.median:,.0f})"
        others = group[~((group["ocid"] == r.ocid) & (group["award_id"] == r.award_id))]
        nearest = others.assign(d=(others["amount"] - r.median).abs()).sort_values(["d", "ocid", "award_id"]).head(int(pa["comparables_cited"]))
        cites = [ev(r.ocid, r.award_id, "award_amount", float(r.amount))] + \
                [ev(x.ocid, x.award_id, "award_amount", float(x.amount)) for x in nearest.itertuples()]
        out.append({"ocid": r.ocid, "award_id": r.award_id, "title": r.title, "buyer": (r.buyer_name or "").strip(),
                    "supplier": r.supplier_name, "amount_kes": float(r.amount), "ratio_to_median": round(float(r.ratio), 2),
                    "anomaly_score": round(float(r.anomaly_score), 4), "explanation": explanation,
                    "cohort": {"category": r.category, "buyer_type": r.buyer_type, "n": n},
                    "comparables_cited": [{"ocid": x.ocid, "award_id": x.award_id, "title": x.title,
                                           "buyer": (x.buyer_name or "").strip(), "amount_kes": float(x.amount)}
                                          for x in nearest.itertuples()]})
        evidence += cites
        flags.append(make_flag("detect_price_anomalies", f"{r.title} ({r.ocid}): {explanation}",
                               severity_hint(float(r.ratio), float(pa["min_ratio_to_median"])), cites))
    if not out:
        warnings.append("No award is both a model outlier and above the ratio threshold in this scope.")
    summary = (f"{len(out)} anomalous award(s) across {len(cohorts)} cohort(s)" if out else
               f"no anomalous awards across {len(cohorts)} cohort(s)")
    return envelope({"cohorts": cohorts, "anomalies": out, "explanation": summary, "flags": flags}, evidence, params, warnings)
