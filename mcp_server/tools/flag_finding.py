"""flag_finding: persist a sourced flag for human review (a write tool; the analysis database stays read-only).

Flags go to the separate flags database (see mcp_server/flagstore.py) with status `pending_review`. A flag is rejected
unless it carries evidence and every cited award exists in the analysis database: an unsourced or invented citation can
never be stored. An identical flag (same rule, same cited records) is NOT created again: the existing flag is returned
with its current status, so a human decision survives a re-run, and a REJECTED flag is remembered and not re-raised.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import duckdb

from mcp_server import flagstore
from mcp_server.common import ToolInputError, clean_text, envelope, rules
from mcp_server.registry import TOOLS, tool


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
    whether it enters the memo. Calling it again with the same rule and evidence returns the existing flag with its
    current status (including a previous rejection) instead of creating a duplicate.
    """
    fl = rules()["flags"]
    params = {"ocid": ocid, "rule": rule, "severity": severity, "run_id": run_id, "n_evidence": len(evidence or [])}
    spec = TOOLS.get((rule or "").strip())
    if spec is None or not spec.read_only or spec.role:
        raise ToolInputError(f"rule must be the name of an analysis tool, one of "
                             f"{sorted(n for n, s in TOOLS.items() if s.read_only and not s.role)}.")
    sev = (severity or "").strip().lower()
    if sev not in fl["severities"]:
        raise ToolInputError(f"severity must be one of {fl['severities']}.")
    text = (explanation or "").strip()
    if len(text) < int(fl["min_explanation_chars"]):
        raise ToolInputError(f"explanation must be at least {fl['min_explanation_chars']} characters.")
    ev_items = flagstore.clean_evidence(evidence)
    oc = clean_text(ocid)
    if not oc or oc not in {e["ocid"] for e in ev_items}:
        raise ToolInputError("ocid must be one of the ocids cited in evidence.")
    flagstore.verify_citations(ev_items)

    key = hashlib.sha1(json.dumps([spec.name, sorted(json.dumps(e, sort_keys=True, default=str) for e in ev_items)]
                                  ).encode("utf-8")).hexdigest()
    try:
        with flagstore.connect() as con:
            existing = con.execute("SELECT flag_id, status, decision_reason FROM flags WHERE dedupe_key = ?", [key]).fetchone()
            if existing:
                result = {"flag_id": existing[0], "status": existing[1], "created": False, "ocid": oc, "rule": spec.name}
                note = f"An identical flag already exists ({existing[0]}, status {existing[1]}); not created again."
                if existing[1] == fl["status_rejected"]:
                    note = (f"This flag ({existing[0]}) was REJECTED by a reviewer earlier (reason: {existing[2]}); "
                            "it is remembered and not re-raised.")
                warnings = [note]
            else:
                flag_id = "flag_" + uuid.uuid4().hex[:flagstore.ID_HEX_CHARS]
                con.execute("INSERT INTO flags (flag_id, created_at, run_id, ocid, rule, severity, explanation, "
                            "evidence_json, dedupe_key, status, clarification_rounds) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
                            [flag_id, datetime.now(timezone.utc).replace(tzinfo=None), run_id, oc, spec.name, sev, text,
                             json.dumps(ev_items, default=str), key, fl["status_pending"]])
                result = {"flag_id": flag_id, "status": fl["status_pending"], "created": True, "ocid": oc, "rule": spec.name}
                warnings = []
    except duckdb.IOException as exc:
        raise ToolInputError(f"The flags database is locked or unavailable ({flagstore.db_path()}): {exc}") from exc
    params.update(severity=sev, rule=spec.name)
    return envelope(result, ev_items, params, warnings)
