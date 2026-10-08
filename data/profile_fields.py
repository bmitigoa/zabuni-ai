"""
Zabuni AI - field profile
Shows which OCDS fields are actually populated, so we can confirm which
red-flag tools are feasible. Run after loader.py.

Usage:
    python data/profile_fields.py
"""
import duckdb

con = duckdb.connect("data/zabuni.duckdb", read_only=True)


def fill(table):
    cols = [c[0] for c in con.execute(f"DESCRIBE {table}").fetchall()]
    total = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    print(f"\n== {table} ({total:,} rows) - % filled ==")
    for c in cols:
        n = con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {c} IS NOT NULL AND CAST({c} AS VARCHAR) <> ''"
        ).fetchone()[0]
        print(f"  {c:28s} {100 * n / max(total, 1):6.1f}%")


def show(title, sql):
    print(f"\n== {title} ==")
    for row in con.execute(sql).fetchall():
        print("  ", row)


fill("tenders")
fill("awards")
fill("award_suppliers")

show("Procurement methods", """
    SELECT procurement_method, procurement_method_details, COUNT(*) n
    FROM tenders GROUP BY 1,2 ORDER BY n DESC LIMIT 15""")
show("Categories", "SELECT category, COUNT(*) n FROM tenders GROUP BY 1 ORDER BY n DESC")
show("Buyer types (heuristic, from buyer name)", "SELECT buyer_type, COUNT(*) n FROM award_facts GROUP BY 1 ORDER BY n DESC")
show("Currencies (awards)", "SELECT currency, COUNT(*) n FROM awards GROUP BY 1 ORDER BY n DESC")
show("Awards per year", """
    SELECT YEAR(award_date) y, COUNT(*) n FROM award_facts GROUP BY 1 ORDER BY 1""")
show("Award amount percentiles (KES)", """
    SELECT quantile_cont(award_amount, [0.01,0.25,0.5,0.75,0.99]) , MAX(award_amount)
    FROM award_facts WHERE award_amount > 0""")
show("Top 10 buyers by awards", """
    SELECT buyer_name, COUNT(*) n FROM award_facts GROUP BY 1 ORDER BY n DESC LIMIT 10""")
show("Sample titles", "SELECT title FROM tenders WHERE title IS NOT NULL USING SAMPLE 10")
con.close()
