"""
Zabuni AI - OCDS loader
Loads the PPRA Kenya OCDS snapshot (JSONL, gzip) into DuckDB.

Source: PPRA Kenya via Open Contracting Data Registry (publication 147)
https://data.open-contracting.org/en/publication/147
Snapshot only - never fetch live tender data.

Usage:
    python data/loader.py                       # default paths
    python data/loader.py --src data/raw/full.jsonl.gz --db data/zabuni.duckdb
"""
import argparse
import gzip
import json
import re
from pathlib import Path

import duckdb
import pandas as pd

# Award dates outside this window are data-entry junk (e.g. year 24, 204) -> set to NULL
MIN_YEAR, MAX_YEAR = 2018, 2026

# Heuristic buyer classification from the raw buyer name (first match wins). Used only to
# build comparable cohorts for price benchmarks; not an official classification.
BUYER_TYPE_SQL = """
    CASE
      WHEN regexp_matches(UPPER(t.buyer_name), 'UNIVERSIT|COLLEGE|POLYTECHNIC|TECHNICAL|TRAINING|SCHOOL|INSTITUTE') THEN 'education'
      WHEN regexp_matches(UPPER(t.buyer_name), 'HOSPITAL|HEALTH|MEDICAL|KEMSA|CLINIC') THEN 'health'
      WHEN regexp_matches(UPPER(t.buyer_name), 'COUNTY|MUNICIPAL|CITY COUNCIL') THEN 'county_government'
      WHEN regexp_matches(UPPER(t.buyer_name), 'AUTHORITY|COMMISSION|COUNCIL|BOARD|AGENCY|MINISTRY|DEPARTMENT|BUREAU|FUND|SERVICE|TRIBUNAL') THEN 'state_agency'
      WHEN regexp_matches(UPPER(t.buyer_name), 'COMPANY|CORPORATION|LIMITED|LTD|SACCO|BANK') THEN 'state_corporation'
      ELSE 'other'
    END"""


def norm(name):
    """Normalise organisation names for matching (IDs in this dataset are unreliable)."""
    if not name:
        return None
    n = re.sub(r"[^A-Z0-9 ]", " ", str(name).upper())
    n = re.sub(r"\b(LIMITED|LTD|CO|COMPANY|ENTERPRISES?|THE)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip() or None


def unwrap(obj):
    """Registry lines may be a compiled release or a record wrapper."""
    if "compiledRelease" in obj:
        return obj["compiledRelease"]
    if "releases" in obj and obj["releases"]:
        return obj["releases"][-1]
    return obj


def g(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def load(src: Path):
    tenders, awards, award_suppliers = [], [], []
    with gzip.open(src, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = unwrap(json.loads(line))
            ocid = r.get("ocid")
            t = r.get("tender") or {}
            buyer_name = g(r, "buyer", "name")
            tenders.append({
                "ocid": ocid,
                "release_id": r.get("id"),
                "release_date": r.get("date"),
                "buyer_id": g(r, "buyer", "id"),
                "buyer_name": buyer_name,
                "buyer_norm": norm(buyer_name),
                "tender_id": t.get("id"),
                "title": t.get("title"),
                "description": t.get("description"),
                "status": t.get("status"),
                "value_amount": g(t, "value", "amount"),
                "currency": g(t, "value", "currency"),
                "procurement_method": t.get("procurementMethod"),
                "procurement_method_details": t.get("procurementMethodDetails"),
                "category": t.get("mainProcurementCategory"),
                "period_start": g(t, "tenderPeriod", "startDate"),
                "period_end": g(t, "tenderPeriod", "endDate"),
                "number_of_tenderers": t.get("numberOfTenderers"),
            })
            # contracts[] carries the signing date; link to awards via awardID
            signed = {k.get("awardID"): k for k in r.get("contracts") or []}
            for a in r.get("awards") or []:
                k = signed.get(a.get("id")) or {}
                sups = a.get("suppliers") or []
                awards.append({
                    "ocid": ocid,
                    "award_id": a.get("id"),
                    # snapshot has no awards[].date; use contract signing date,
                    # else the award's contract-period start (see docs/data_profile.txt)
                    "award_date": k.get("dateSigned") or g(a, "contractPeriod", "startDate"),
                    "status": a.get("status"),
                    "amount": g(a, "value", "amount"),
                    "currency": g(a, "value", "currency"),
                    "title": a.get("title"),
                    "n_suppliers": len(sups),
                })
                for s in sups:  # company-level only; no contact points loaded
                    award_suppliers.append({
                        "ocid": ocid,
                        "award_id": a.get("id"),
                        "supplier_id": s.get("id"),
                        "supplier_name": s.get("name"),
                        "supplier_norm": norm(s.get("name")),
                    })
    return pd.DataFrame(tenders), pd.DataFrame(awards), pd.DataFrame(award_suppliers)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/raw/full.jsonl.gz")
    ap.add_argument("--db", default="data/zabuni.duckdb")
    args = ap.parse_args()

    tenders, awards, award_suppliers = load(Path(args.src))
    con = duckdb.connect(args.db)
    for name, df in [("tenders", tenders), ("awards", awards), ("award_suppliers", award_suppliers)]:
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
    # typed convenience view used by the MCP tools
    con.execute(f"""
        CREATE OR REPLACE VIEW award_facts AS
        WITH dated AS (
            SELECT a.*, TRY_CAST(a.award_date AS TIMESTAMP) AS d FROM awards a
        )
        SELECT a.ocid, a.award_id,
               CASE WHEN YEAR(a.d) BETWEEN {MIN_YEAR} AND {MAX_YEAR} THEN a.d END AS award_date,
               TRY_CAST(a.amount AS DOUBLE)             AS award_amount,
               a.currency,
               t.buyer_name, t.buyer_norm, {BUYER_TYPE_SQL} AS buyer_type, t.title, t.category,
               t.procurement_method, t.procurement_method_details,
               TRY_CAST(t.value_amount AS DOUBLE)       AS tender_amount,
               TRY_CAST(t.period_start AS TIMESTAMP)    AS period_start,
               TRY_CAST(t.period_end AS TIMESTAMP)      AS period_end,
               s.supplier_name, s.supplier_norm
        FROM dated a
        LEFT JOIN tenders t USING (ocid)
        LEFT JOIN award_suppliers s ON s.ocid = a.ocid AND s.award_id IS NOT DISTINCT FROM a.award_id
    """)
    for name in ["tenders", "awards", "award_suppliers", "award_facts"]:
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"{name:16s} {n:>10,} rows")
    con.close()
    print(f"\nSaved -> {args.db}")


if __name__ == "__main__":
    main()
