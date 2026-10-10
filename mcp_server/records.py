"""Data behind the MCP resource `ocds://release/{ocid}`: the stored record used to cite a flag's source."""
from __future__ import annotations

from typing import Any

from mcp_server.common import clean_text, rows


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
