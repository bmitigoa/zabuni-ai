"""Section 9 acceptance tests: auto-registration, docstrings rendered from rules, no magic numbers, norm() fix.

These tests are written so that adding a tool or changing a threshold never requires editing them.
"""
import ast
import json
import re
from pathlib import Path

import pytest

from data.loader import norm
from mcp_server import tools
from mcp_server.registry import PLACEHOLDER, TOOLS, describe, discover_tools, render_doc
from mcp_server.rules import DEFAULT_RULES, load_rules

ROOT = Path(__file__).resolve().parent.parent
TOOL_DIR = ROOT / "mcp_server" / "tools"


# ----------------------------------------------------------------------------- auto-registration
def test_every_module_in_the_tools_package_registers_a_tool():
    modules = {p.stem for p in TOOL_DIR.glob("*.py") if p.stem != "__init__"}
    registered = {spec.fn.__module__.rsplit(".", 1)[-1] for spec in discover_tools().values()}
    assert modules and modules == registered, "a module in mcp_server/tools/ registers no tool (or vice versa)"


def test_registered_tools_are_importable_from_the_package_and_wrapped_safely():
    for name, spec in TOOLS.items():
        assert getattr(tools, name) is spec.fn
        assert spec.doc_template.strip(), f"{name} has no docstring: the LLM reads it to choose tools"
    out = tools.search_awards()            # no filter -> must come back as an error message, never raise
    assert "error" in out["result"]


def test_exactly_the_non_read_only_tools_declare_a_role():
    writers = [s for s in TOOLS.values() if not s.read_only]
    assert writers, "the server must have at least one non-read-only tool (hard rule 4)"
    assert all(s.role for s in writers) and not any(s.role for s in TOOLS.values() if s.read_only)


# ----------------------------------------------------------------------------- docstrings come from rules
def test_all_placeholders_resolve_and_none_are_left_in_descriptions():
    rules = load_rules()
    for name, spec in TOOLS.items():
        text = render_doc(spec.doc_template, rules)
        assert not PLACEHOLDER.search(text) and "[[" not in text, f"{name}: unresolved placeholder"


def test_unknown_placeholder_fails_loudly():
    with pytest.raises(KeyError):
        render_doc("[[no_such_section.value]]", load_rules())


def test_changing_a_rule_changes_the_description(monkeypatch, tmp_path):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"price_benchmark": {"min_comparables": 7, "flag_ratio_to_median": 4.5},
                             "limits": {"search_max": 33}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(f))
    bench = " ".join(describe("compute_price_benchmark").split())      # docstrings wrap lines: compare on words
    assert "fewer than 7 comparables" in bench and "4.5x median" in bench
    assert "1-33" in describe("search_awards")


def test_descriptions_hard_code_no_rule_values():
    """After stripping placeholders, only structural numbers may remain (range starts, HHI scale, worked examples)."""
    allowed = {"1-", "1-.", "0-10,000", "[2023,", "2024].", "3.2x", "47", "0"}
    for name, spec in TOOLS.items():
        stripped = PLACEHOLDER.sub("", spec.doc_template)
        leftovers = {m.group(0) for m in re.finditer(r"[\w.\-\[\]]*\d[\w.,\-\]x%]*", stripped)}
        assert leftovers <= allowed, f"{name}: hard-coded numbers in the docstring: {sorted(leftovers - allowed)}"


# ----------------------------------------------------------------------------- no magic numbers in tool code
ALLOWED_NUMBERS = {0, 1, 2, 100}      # truth values, pairs, percent conversion
SCANNED = [*sorted(TOOL_DIR.glob("*.py")), *(ROOT / "mcp_server" / n for n in
                                             ("common.py", "toolkit.py", "registry.py", "records.py"))]


def _numeric_offenders(path: Path) -> list[tuple[int, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip: set[int] = set()

    def skip_all(node: ast.AST) -> None:
        skip.update(id(n) for n in ast.walk(node))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):          # API defaults, e.g. limit: int = 20
            for d in [*node.args.defaults, *node.args.kw_defaults]:
                if d is not None:
                    skip_all(d)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "round":
            for a in node.args[1:]:                                              # presentation rounding digits
                skip_all(a)
    for node in tree.body:                                                       # named module constants (ALL_CAPS)
        if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets):
            skip_all(node)
    bad = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
                and id(node) not in skip and node.value not in ALLOWED_NUMBERS):
            bad.append((node.lineno, node.value))
    return bad


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_no_magic_numbers_in_tool_code(path):
    assert _numeric_offenders(path) == [], f"{path.name}: move these into mcp_server/rules.py (or a named CONSTANT)"


# ----------------------------------------------------------------------------- rules loading
def test_rules_file_override_merges_and_a_wrong_path_is_an_error(monkeypatch, tmp_path):
    f = tmp_path / "rules.json"
    f.write_text(json.dumps({"splitting": {"window_days": 14}}), encoding="utf-8")
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(f))
    merged = load_rules()
    assert merged["splitting"]["window_days"] == 14 and merged["splitting"]["min_awards"] == DEFAULT_RULES["splitting"]["min_awards"]
    monkeypatch.setenv("ZABUNI_RULES_FILE", str(tmp_path / "typo.json"))
    with pytest.raises(FileNotFoundError):
        load_rules()
    out = tools.detect_splitting(buyer="Makueni County Government")    # surfaced to the agent as a message, not a crash
    assert "does not exist" in json.dumps(out["result"])


def test_year_window_has_a_single_source_of_truth():
    """The loader must not carry its own copy of the window (it reads rules.py)."""
    loader = (ROOT / "data" / "loader.py").read_text(encoding="utf-8")
    assert "MIN_YEAR" not in loader and "MAX_YEAR" not in loader and "load_rules" in loader


# ----------------------------------------------------------------------------- norm()
@pytest.mark.parametrize("raw, expected", [
    ("Co-operative Bank", "COOPERATIVE BANK"),
    ("CO-OPERATIVE BANK OF KENYA", "COOPERATIVE BANK OF KENYA"),
    ("NEW KENYA  CO- OPERATIVE CREAMERIES LTD", "NEW KENYA COOPERATIVE CREAMERIES"),
    ("NGOs Co-ordination Board", "NGOS COORDINATION BOARD"),
    ("Smith & Co.", "SMITH"),                       # a standalone Co is still removed
    ("Doshi & Co Hardware Limited", "DOSHI HARDWARE"),
    ("Rimax international co-ltd", "RIMAX INTERNATIONAL"),
    ("COMMERCE HOUSE", "COMMERCE HOUSE"),           # CO inside a word is untouched
    ("The Co. Ltd", None),                          # only company-form words left
    ("", None), (None, None),
])
def test_norm_keeps_cooperative_as_one_word(raw, expected):
    assert norm(raw) == expected
