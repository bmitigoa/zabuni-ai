"""Zabuni AI - rule thresholds (single source of truth for the tools and `rules://ppada`).

Every value is a screening default, NOT a legal limit. All are PROVISIONAL and must be
validated with a procurement practitioner (see docs/user_engagement.md). Override any value
by pointing ZABUNI_RULES_FILE at a JSON file with the same structure (partial files are
merged over these defaults), or per call through the tool arguments that expose them.

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
        "min_year": 2018,
        "max_year": 2026,
    },
    "price_benchmark": {
        "min_comparables": 10,                # fewer -> insufficient_comparables flag (agent widens search)
        "flag_ratio_to_median": 3.0,          # award >= 3x cohort median is flagged for review
        "max_comparables_cited": 10,
    },
    "splitting": {
        "window_days": 30,
        "min_awards": 3,
        "near_below_ratio": 0.5,              # an award is 'just below' T if near_below_ratio*T <= amount < T
        "thresholds_kes": [500_000, 1_000_000, 2_000_000, 5_000_000, 10_000_000],  # illustrative, unverified
    },
    "noncompetitive_method": {
        "direct_methods": ["direct"],         # OCDS procurementMethod values
        "restricted_methods": ["selective"],  # OCDS 'selective' = restricted tendering
        "flag_ratio_to_baseline": 2.0,        # buyer direct+restricted share >= 2x national baseline
        "min_noncompetitive_awards": 10,  # direct + restricted awards needed before a buyer is flagged
        "min_awards_for_ranking": 20,         # used when ranking buyers (no buyer given)
    },
    "supplier_concentration": {
        "min_awards": 30,                     # buyer needs >= this many supplier-named awards
        "flag_share": 0.25,                   # top supplier >= 25 % by count or by value
    },
}


def load_rules() -> dict[str, Any]:
    """Return the defaults merged with the optional JSON override file (ZABUNI_RULES_FILE)."""
    rules = copy.deepcopy(DEFAULT_RULES)
    path = os.environ.get("ZABUNI_RULES_FILE")
    if path and Path(path).is_file():
        _merge(rules, json.loads(Path(path).read_text(encoding="utf-8")))
    return rules


def _merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
