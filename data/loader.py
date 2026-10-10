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
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.rules import load_rules  # noqa: E402  (year window and buyer types live in ONE place: rules.py)


def buyer_type_sql(rules: dict) -> str:
    """SQL CASE that classifies a buyer by keywords in its raw name (first matching rule wins).

    Built from rules['buyer_types'], so editing the keyword lists there and re-running this loader is all it takes.
    A heuristic used only to build comparable cohorts for price benchmarks; not an official classification."""
    def lit(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    whens = "\n".join(
        f"      WHEN regexp_matches(UPPER(t.buyer_name), {lit('|'.join(r['keywords']))}) THEN {lit(r['type'])}"
        for r in rules["buyer_types"])
    return f"CASE\n{whens}\n      ELSE {lit(rules['default_buyer_type'])}\n    END"


def norm(name):
    """Normalise organisation names for matching (IDs in this dataset are unreliable).

    Upper-case, punctuation to spaces, drop company-form words (LIMITED, LTD, CO, COMPANY, ENTERPRISE(S), THE).
    A hyphenated "CO-" prefix is part of the word (CO-OPERATIVE -> COOPERATIVE, CO-ORDINATION -> COORDINATION), so
    it must be joined BEFORE the hyphen becomes a space and "CO" is stripped as a company suffix (the flaw that
    produced "OPERATIVE BANK"). A standalone "Co" / "& Co." is still removed, as before."""
    if not name:
        return None
    n = str(name).upper()
    n = re.sub(r"\bCO\s*-\s*(?!LTD\b|LIMITED\b)(?=[A-Z])", "CO", n)
    n = re.sub(r"\bCO\s+(?=OPERATIVE)", "CO", n)
    n = re.sub(r"[^A-Z0-9 ]", " ", n)
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

    rules = load_rules()
    min_year, max_year = rules["data_cleaning"]["min_year"], rules["data_cleaning"]["max_year"]
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
               CASE WHEN YEAR(a.d) BETWEEN {min_year} AND {max_year} THEN a.d END AS award_date,
               TRY_CAST(a.amount AS DOUBLE)             AS award_amount,
               a.currency,
               t.buyer_name, t.buyer_norm, {buyer_type_sql(rules)} AS buyer_type, t.title, t.category,
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
