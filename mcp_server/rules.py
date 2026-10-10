"""Zabuni AI - rule thresholds, limits and agent policy: the single source of truth.

Everything that is a *number or a word list that changes behaviour* lives here, not in tool code. The tools read it
through `load_rules()`, the agent reads it through the MCP resource `rules://ppada`, and tool descriptions are rendered
from it (placeholders like `[[price_benchmark.min_comparables]]`, see registry.render_doc).

Every threshold is a screening default, NOT a legal limit, and PROVISIONAL: it must be validated with a procurement
practitioner (docs/user_engagement.md). Override any value by pointing ZABUNI_RULES_FILE at a JSON file with the same
structure (partial files are merged over these defaults); a wrong path is an error, never silently ignored.

Human-readable description of each rule: docs/red_flags.md.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

PROVISIONAL = "provisional — to be validated with a procurement practitioner"

DEFAULT_RULES: dict[str, Any] = {
    "status": PROVISIONAL,
    "sources": [
        "Kenya Public Procurement and Asset Disposal Act (PPADA), 2015 — procurement methods and the "
        "provision against splitting procurements (section numbers to be verified against the Act text)",
        "Open Contracting Partnership, 'Red Flags for Integrity: Giving the green light to open data solutions' (2016)",
    ],
    "data_cleaning": {
        "min_valid_amount_kes": 0.0,          # amounts <= this are treated as invalid (exclusive)
        "max_valid_amount_kes": 1e10,         # amounts above this are probable data-entry errors
        "min_year": 2018,                     # award dates outside [min_year, max_year] are set to NULL by the loader
        "max_year": 2026,                     # (the loader reads these two values from here: one source of truth)
    },
    "limits": {
        "search_max": 50,                     # search_awards rows per call
        "ranking_max": 20,                    # clusters / ranked buyers / suppliers per call
        "evidence_max_items": 100,            # evidence items per tool response
        "candidates_shown": 10,               # buyer names listed when a name is ambiguous
        "fuzzy_matches": 5,                   # similar buyer names suggested for an unknown name
        "fuzzy_cutoff": 0.5,                  # minimum similarity (0-1) for a suggested name
    },
    "categories": ["goods", "works", "services"],
    # Ordered: the first entry whose keywords occur in the (upper-cased) buyer name wins; no match -> default_buyer_type.
    # A heuristic used only to build comparable cohorts. The loader builds the buyer_type column from this list, so a
    # change here needs `python data/loader.py` to be re-run.
    "buyer_types": [
        {"type": "education", "keywords": ["UNIVERSIT", "COLLEGE", "POLYTECHNIC", "TECHNICAL", "TRAINING", "SCHOOL", "INSTITUTE"]},
        {"type": "health", "keywords": ["HOSPITAL", "HEALTH", "MEDICAL", "KEMSA", "CLINIC"]},
        {"type": "county_government", "keywords": ["COUNTY", "MUNICIPAL", "CITY COUNCIL"]},
        {"type": "state_agency", "keywords": ["AUTHORITY", "COMMISSION", "COUNCIL", "BOARD", "AGENCY", "MINISTRY",
                                              "DEPARTMENT", "BUREAU", "FUND", "SERVICE", "TRIBUNAL"]},
        {"type": "state_corporation", "keywords": ["COMPANY", "CORPORATION", "LIMITED", "LTD", "SACCO", "BANK"]},
    ],
    "default_buyer_type": "other",
    "price_benchmark": {
        "min_comparables": 10,                # fewer -> insufficient_comparables flag (agent widens search)
        "flag_ratio_to_median": 3.0,          # award >= 3x cohort median is flagged for review
        "max_comparables_cited": 10,
        "iqr_fence_multiplier": 1.5,          # Tukey fence: above Q3 + 1.5 x IQR counts as an outlier
        "title_token_min_len": 4,             # shortest title word used to build a comparable cohort
        "auto_keyword_candidates": 8,         # title words tried when choosing cohort keywords automatically
        "auto_keyword_max": 2,                # most keywords combined automatically
        "stopwords": ["supply", "supplies", "delivery", "deliver", "provision", "proposed", "services", "service",
                      "works", "with", "various", "from", "assorted", "items", "tender", "contract", "financial",
                      "year", "general", "limited", "that", "this", "their", "other", "within", "county",
                      "government", "ministry"],
    },
    "splitting": {
        "window_days": 30,
        "window_days_max": 365,               # largest window a caller may ask for
        "min_awards": 3,
        "min_awards_floor": 2,                # smallest min_awards a caller may ask for
        "min_awards_max": 50,                 # largest min_awards a caller may ask for
        "near_below_ratio": 0.5,              # an award is 'just below' T if near_below_ratio*T <= amount < T
        "thresholds_kes": [500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000],  # illustrative, unverified
    },
    "noncompetitive_method": {
        "direct_methods": ["direct"],         # OCDS procurementMethod values
        "restricted_methods": ["selective"],  # OCDS 'selective' = restricted tendering
        "flag_ratio_to_baseline": 2.0,        # buyer direct+restricted share >= 2x national baseline
        "min_noncompetitive_awards": 10,      # direct + restricted awards needed before a buyer is flagged
        "min_awards_for_ranking": 20,         # used when ranking buyers (no buyer given)
    },
    "supplier_concentration": {
        "min_awards": 30,                     # buyer needs >= this many supplier-named awards
        "flag_share": 0.25,                   # top supplier >= 25 % by count or by value
    },
    "evidence": {
        "noncompetitive_buyer": 10,           # non-open awards cited for one buyer
        "noncompetitive_ranking": 3,          # non-open awards cited per ranked buyer
        "supplier_awards": 3,                 # largest awards cited per supplier
    },
    "flags": {
        "severities": ["low", "medium", "high"],
        "high_multiple": 2.0,                 # a metric >= 2x its flag threshold gets severity_hint 'high'
        "min_explanation_chars": 20,          # flag_finding rejects shorter explanations
        "db_path": "data/flags.duckdb",       # writable flags store (the analysis database stays read-only)
        "status_pending": "pending_review",
    },
    "agent": {
        "max_steps": 12,                      # tool calls the Investigator may make in one run (step cap)
        "max_plan_steps": 8,                  # steps the Planner may propose
        "max_corrections": 1,                 # times a failing call is retried with corrected arguments
        "max_recoveries_per_step": 2,         # times an insufficient_comparables step is widened
        "audit_output_max_chars": 20000,      # tool output kept in the audit log before truncation
        "tool_timeout_s": 120.0,              # one MCP tool call may take at most this long
        "report_awards_listed": 5,            # cited awards listed per flag in the draft summary
        "llm_max_retries": 4,                 # retries on rate limit / malformed structured output
        "llm_backoff_base_s": 5.0,            # rate-limit waits: 5, 10, 20, 40 s (spans a one-minute limit window)
        "llm_max_wait_s": 90.0,               # longest single wait honoured when the provider says "try again in N s"
        "llm_max_tokens": 900,                # cap on each reply; Groq's free tier allows only 1000 output tokens/minute
        "llm_temperature": 0.0,               # deterministic-as-possible planning and synthesis
        "llm_timeout_s": 60.0,
        # Hard rule 2: a configured model's name must contain one of these open-weights family names.
        "open_weights_families": ["llama", "qwen", "gpt-oss", "gemma", "mistral", "mixtral", "deepseek", "phi"],
        "flag_writer_role": "flag_writer",    # role (from tool metadata) of the tool that persists flags
        "synth_max_candidates": 8,            # candidate flags the Synthesiser is asked to word (the rest keep the tool's text)
        # Argument NAMES shared by the tools; the Planner's scope (buyer, years) is injected into any step whose schema has
        # them but whose arguments omit them. Agent code never names a tool, only these argument conventions.
        "scope_args": {"buyer": "buyer", "years": "years", "date_from": "date_from", "date_to": "date_to"},
        "filesystem_write_tool_hint": "write",  # the external server's file-writing tool: name containing this
        # Follow-up policy as DATA (names of tools appear here, not in agent code). A rule applies only if its tool
        # was discovered on the server. Templates: $result.<path>, $args.<path>, $scope.buyer, $scope.years,
        # $item.<path> (inside foreach). An unresolved template drops that argument.
        "followups": [
            {"when_tool": "search_awards", "foreach": "result.awards", "limit": 3,
             "then": [{"tool": "compute_price_benchmark",
                       "args": {"ocid": "$item.ocid", "award_id": "$item.award_id"},
                       "reason": "price-check one of the awards just found"}]},
            {"when_tool": "compute_price_benchmark", "when": "result.flagged",
             "then": [{"tool": "detect_splitting",
                       "args": {"buyer": "$result.target.buyer", "supplier": "$result.target.supplier",
                                "years": "$scope.years"},
                       "reason": "price outlier: check whether this buyer split awards to this supplier"},
                      {"tool": "supplier_concentration",
                       "args": {"buyer": "$result.target.buyer", "years": "$scope.years"},
                       "reason": "price outlier: check this buyer's dependence on suppliers"}]},
        ],
    },
}


def load_rules() -> dict[str, Any]:
    """Return the defaults merged with the optional JSON override file (ZABUNI_RULES_FILE).

    A path that does not exist is an error: a typo must not silently fall back to the defaults.
    """
    rules = copy.deepcopy(DEFAULT_RULES)
    path = os.environ.get("ZABUNI_RULES_FILE")
    if path:
        if not Path(path).is_file():
            raise FileNotFoundError(f"ZABUNI_RULES_FILE points to a file that does not exist: {path}")
        _merge(rules, json.loads(Path(path).read_text(encoding="utf-8")))
    return rules


def _merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def buyer_type_names(rules: dict[str, Any]) -> list[str]:
    """All buyer types, including the default, in rule order."""
    return [t["type"] for t in rules["buyer_types"]] + [rules["default_buyer_type"]]
