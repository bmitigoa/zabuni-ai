"""docs/red_flags.md repeats the thresholds in prose. This test keeps the prose and mcp_server/rules.py in step:
each row builds the phrase the document SHOULD contain from the live rule value. Change a rule without updating the
document (or the other way round) and the test fails, naming the rule.

When a rule gains or changes a number that red_flags.md quotes, add or adjust a row here.
"""
from pathlib import Path

import pytest

from mcp_server.rules import load_rules

DOC = (Path(__file__).resolve().parent.parent / "docs" / "red_flags.md").read_text(encoding="utf-8")


def millions(values):
    """[500000, 1000000, ...] -> 'KES 0.5M, 1M, ...' (the document's notation)."""
    return "KES " + ", ".join(f"{v / 1_000_000:g}M" for v in values).replace(", ", ", ", 1)


# (rules path, how the document words it)
ROWS = [
    ("price_benchmark.flag_ratio_to_median", lambda v: f"ratio ≥ **{v:g}×** median"),
    ("price_benchmark.min_comparables", lambda v: f"≥ **{v}** comparables"),
    ("splitting.min_awards", lambda v: f"≥ **{v}** contracts"),
    ("splitting.window_days", lambda v: f"within **{v}** days"),
    ("splitting.near_below_ratio", lambda v: f"({v:g}·T ≤ amount < T)"),
    ("splitting.thresholds_kes", lambda v: f"T ∈ {{{millions(v)}}}"),
    ("noncompetitive_method.flag_ratio_to_baseline", lambda v: f"share ≥ **{v:g}×** baseline"),
    ("noncompetitive_method.min_noncompetitive_awards", lambda v: f"≥ **{v}** such awards"),
    ("noncompetitive_method.min_awards_for_ranking", lambda v: f"ranking mode needs ≥ {v} awards"),
    ("supplier_concentration.flag_share", lambda v: f"≥ **{v * 100:g} %** by count or value"),
    ("supplier_concentration.min_awards", lambda v: f"≥ **{v}** supplier-named awards"),
    ("flags.min_explanation_chars", lambda v: f"explanation ≥ {v} characters"),
    ("flags.severities", lambda v: f"severity in {{{', '.join(v)}}}"),
    ("limits.search_max", lambda v: f"`limit` 1–{v}"),
    ("review.min_reason_chars", lambda v: f"a reason of at least {v} characters is required"),
    ("review.max_clarify_rounds", lambda v: f"at most {v} clarification rounds per flag"),
    ("review.decisions", lambda v: f"decision in {{{', '.join(v)}}}"),
    ("review.roles", lambda v: f"({', '.join(r for r in v if r != 'demo_reviewer')}, demo_reviewer)"),
    ("data_cleaning.max_valid_amount_kes", lambda v: f"above KES {v / 1e9:g} billion"),
]


def lookup(rules, path):
    for part in path.split("."):
        rules = rules[part]
    return rules


@pytest.mark.parametrize("path, phrase", ROWS, ids=[r[0] for r in ROWS])
def test_the_document_states_the_current_rule_value(path, phrase):
    value = lookup(load_rules(), path)
    expected = phrase(value)
    assert expected in DOC, f"docs/red_flags.md no longer matches rules.py: {path} = {value!r}; expected the text {expected!r}"


def test_the_year_window_in_the_document_matches_the_rules():
    dc = load_rules()["data_cleaning"]
    assert f"outside {dc['min_year']}–{dc['max_year']}" in DOC


def test_a_changed_rule_would_be_caught(monkeypatch, tmp_path):
    """Self-check: with a rules override the phrase changes, so the document phrase would no longer be found."""
    import json
    override = tmp_path / "rules.json"
    override.write_text(json.dumps({"splitting": {"window_days": 45}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(override))
    phrase = dict(ROWS)["splitting.window_days"](lookup(load_rules(), "splitting.window_days"))
    assert phrase == "within **45** days" and phrase not in DOC
