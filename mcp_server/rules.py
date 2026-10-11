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
        "status_approved": "approved",
        "status_rejected": "rejected",        # remembered: the same flag (same rule and cited records) is not re-raised
        "status_clarify": "clarification_requested",
    },
    "review": {
        # Human approval gate. Reviewers are recorded by ROLE, never by name (no personal data).
        "roles": ["procurement_officer", "internal_auditor", "committee_member", "oversight_reviewer", "demo_reviewer"],
        "decisions": ["approve", "reject", "clarify"],
        "min_reason_chars": 5,                # a reason is required for every decision
        "max_clarify_rounds": 2,              # re-investigations per flag before it must be decided
        "clarify_max_steps": 2,               # tool calls the agent may spend answering one clarification
        "interrupt_evidence_shown": 5,        # evidence items per flag shown in the pause payload / review CLI
    },
    "memo": {
        "evidence_listed_per_flag": 10,       # cited records listed per approved finding in the memo
        "disclaimer": "This memo does not recommend or award any tender. Every finding is a screening indicator from "
                      "published OCDS data, approved by a human reviewer, and may have a legitimate explanation.",
        "limitations": [
            "Bid documents are not published, so bid content, scoring and bidder qualifications cannot be assessed.",
            "The data has no tenderers, tender estimates or item-level detail; price comparisons use title words, category and "
            "buyer type only, and a cluster of awards may be genuinely different goods.",
            "Award dates are contract signing dates; amounts outside the valid range are excluded from statistics.",
            "Supplier-based checks cover only awards that name a supplier. All thresholds are provisional and unvalidated.",
        ],
    },
    "price_anomalies": {
        "min_cohort": 30,                     # comparable awards needed before the model is fitted
        "contamination": 0.01,                # expected share of anomalies
        "random_state": 42,                   # fixed seed: the same data always gives the same answer
        "n_estimators": 100,
        "min_ratio_to_median": 3.0,           # an anomaly must also be at least this multiple of the cohort median
        "max_anomalies": 10,
        "comparables_cited": 5,
    },
    "demo": {
        # Hosted-demo guard (the Streamlit app on Day 5 uses it)
        "runs_per_visitor_per_hour": 3,
        "expected_run_tokens": 8000,          # a run needs roughly this many tokens; checked against the estimated daily quota
        "quota_message": "The daily free-tier quota for the live model has been reached. Please watch the recorded demo "
                         "video instead; the live demo works again when the quota resets.",
        "rate_message": "You have reached the limit of live runs per hour for this demo. Please try again later or watch "
                        "the recorded demo video.",
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
        # --- run budget (the run stops cleanly and the memo says so)
        "max_flags_per_run": 10,              # at most this many flags are raised per run: the strongest (severity, then evidence count)
        "scope_request_message": "This request names no buyer, supplier or period, so every tool would scan all of Kenya. Add a buyer "
                                 "(for example a county or agency), a supplier or years, and run it again.",
        "max_total_tokens": 25000,            # LLM tokens (prompt + reply) per run; Ollama and Groq alike
        "synth_reserve_tokens": 4000,         # kept back so the Synthesiser can still run when the investigation is cut short
        "max_clarify_tokens": 6000,           # most tokens one clarification round may use
        # --- token economy
        "catalogue_brief_chars": 220,         # the Planner sees only the first sentence(s) of each tool description
        "ollama_num_ctx": 4096,               # context window requested from Ollama (its default may truncate prompts)
        "ollama_keep_alive": "15m",           # keep the local model loaded between calls
        # --- providers, fallback, quota, cost
        # Tried in order on a 429; separate quota per model. Checked on 12 Oct 2026 with `python -m agent.models`: the project's
        # Groq key lists NO Llama, Gemma, Mistral or Aya chat model (only Llama prompt-guard classifiers, which cannot chat), so
        # the only other open-weights chat models it can call are the gpt-oss family. gpt-oss-120b (open weights, OpenAI) is kept as
        # the fallback; it is not one of the challenge's named families, which the README documents.
        "llm_fallback_models": {"groq": ["openai/gpt-oss-120b"]},
        "groq_daily_token_limits": {"qwen/qwen3.8-27b": 200000},      # free tier as observed on 10 Oct 2026 (unknown: not listed)
        "quota_window_hours": 24,             # Groq's daily limit behaves like a rolling window (observed)
        "pricing": {
            # Pay-as-you-go equivalent in USD per 1M tokens, from console.groq.com/docs/models. The page shows no date; the
            # values were retrieved on 11 Oct 2026. The free tier itself costs nothing. Ollama (local) cost is 0.
            "as_of": "2026-10-11 (retrieval date; the source page is undated)",
            "source": "https://console.groq.com/docs/models",
            "groq": {"qwen/qwen3.8-27b": {"input_per_mtok": 0.80, "output_per_mtok": 4.00},
                     "openai/gpt-oss-120b": {"input_per_mtok": 0.15, "output_per_mtok": 0.60},
                     "openai/gpt-oss-20b": {"input_per_mtok": 0.075, "output_per_mtok": 0.30}},
        },
        "review_round_allowance": 20,         # extra graph visits allowed for review/clarify rounds (LangGraph recursion limit)
        "mcp_reconnects": 1,                  # times a dead MCP server is restarted before the call fails
        "checkpoint_db": "data/checkpoints.sqlite",   # LangGraph SQLite checkpointer: the paused run survives a restart
        "flag_reader_role": "flag_reader",    # roles (from tool metadata) of the review-time tools
        "decision_writer_role": "decision_writer",
        "clarification_writer_role": "clarification_writer",
        "memo_writer_role": "memo_writer",
        # Argument NAMES shared by the tools; the Planner's scope (buyer, years) is injected into any step whose schema has
        # them but whose arguments omit them. Agent code never names a tool, only these argument conventions.
        "scope_args": {"buyer": "buyer", "supplier": "supplier", "years": "years", "date_from": "date_from", "date_to": "date_to"},
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
