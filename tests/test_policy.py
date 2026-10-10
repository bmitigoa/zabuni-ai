"""Pure decision logic (agent/policy.py): templates, follow-ups, widening, schema checks, citation guard."""
import pytest

from agent import policy
from mcp_server.rules import load_rules

POLICY = load_rules()["agent"]["followups"]
KNOWN = {"search_awards", "compute_price_benchmark", "detect_splitting", "supplier_concentration"}
SCOPE = {"buyer": "Makueni County Government", "years": [2022, 2023]}


# ----------------------------------------------------------------------------- templates
def test_resolve_path_and_templates():
    data = {"target": {"buyer": "X", "n": 0}, "awards": [{"ocid": "o1"}, {"ocid": "o2"}]}
    assert policy.resolve_path(data, "target.buyer") == "X" and policy.resolve_path(data, "awards.1.ocid") == "o2"
    assert policy.resolve_path(data, "awards.5.ocid") is None and policy.resolve_path(data, "target.missing") is None
    assert policy.resolve_template("$result.target.buyer", {"result": data}) == "X"
    assert policy.resolve_template("plain", {}) == "plain"


def test_render_args_drops_unresolved_and_empty_values():
    ctx = {"result": {"target": {"buyer": " Acme ", "supplier": None}}, "scope": {"years": []}}
    out = policy.render_args({"buyer": "$result.target.buyer", "supplier": "$result.target.supplier",
                              "years": "$scope.years", "limit": 3}, ctx)
    assert out == {"buyer": "Acme", "limit": 3}


# ----------------------------------------------------------------------------- follow-ups
def step(tool, **args):
    return {"tool": tool, "args": args}


def test_search_results_spawn_a_price_check_per_award_up_to_the_limit():
    out = {"result": {"awards": [{"ocid": f"o{i}", "award_id": f"a{i}"} for i in range(10)]}}
    added = policy.followups_for(step("search_awards"), out, SCOPE, POLICY, KNOWN)
    assert [a["args"] for a in added] == [{"ocid": f"o{i}", "award_id": f"a{i}"} for i in range(3)]
    assert all(a["tool"] == "compute_price_benchmark" and a["origin"] == "follow-up of search_awards" for a in added)


def test_flagged_price_outlier_spawns_splitting_and_concentration_for_that_buyer_supplier():
    out = {"result": {"flagged": True, "target": {"buyer": "KRA", "supplier": "VIVO"}}}
    added = policy.followups_for(step("compute_price_benchmark", ocid="o"), out, SCOPE, POLICY, KNOWN)
    assert [(a["tool"], a["args"]) for a in added] == [
        ("detect_splitting", {"buyer": "KRA", "supplier": "VIVO", "years": [2022, 2023]}),
        ("supplier_concentration", {"buyer": "KRA", "years": [2022, 2023]})]


def test_unflagged_errors_and_unknown_tools_spawn_nothing():
    ok = {"result": {"flagged": False, "target": {"buyer": "KRA"}}}
    assert policy.followups_for(step("compute_price_benchmark"), ok, SCOPE, POLICY, KNOWN) == []
    err = {"result": {"error": "boom", "flagged": True}}
    assert policy.followups_for(step("compute_price_benchmark"), err, SCOPE, POLICY, KNOWN) == []
    flagged = {"result": {"flagged": True, "target": {"buyer": "KRA", "supplier": "V"}}}
    only = policy.followups_for(step("compute_price_benchmark"), flagged, SCOPE, POLICY, {"detect_splitting"})
    assert [a["tool"] for a in only] == ["detect_splitting"]          # a rule for a tool not on the server is ignored
    assert policy.followups_for(step("compute_price_benchmark"), flagged, SCOPE, POLICY, set()) == []


def test_dedupe_steps_never_repeats_a_call():
    seen: set[str] = set()
    a, b = step("t", x=1), step("t", x=2)
    assert policy.dedupe_steps([a, a, b], seen) == [a, b] and policy.dedupe_steps([a], seen) == []


