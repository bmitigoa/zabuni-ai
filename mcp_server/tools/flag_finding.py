"""flag_finding: persist a sourced flag for human review (the server's first non-read-only tool).

The analysis database stays read-only. Flags go to a separate writable DuckDB file (rules: flags.db_path, env
ZABUNI_FLAGS_DB) in a `flags` table with status `pending_review`. A flag is rejected unless it carries evidence and
every cited award exists in the analysis database: an unsourced or invented citation can never be stored.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from mcp_server.common import ROOT, ToolInputError, clean_text, envelope, rows, rules
from mcp_server.registry import TOOLS, tool

EVIDENCE_KEYS = ("ocid", "award_id", "field", "value")
FLAG_ID_HEX_CHARS = 12          # length of the random part of a flag id
ERRORS_SHOWN = 5                # missing citations named in an error message
SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    flag_id         VARCHAR PRIMARY KEY,
    created_at      TIMESTAMP NOT NULL,
    run_id          VARCHAR,
    ocid            VARCHAR NOT NULL,
    rule            VARCHAR NOT NULL,
    severity        VARCHAR NOT NULL,
    explanation     VARCHAR NOT NULL,
    evidence_json   VARCHAR NOT NULL,
    dedupe_key      VARCHAR NOT NULL,
    status          VARCHAR NOT NULL,
    reviewer        VARCHAR,
    decision_reason VARCHAR,
    decided_at      TIMESTAMP
)"""


def flags_db_path() -> Path:
    """Where flags are stored (env ZABUNI_FLAGS_DB, else rules flags.db_path under the project root)."""
    return Path(os.environ.get("ZABUNI_FLAGS_DB") or ROOT / rules()["flags"]["db_path"])


def _clean_evidence(evidence: Any) -> list[dict[str, Any]]:
    if not isinstance(evidence, list) or not evidence:
        raise ToolInputError("A flag needs evidence: a non-empty list of {ocid, award_id, field, value} items "
                             "taken from tool results. Unsourced flags are rejected.")
    out = []
    for i, item in enumerate(evidence):
        if not isinstance(item, dict) or any(k not in item for k in EVIDENCE_KEYS):
            raise ToolInputError(f"evidence[{i}] must have the keys {list(EVIDENCE_KEYS)}.")
        if not (clean_text(item["ocid"]) and clean_text(item["award_id"]) and clean_text(item["field"])):
            raise ToolInputError(f"evidence[{i}] needs a non-empty ocid, award_id and field.")
        out.append({k: item[k] for k in EVIDENCE_KEYS})
    return out


def _verify_citations(evidence: list[dict[str, Any]]) -> None:
    """Every cited (ocid, award_id) must exist in the analysis database."""
    missing = []
    for pair in sorted({(e["ocid"], e["award_id"]) for e in evidence}):
        if not rows("SELECT 1 FROM awards WHERE ocid = ? AND award_id = ?", list(pair)):
            missing.append(f"{pair[0]} / {pair[1]}")
    if missing:
        raise ToolInputError("Evidence cites award(s) that do not exist in the OCDS data: " + "; ".join(missing[:ERRORS_SHOWN]))


@tool(read_only=False, role="flag_writer")
def flag_finding(ocid: str, rule: str, severity: str, explanation: str, evidence: list[dict[str, Any]],
                 run_id: str | None = None) -> dict[str, Any]:
    """Record a finding as a flag awaiting human review. Call it only for a finding supported by tool results.

    `ocid` is the main OCDS process the flag is about and must appear in `evidence`. `rule` is the name of the
    analysis tool whose result supports the flag. `severity` is one of [[flags.severities|list]]. `explanation` is a
    plain-language statement of what was found (at least [[flags.min_explanation_chars]] characters); it must not
    recommend or award anything. `evidence` is a non-empty list of {ocid, award_id, field, value} items copied from the
    evidence of tool results; every cited award must exist in the OCDS data or the flag is rejected. `run_id` links
    the flag to an audit-log run. The flag is stored with status [[flags.status_pending]]: a human reviewer decides
    whether it enters the memo. Calling it again with the same rule and evidence returns the existing flag instead of
    creating a duplicate.
    """
    fl = rules()["flags"]
    params = {"ocid": ocid, "rule": rule, "severity": severity, "run_id": run_id, "n_evidence": len(evidence or [])}
    spec = TOOLS.get((rule or "").strip())
    if spec is None or not spec.read_only:
        raise ToolInputError(f"rule must be the name of an analysis tool, one of "
                             f"{sorted(n for n, s in TOOLS.items() if s.read_only)}.")
    sev = (severity or "").strip().lower()
    if sev not in fl["severities"]:
        raise ToolInputError(f"severity must be one of {fl['severities']}.")
    text = (explanation or "").strip()
    if len(text) < int(fl["min_explanation_chars"]):
        raise ToolInputError(f"explanation must be at least {fl['min_explanation_chars']} characters.")
    ev_items = _clean_evidence(evidence)
    oc = clean_text(ocid)
    if not oc or oc not in {e["ocid"] for e in ev_items}:
        raise ToolInputError("ocid must be one of the ocids cited in evidence.")
    _verify_citations(ev_items)

    key = hashlib.sha1(json.dumps([spec.name, sorted(json.dumps(e, sort_keys=True, default=str) for e in ev_items)]
                                  ).encode("utf-8")).hexdigest()
    path = flags_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        con.execute(SCHEMA)
        existing = con.execute("SELECT flag_id, status FROM flags WHERE dedupe_key = ?", [key]).fetchone()
        if existing:
            result = {"flag_id": existing[0], "status": existing[1], "created": False, "ocid": oc, "rule": spec.name}
            warnings = [f"An identical flag already exists ({existing[0]}, status {existing[1]}); not created again."]
        else:
            flag_id = "flag_" + uuid.uuid4().hex[:FLAG_ID_HEX_CHARS]
            con.execute("INSERT INTO flags (flag_id, created_at, run_id, ocid, rule, severity, explanation, "
                        "evidence_json, dedupe_key, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [flag_id, datetime.now(timezone.utc).replace(tzinfo=None), run_id, oc, spec.name, sev, text,
                         json.dumps(ev_items, default=str), key, fl["status_pending"]])
            result = {"flag_id": flag_id, "status": fl["status_pending"], "created": True, "ocid": oc, "rule": spec.name}
            warnings = []
    except duckdb.IOException as exc:
        raise ToolInputError(f"The flags database is locked or unavailable ({path}): {exc}") from exc
    finally:
        con.close()
    params.update(severity=sev, rule=spec.name)
    return envelope(result, ev_items, params, warnings)
