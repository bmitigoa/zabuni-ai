"""Small helpers shared by the tool modules (kept out of common.py, which is about data access and envelopes)."""
from __future__ import annotations

from typing import Annotated, Any

import pandas as pd
from pydantic import Field

from mcp_server.common import ToolInputError, rules
from mcp_server.rules import DEFAULT_RULES, buyer_type_names

# Short, per-argument descriptions that end up in each tool's JSON schema. Clients (and the agent's compact catalogue) read
# these instead of the long docstring. Enumerations come from the default rules, so they cannot drift from them.
_CATEGORIES = " | ".join(DEFAULT_RULES["categories"])
_BUYER_TYPES = " | ".join(buyer_type_names(DEFAULT_RULES))
BuyerArg = Annotated[str | None, Field(description="Organisation name (partial ok); must identify one buyer")]
SupplierArg = Annotated[str | None, Field(description="Partial supplier name")]
KeywordArg = Annotated[str | None, Field(description="Word or phrase in the award title")]
YearsArg = Annotated[list[int] | None, Field(description="Calendar years, e.g. [2023, 2024]")]
LimitArg = Annotated[int, Field(description="Maximum rows returned")]
CategoryArg = Annotated[str | None, Field(description=_CATEGORIES)]
BuyerTypeArg = Annotated[str | None, Field(description=_BUYER_TYPES)]
DateArg = Annotated[str | None, Field(description="YYYY-MM-DD")]
SortArg = Annotated[str, Field(description="newest | largest")]
OcidArg = Annotated[str | None, Field(description="OCDS process id, taken from a search result")]
AwardIdArg = Annotated[str | None, Field(description="Award id inside that ocid")]
AmountArg = Annotated[float | None, Field(description="A KES amount to position against the cohort")]
AutoKeywordsArg = Annotated[bool, Field(description="false = compare on category only")]
WindowArg = Annotated[int | None, Field(description="Days within which awards count as one purchase")]
MinAwardsArg = Annotated[int | None, Field(description="Smallest cluster size")]
ThresholdsArg = Annotated[list[float] | None, Field(description="Approval thresholds in KES")]


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


def award_detail(ocid: str, award_id: str | None, buyer: Any, supplier: Any, date: Any, amount: Any, title: Any) -> dict[str, Any]:
    """The facts a reviewer needs about one flagged award (what, who, when, how much); amounts are raw KES numbers."""
    return {"ocid": ocid, "award_id": award_id, "buyer": (buyer or "").strip() or None, "supplier": supplier or None,
            "date": date if isinstance(date, str) or date is None else date_text(date), "amount_kes": None if amount is None else float(amount),
            "title": (title or "").strip() or None}


def validation_label(rule: str) -> str:
    """'provisional' unless a practitioner has validated this rule's thresholds (rules validation.validated_rules)."""
    v = rules()["validation"]
    return "validated" if rule in v["validated_rules"] else v["label"]


def threshold_text(rule: str, text: str) -> str:
    """A threshold sentence with its validation status attached, e.g. 'ratio >= 3x the median (provisional)'."""
    return f"{text} ({validation_label(rule)})"


def data_entry_hints(amount: float | None, ratio_to_median: float | None) -> list[str]:
    """Rule-based hint that an amount may be a data-entry error: extreme multiple of its comparison median, a large round
    number, or a run of nines. Thresholds are in rules data_quality (provisional)."""
    dq = rules()["data_quality"]
    if amount is None:
        return []
    digits = str(int(amount))
    extreme = ratio_to_median is not None and ratio_to_median >= float(dq["suspect_ratio_to_median"])
    round_number = amount >= float(dq["round_number_min_amount_kes"]) and amount % float(dq["round_number_modulus_kes"]) == 0
    nines = digits.endswith("9" * int(dq["nines_run_min_digits"]))
    return [dq["hint"]] if (extreme or round_number or nines) else []


def make_flag(rule: str, summary: str, severity: str, evidence: list[dict[str, Any]], *, subject: dict[str, Any] | None = None,
              metric: str | None = None, threshold: str | None = None, comparison: dict[str, Any] | None = None,
              hints: list[str] | None = None) -> dict[str, Any]:
    """A tool-proposed flag. The agent needs no knowledge of each tool's result shape: it reads `result.flags`.

    `detail` is the structured, tool-made description the review screens draw: the flagged subject (awards, or a buyer),
    the signal (metric, threshold with its validation status, comparison set) and data-entry hints. A flag without evidence is
    never made (hard rule: unsourced flags are disqualifying)."""
    if not evidence:
        raise ValueError("a flag needs evidence")
    signal = {"rule": rule, "summary": summary, "severity": severity, "metric": metric,
              "threshold": threshold_text(rule, threshold) if threshold else None, "comparison": comparison,
              "caveat": rules()["flag_caveats"].get(rule)}
    detail = {"subject": subject or {"kind": "awards", "name": None, "awards": []}, "signals": [signal], "hints": list(hints or [])}
    return {"rule": rule, "summary": summary, "severity_hint": severity,
            "ocids": sorted({e["ocid"] for e in evidence}), "evidence": evidence, "detail": detail}