# ----------------------------------------------------------------------------- recovery and argument checks
def test_widening_applies_the_first_option_that_changes_the_arguments():
    opts = [{"op": "drop", "arg": "buyer_type"}, {"op": "drop", "arg": "years"}, {"op": "set", "arg": "auto_keywords", "value": False}]
    new, op = policy.next_widening({"buyer_type": "health", "years": [2019]}, opts)
    assert new == {"years": [2019]} and op["arg"] == "buyer_type"
    new, op = policy.next_widening(new, opts)
    assert new == {} and op["arg"] == "years"
    new, op = policy.next_widening({}, opts)                         # nothing left to drop: the set-op still changes args
    assert new == {"auto_keywords": False}
    assert policy.next_widening({"auto_keywords": False}, opts) is None


def test_needs_widening_requires_both_the_flag_and_machine_readable_options():
    assert policy.needs_widening({"result": {"insufficient_comparables": True, "widen_options": [{"op": "drop", "arg": "x"}]}})
    assert not policy.needs_widening({"result": {"insufficient_comparables": True, "widen_options": []}})
    assert not policy.needs_widening({"result": {"insufficient_comparables": False, "widen_options": [{}]}})
    assert not policy.needs_widening("not a dict")


SCHEMA = {"type": "object", "properties": {"limit": {"type": "integer"}, "buyer": {"type": "string"}}, "required": ["buyer"]}


def test_schema_problems():
    assert policy.schema_problems({"buyer": "X", "limit": 3}, SCHEMA) == []
    problems = policy.schema_problems({"limit": "three"}, SCHEMA)
    assert len(problems) == 2 and any("required" in p for p in problems)
    assert policy.schema_problems("nope", SCHEMA)[0].startswith("arguments must be a JSON object")
    assert policy.error_text({"result": {"error": "bad"}}) == "bad" and policy.error_text({"result": {}}) is None


# ----------------------------------------------------------------------------- candidates and the citation guard
def ev(ocid, field="award_amount", award="a1", value=1.0):
    return {"ocid": ocid, "award_id": award, "field": field, "value": value}


def flag(rule, ocids, hint="medium"):
    evidence = [ev(o) for o in ocids]
    return {"rule": rule, "summary": f"{rule} flag on {', '.join(ocids)}", "severity_hint": hint, "ocids": sorted(ocids), "evidence": evidence}


RESULTS = [
    {"tool": "detect_splitting", "output": {"result": {"flags": [flag("detect_splitting", ["ocds-1-a", "ocds-1-b"], "high")]},
                                            "evidence": [ev("ocds-1-a"), ev("ocds-1-b")]}},
    {"tool": "supplier_concentration", "output": {"result": {"flags": [flag("supplier_concentration", ["ocds-2-c"])]},
                                                  "evidence": [ev("ocds-2-c")]}},
    {"tool": "search_awards", "output": {"result": {}, "evidence": [ev("ocds-3-d")]}},
    {"tool": "detect_splitting", "output": None, "error": "failed"},
]
SEVERITIES, MIN_CHARS = ["low", "medium", "high"], 20


def test_candidates_and_pool_come_only_from_tool_outputs():
    cands = policy.extract_candidates(RESULTS)
    assert [(c["id"], c["tool"]) for c in cands] == [("c1", "detect_splitting"), ("c2", "supplier_concentration")]
    pool = policy.evidence_pool(RESULTS)
    assert {e["ocid"] for e in pool} == {"ocds-1-a", "ocds-1-b", "ocds-2-c", "ocds-3-d"}
    assert {e["source"] for e in pool if e["ocid"] == "ocds-3-d"} == {"search_awards"}


def test_ocids_in_text_trims_punctuation_but_keeps_balanced_parentheses():
    text = "see ocds-5whusi-1177969, and ocds-5whusi-SDPW/SB/018/2022-2024(1). Also (ocds-9-x)."
    assert policy.ocids_in(text) == {"ocds-5whusi-1177969", "ocds-5whusi-SDPW/SB/018/2022-2024(1)", "ocds-9-x"}


def run_guard(drafts):
    cands = policy.extract_candidates(RESULTS)
    return policy.guard_findings(drafts, cands, policy.evidence_pool(RESULTS), SEVERITIES, MIN_CHARS)


def draft(**kw):
    return {"candidate_id": None, "rule": "", "severity": "medium", "explanation": "x" * 30, "ocids": [], **kw}


