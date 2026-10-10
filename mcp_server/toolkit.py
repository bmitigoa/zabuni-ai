"""Small helpers shared by the tool modules (kept out of common.py, which is about data access and envelopes)."""
from __future__ import annotations

from typing import Any

import pandas as pd

from mcp_server.common import ToolInputError, rules
from mcp_server.rules import buyer_type_names


def clamp(value: int, lo: int, hi: int) -> int:
    """Force `value` into [lo, hi]. Bounds come from rules; callers never pass literals."""
    return max(lo, min(hi, int(value)))


def ranking_limit(value: int) -> int:
    """`limit` for ranking-style tools: 1 .. limits.ranking_max."""
    return clamp(value, 1, int(rules()["limits"]["ranking_max"]))


def categories() -> list[str]:
    return list(rules()["categories"])


def buyer_types() -> list[str]:
    return buyer_type_names(rules())


def check_choice(value: str | None, allowed: list[str], name: str) -> str | None:
    """Lower-case a text choice and reject values outside `allowed` with a helpful message."""
    value = (value or "").strip().lower() or None
    if value is not None and value not in allowed:
        raise ToolInputError(f"{name} must be one of {sorted(allowed)}.")
    return value


def date_text(value: Any) -> str | None:
    """ISO date (YYYY-MM-DD) for a timestamp-like value, or None when missing."""
    if value is None or pd.isna(value):
        return None
    return value.date().isoformat()


def award_row(r: dict[str, Any]) -> dict[str, Any]:
    """One award as returned by search_awards."""
    return {"ocid": r["ocid"], "award_id": r["award_id"], "buyer": r["buyer_name"],
            "supplier": r["supplier_name"], "title": r["title"], "category": r["category"],
            "procurement_method": r["procurement_method"], "amount_kes": r["raw_amount"],
            "award_date": date_text(r["award_date"])}


def severity_hint(metric: float | None, threshold: float) -> str:
    """'high' when a flag's metric is at least flags.high_multiple x its threshold, else 'medium'."""
    if metric is None:
        return "medium"
    return "high" if metric >= threshold * float(rules()["flags"]["high_multiple"]) else "medium"


def make_flag(rule: str, summary: str, severity: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """A tool-proposed flag. The agent needs no knowledge of each tool's result shape: it reads `result.flags`.

    A flag without evidence is never made (hard rule: unsourced flags are disqualifying)."""
    if not evidence:
        raise ValueError("a flag needs evidence")
    return {"rule": rule, "summary": summary, "severity_hint": severity,
            "ocids": sorted({e["ocid"] for e in evidence}), "evidence": evidence}
