"""Tool registry: how a tool becomes visible to MCP clients with no edit outside its own module.

A tool module defines a function and decorates it with `@tool(...)`. Importing the module registers it; `server.py`
loops over `TOOLS`; tests derive their expectations from `TOOLS`. Adding a tool therefore needs three edits only:
(1) a new file in mcp_server/tools/, (2) its section in rules.py, (3) its row in docs/red_flags.md (a test enforces it).

Docstrings are templates: `[[price_benchmark.min_comparables]]` is replaced by the live rule value when the tool is
registered with FastMCP, so a description can never disagree with the rules the code actually applies.
"""
from __future__ import annotations

import functools
import importlib
import pkgutil
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from mcp_server.common import ToolInputError, error
from mcp_server.rules import load_rules

PLACEHOLDER = re.compile(r"\[\[([a-z_.0-9]+)(?:\|([a-z]+))?\]\]")


@dataclass
class ToolSpec:
    """One registered tool."""
    name: str
    fn: Callable[..., dict[str, Any]]
    read_only: bool = True
    role: str | None = None                    # machine-readable role for clients, e.g. "flag_writer"
    doc_template: str = ""
    annotations: dict[str, bool] = field(default_factory=dict)


TOOLS: dict[str, ToolSpec] = {}


def safe(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
    """Turn input errors and unexpected failures into an error envelope instead of a crash."""
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except ToolInputError as exc:
            return error(str(exc), kwargs)
        except Exception as exc:  # noqa: BLE001 - last line of defence for the agent loop
            print(f"[zabuni-mcp] {fn.__name__} failed: {exc!r}", file=sys.stderr)
            return error(f"Internal error in {fn.__name__}: {type(exc).__name__}: {exc}", kwargs)
    return wrapper


def tool(*, read_only: bool = True, role: str | None = None) -> Callable[[Callable[..., dict[str, Any]]], Callable[..., dict[str, Any]]]:
    """Decorator: wrap with `safe`, record in TOOLS. `role` lets a client find a tool without knowing its name."""
    def decorate(fn: Callable[..., dict[str, Any]]) -> Callable[..., dict[str, Any]]:
        wrapped = safe(fn)
        TOOLS[fn.__name__] = ToolSpec(
            name=fn.__name__, fn=wrapped, read_only=read_only, role=role, doc_template=fn.__doc__ or "",
            annotations={"readOnlyHint": read_only, "destructiveHint": False,
                         "idempotentHint": True, "openWorldHint": False})
        return wrapped
    return decorate


def _lookup(rules: dict[str, Any], path: str) -> Any:
    value: Any = rules
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(f"docstring placeholder [[{path}]] does not exist in rules")
        value = value[part]
    return value


def render_doc(template: str, rules: dict[str, Any]) -> str:
    """Replace `[[a.b.c]]` (and `[[a.b.c|pct]]`, `[[a.b.c|kes]]`, `[[a.b.c|list]]`) with the live rule value."""
    def sub(m: re.Match[str]) -> str:
        value = _lookup(rules, m.group(1))
        fmt = m.group(2)
        if fmt == "pct":
            return f"{value * 100:g}%"
        if fmt == "kes":
            return f"KES {value:,.0f}"
        if fmt == "list":
            return ", ".join(str(v) for v in value)
        return f"{value:g}" if isinstance(value, float) else str(value)
    return PLACEHOLDER.sub(sub, template)


def describe(name: str, rules: dict[str, Any] | None = None) -> str:
    """The description a client sees for a tool (docstring rendered from the live rules)."""
    return render_doc(TOOLS[name].doc_template, rules or load_rules())


def discover_tools() -> dict[str, ToolSpec]:
    """Import every module in mcp_server.tools so each `@tool` registers itself."""
    pkg = importlib.import_module("mcp_server.tools")
    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"mcp_server.tools.{mod.name}")
    return TOOLS