def test_guard_accepts_a_candidate_finding_using_the_tool_evidence_not_the_models():
    acc, dropped = run_guard([draft(candidate_id="c1", severity="HIGH", ocids=["ocds-1-a"],
                                    explanation="Three awards to one supplier just below a threshold in two weeks.")])
    first = acc[0]
    assert not dropped and first["rule"] == "detect_splitting" and first["severity"] == "high"
    assert first["evidence"] == RESULTS[0]["output"]["result"]["flags"][0]["evidence"] and first["source"] == "candidate"
    assert any(f["candidate_id"] == "c2" and f["source"] == "candidate_default" for f in acc)   # unmentioned candidate kept


def test_guard_drops_a_finding_that_cites_an_ocid_no_tool_returned_and_keeps_the_tool_flag():
    acc, dropped = run_guard([draft(candidate_id="c1", ocids=["ocds-9-FAKE"], explanation="Real flag but cites a made-up ocid.")])
    assert dropped[0]["reason"] == "ocid not in tool-returned evidence" and dropped[0]["ocids"] == ["ocds-9-FAKE"]
    assert "ocds-9-FAKE" not in str(acc) and {f["candidate_id"] for f in acc} == {"c1", "c2"}      # c1 survives as the tool's default


def test_guard_catches_a_fabricated_ocid_hidden_in_the_explanation_text():
    acc, dropped = run_guard([draft(candidate_id="c2", ocids=["ocds-2-c"],
                                    explanation="The supplier also won ocds-8-INVENTED at the same buyer last year.")])
    assert dropped and dropped[0]["ocids"] == ["ocds-8-INVENTED"]
    assert all("ocds-8-INVENTED" not in f["explanation"] for f in acc)


def test_guard_extra_finding_needs_tool_evidence_for_the_cited_ocids_and_rule():
    good = draft(rule="search_awards", ocids=["ocds-3-d"], explanation="A large award found by the search worth a look.")
    bad_rule = draft(rule="detect_splitting", ocids=["ocds-3-d"], explanation="Wrong rule for this evidence entirely.")
    none = draft(rule="search_awards", ocids=[], explanation="No ocids cited at all here, nothing to check.")
    acc, dropped = run_guard([good, bad_rule, none])
    assert [f["source"] for f in acc if f["source"] == "model_extra"] == ["model_extra"]
    assert len(dropped) == 2 and all("no tool-returned evidence" in d["reason"] for d in dropped)


def test_guard_ignores_unknown_candidate_ids_duplicates_and_bad_severity():
    acc, dropped = run_guard([draft(candidate_id="c1", severity="catastrophic", explanation="short"),
                              draft(candidate_id="c1", explanation="A second finding for the same candidate id.")])
    first = next(f for f in acc if f["candidate_id"] == "c1" and f["source"] == "candidate")
    assert first["severity"] == "high"                                   # fell back to the tool's severity_hint
    assert first["explanation"] == RESULTS[0]["output"]["result"]["flags"][0]["summary"]   # too short -> tool's own text
    assert len(dropped) == 1 and "duplicate" in dropped[0]["reason"]


# ----------------------------------------------------------------------------- scope injection
NAMES = load_rules()["agent"]["scope_args"]
BUYER_YEARS = {"properties": {"buyer": {}, "years": {}, "limit": {}}}
BUYER_DATES = {"properties": {"buyer": {}, "date_from": {}, "date_to": {}}}


def test_scope_is_injected_where_the_tool_accepts_it_and_the_plan_left_it_out():
    out, injected = policy.inject_scope({"limit": 5}, BUYER_YEARS, SCOPE, NAMES)
    assert out == {"limit": 5, "buyer": "Makueni County Government", "years": [2022, 2023]} and injected == ["buyer", "years"]
    out, injected = policy.inject_scope({}, BUYER_DATES, SCOPE, NAMES)           # a tool with a date range instead of years
    assert out == {"buyer": "Makueni County Government", "date_from": "2022-01-01", "date_to": "2023-12-31"}


def test_injection_never_overwrites_and_never_adds_what_the_tool_does_not_accept():
    out, injected = policy.inject_scope({"buyer": "Other Buyer"}, BUYER_YEARS, SCOPE, NAMES)
    assert out["buyer"] == "Other Buyer" and injected == ["years"]
    out, injected = policy.inject_scope({}, {"properties": {"limit": {}}}, SCOPE, NAMES)
    assert out == {} and injected == []
    out, injected = policy.inject_scope({}, BUYER_YEARS, {"buyer": None, "years": None}, NAMES)   # request had no scope
    assert out == {} and injected == []

