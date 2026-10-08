"""Shared helpers for the zabuni-mcp tools: DB access, input validation, buyer resolution,
and the standard response envelope {result, evidence, params_used, warnings}.

Only company/organisation names are ever handled here; the database holds no personal data.
"""
from __future__ import annotations

import difflib
import os
import sys
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.loader import norm  # noqa: E402  (same normaliser the loader used, so names match)
from mcp_server.rules import load_rules  # noqa: E402

DB_PATH = Path(os.environ.get("ZABUNI_DB", ROOT / "data" / "zabuni.duckdb"))
MAX_EVIDENCE = 100

_con: duckdb.DuckDBPyConnection | None = None


class ToolInputError(ValueError):
    """Bad tool input; reported to the agent as a message, never as a crash."""


def cursor() -> duckdb.DuckDBPyConnection:
    """Return a cursor on the shared read-only connection (raises a clear error if DB is missing)."""
    global _con
    if _con is None:
        if not DB_PATH.is_file():
            raise ToolInputError(
                f"Database not found at {DB_PATH}. Build it first: python data/loader.py"
            )
        _con = duckdb.connect(str(DB_PATH), read_only=True)
    return _con.cursor()


def rules() -> dict[str, Any]:
    return load_rules()


def rows(sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Run a query and return a list of dicts."""
    cur = cursor()
    cur.execute(sql, params or [])
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def frame(sql: str, params: list[Any] | None = None):
    """Run a query and return a pandas DataFrame."""
    return cursor().execute(sql, params or []).df()


# ---------------------------------------------------------------- response envelope
def envelope(result: Any, evidence: list[dict[str, Any]] | None = None,
             params: dict[str, Any] | None = None, warnings: list[str] | None = None) -> dict[str, Any]:
    """Standard tool response. Evidence is capped; the cap is announced in warnings."""
    ev = evidence or []
    warns = list(warnings or [])
    if len(ev) > MAX_EVIDENCE:
        warns.append(f"Evidence truncated to {MAX_EVIDENCE} of {len(ev)} items.")
        ev = ev[:MAX_EVIDENCE]
    return {"result": result, "evidence": ev, "params_used": params or {}, "warnings": warns}


def error(message: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Envelope for bad input / unusable request: no result, no evidence, explicit message."""
    return envelope({"error": message}, [], params, [message])


def ev(ocid: str, award_id: str | None, field: str, value: Any) -> dict[str, Any]:
    """One evidence item pointing at a field of an OCDS award."""
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    return {"ocid": ocid, "award_id": award_id, "field": field, "value": value}


# ---------------------------------------------------------------- input validation
def clean_text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def clean_years(years: list[int] | None, cfg: dict[str, Any]) -> list[int] | None:
    """Validate a list of calendar years against the data window; None means all years."""
    if not years:
        return None
    lo, hi = cfg["min_year"], cfg["max_year"]
    out = sorted({int(y) for y in years})
    bad = [y for y in out if not lo <= y <= hi]
    if bad:
        raise ToolInputError(f"years {bad} outside the data window {lo}-{hi}.")
    return out


def clean_date(value: str | None, name: str) -> str | None:
    value = clean_text(value)
    if value is None:
        return None
    try:
        return cursor().execute("SELECT CAST(? AS DATE)", [value]).fetchone()[0].isoformat()
    except Exception as exc:  # duckdb ConversionException etc.
        raise ToolInputError(f"{name}={value!r} is not a valid date; use YYYY-MM-DD.") from exc


def base_cte(valid_only: bool = True) -> str:
    """CTE `a`: one row per award with cleaned amounts. Invalid amounts (<=0 or > max) become NULL
    in `amount` and are flagged with amount_valid=false; `valid_only` drops them."""
    c = rules()["data_cleaning"]
    where = "WHERE amount IS NOT NULL" if valid_only else ""
    return f"""
    a AS (
      SELECT * FROM (
        SELECT f.ocid, f.award_id, f.award_date, YEAR(f.award_date) AS yr, f.buyer_name, f.buyer_norm,
               f.buyer_type, f.title, f.category, f.procurement_method, f.supplier_name, f.supplier_norm,
               f.award_amount AS raw_amount,
               CASE WHEN f.award_amount > {c['min_valid_amount_kes']} AND f.award_amount <= {c['max_valid_amount_kes']}
                    THEN f.award_amount END AS amount
        FROM award_facts f
      ) {where}
    )"""


# ---------------------------------------------------------------- buyer resolution
def resolve_buyer(buyer: str) -> tuple[str | None, list[str], str | None]:
    """Map free text to one normalised buyer name.

    Returns (buyer_norm, candidates, problem). Exact normalised match wins; otherwise a unique
    substring match; otherwise a problem message with candidate/similar names for the agent.
    """
    q = norm(buyer)
    if not q:
        return None, [], "buyer is empty."
    names = [r["buyer_norm"] for r in rows(
        "SELECT DISTINCT buyer_norm FROM award_facts WHERE buyer_norm IS NOT NULL")]
    if q in names:
        return q, [q], None
    subs = sorted(n for n in names if q in n)
    if len(subs) == 1:
        return subs[0], subs, None
    if len(subs) > 1:
        return None, subs[:10], f"buyer {buyer!r} is ambiguous ({len(subs)} matches); use a more specific name."
    similar = difflib.get_close_matches(q, names, n=5, cutoff=0.5)
    return None, similar, f"no buyer matches {buyer!r}."


def buyer_problem(buyer: str, params: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve a buyer; on failure return a ready-made error envelope listing candidates."""
    b, cands, problem = resolve_buyer(buyer)
    if b:
        return b, None
    out = error(problem or "unknown buyer.", params)
    out["result"]["candidates"] = cands
    return None, out


def year_filter(years: list[int] | None) -> str:
    return f" AND yr IN ({','.join(str(y) for y in years)})" if years else ""
