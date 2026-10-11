"""The flags database: the ONLY writable store in zabuni-mcp (the analysis database stays read-only).

One DuckDB file (rules: flags.db_path, env ZABUNI_FLAGS_DB) holds
  flags               one row per flag, with its current status (pending_review, approved, rejected, clarification_requested)
  flag_decisions      append-only history of every human decision (reviewer ROLE, never a name; a reason is required)
  flag_clarifications a reviewer's question and the agent's answer (with any new evidence)
  memos               every evaluation memo drafted, with the flag ids it was built from

Connections are opened per call and closed afterwards so other processes (the review CLI, another run) can read the file.
`ensure_schema` also migrates a Day 3 file in place.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import duckdb

from mcp_server.common import ROOT, rules

ID_HEX_CHARS = 12          # length of the random part of a generated id

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS flags (
        flag_id VARCHAR PRIMARY KEY, created_at TIMESTAMP NOT NULL, run_id VARCHAR, ocid VARCHAR NOT NULL,
        rule VARCHAR NOT NULL, severity VARCHAR NOT NULL, explanation VARCHAR NOT NULL, evidence_json VARCHAR NOT NULL,
        dedupe_key VARCHAR NOT NULL, status VARCHAR NOT NULL, reviewer VARCHAR, decision_reason VARCHAR,
        decided_at TIMESTAMP)""",
    "ALTER TABLE flags ADD COLUMN IF NOT EXISTS clarification_rounds INTEGER DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS flag_decisions (
        decision_id VARCHAR PRIMARY KEY, flag_id VARCHAR NOT NULL, run_id VARCHAR, decided_at TIMESTAMP NOT NULL,
        decision VARCHAR NOT NULL, reason VARCHAR NOT NULL, reviewer_role VARCHAR NOT NULL, status_after VARCHAR NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS flag_clarifications (
        clarification_id VARCHAR PRIMARY KEY, flag_id VARCHAR NOT NULL, run_id VARCHAR, round INTEGER NOT NULL,
        asked_at TIMESTAMP NOT NULL, question VARCHAR NOT NULL, response VARCHAR, responded_at TIMESTAMP,
        evidence_json VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS memos (
        memo_id VARCHAR PRIMARY KEY, run_id VARCHAR, created_at TIMESTAMP NOT NULL, flag_ids_json VARCHAR NOT NULL,
        markdown VARCHAR NOT NULL, sha1 VARCHAR NOT NULL)""",
]


def db_path() -> Path:
    """Where the flags are stored (env ZABUNI_FLAGS_DB, else rules flags.db_path under the project root)."""
    return Path(os.environ.get("ZABUNI_FLAGS_DB") or ROOT / rules()["flags"]["db_path"])


def ensure_schema(con: duckdb.DuckDBPyConnection) -> None:
    for statement in SCHEMA:
        con.execute(statement)


@contextmanager
def connect(read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection | None]:
    """Writable (creates the file and schema) or read-only connection; read-only yields None when no file exists yet."""
    path = db_path()
    if read_only and not path.is_file():
        yield None
        return
    if not read_only:
        path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    try:
        if not read_only:
            ensure_schema(con)
        yield con
    finally:
        con.close()


COLUMNS = ["flag_id", "created_at", "run_id", "ocid", "rule", "severity", "explanation", "evidence_json", "status",
           "reviewer", "decision_reason", "decided_at", "clarification_rounds"]


def row_to_flag(row: tuple[Any, ...]) -> dict[str, Any]:
    """A flags row as a dict with the evidence parsed."""
    flag = dict(zip(COLUMNS, row))
    flag["evidence"] = json.loads(flag.pop("evidence_json"))
    flag["created_at"] = str(flag["created_at"])
    flag["decided_at"] = str(flag["decided_at"]) if flag["decided_at"] else None
    flag["clarification_rounds"] = int(flag["clarification_rounds"] or 0)
    return flag


def fetch_flag(con: duckdb.DuckDBPyConnection, flag_id: str) -> dict[str, Any] | None:
    row = con.execute(f"SELECT {', '.join(COLUMNS)} FROM flags WHERE flag_id = ?", [flag_id]).fetchone()
    return row_to_flag(row) if row else None


def fetch_clarifications(con: duckdb.DuckDBPyConnection, flag_id: str) -> list[dict[str, Any]]:
    """Questions and answers for a flag, oldest first."""
    cols = ["round", "asked_at", "question", "response", "responded_at", "evidence_json"]
    out = []
    for r in con.execute(f"SELECT {', '.join(cols)} FROM flag_clarifications WHERE flag_id = ? ORDER BY round", [flag_id]).fetchall():
        d = dict(zip(cols, r))
        d["evidence"] = json.loads(d.pop("evidence_json") or "[]")
        d["asked_at"], d["responded_at"] = str(d["asked_at"]), (str(d["responded_at"]) if d["responded_at"] else None)
        out.append(d)
    return out


# ---------------------------------------------------------------- evidence validation (shared by flag_finding and attach_clarification)
EVIDENCE_KEYS = ("ocid", "award_id", "field", "value")
ERRORS_SHOWN = 5           # missing citations named in an error message


def clean_evidence(evidence: Any, *, required: bool = True) -> list[dict[str, Any]]:
    """Validate a list of {ocid, award_id, field, value} items; an unsourced flag has no evidence and is rejected."""
    from mcp_server.common import ToolInputError, clean_text      # local: avoids an import cycle at module load

    if evidence in (None, []) and not required:
        return []
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


def verify_citations(evidence: list[dict[str, Any]]) -> None:
    """Every cited (ocid, award_id) must exist in the analysis database."""
    from mcp_server.common import ToolInputError, rows

    missing = []
    for pair in sorted({(e["ocid"], e["award_id"]) for e in evidence}):
        if not rows("SELECT 1 FROM awards WHERE ocid = ? AND award_id = ?", list(pair)):
            missing.append(f"{pair[0]} / {pair[1]}")
    if missing:
        raise ToolInputError("Evidence cites award(s) that do not exist in the OCDS data: " + "; ".join(missing[:ERRORS_SHOWN]))
