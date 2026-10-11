"""Tool discovery: everything the agent knows about its tools comes from the MCP server's `list_tools`.

No tool name appears in agent code. A tool is plannable when it is annotated read-only and has no role; the tool that
persists flags is found by its *role* ("flag_writer", carried in the tool's metadata), not by its name. A tool added
to the server is therefore usable by the planner without any agent change.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolInfo:
    """What a client learns about one tool from list_tools."""
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    role: str | None = None
    server: str = "zabuni"
    output_keys: tuple[str, ...] = field(default=())


def to_tool_info(tool: Any, server: str) -> ToolInfo:
    """Convert an `mcp.types.Tool` (or a dict with the same fields) into a ToolInfo."""
    get = (lambda k, d=None: tool.get(k, d)) if isinstance(tool, dict) else (lambda k, d=None: getattr(tool, k, d))
    annotations = get("annotations") or {}
    read_only = annotations.get("readOnlyHint") if isinstance(annotations, dict) else getattr(annotations, "readOnlyHint", None)
    meta = get("meta") or get("_meta") or {}
    role = ((meta.get("zabuni") or {}).get("role")) if isinstance(meta, dict) else None
    return ToolInfo(name=get("name"), description=get("description") or "", input_schema=get("inputSchema") or {},
                    read_only=bool(read_only), role=role, server=server)


def planning_tools(tools: list[ToolInfo]) -> list[ToolInfo]:
    """Tools the Planner may schedule: read-only and without a special role (so never the flag writer)."""
    return [t for t in tools if t.read_only and not t.role]


def tool_with_role(tools: list[ToolInfo], role: str) -> ToolInfo | None:
    """The tool that declares `role` (e.g. the flag writer), or None if the server offers none."""
    return next((t for t in tools if t.role == role), None)


BRIEF_MIN_CUT = 40          # do not cut a description shorter than this when looking for a sentence boundary


def brief(description: str, max_chars: int) -> str:
    """The first paragraph of a tool description, cut at a sentence boundary within `max_chars`.

    The Planner needs to know what a tool is FOR, not its every threshold; the full text stays available through list_tools."""
    para = " ".join(description.strip().split("\n\n")[0].split())
    if len(para) <= max_chars:
        return para
    cut = para[:max_chars]
    boundary = max(cut.rfind(". "), cut.rfind("; "), cut.rfind(": "))
    return (cut[:boundary + 1] if boundary > BRIEF_MIN_CUT else cut.rstrip()) + ("" if boundary > BRIEF_MIN_CUT else "...")


def type_label(spec: dict[str, Any]) -> str:
    """A short type for one JSON-schema property: str, int, int[], a|b|c for enums, ..."""
    if "enum" in spec:
        return "|".join(str(v) for v in spec["enum"])
    options = [o for o in spec.get("anyOf", []) if o.get("type") != "null"] or [spec]
    t = options[0]
    kind = t.get("type", "any")
    if kind == "array":
        return type_label(t.get("items", {})) + "[]"
    return {"string": "str", "integer": "int", "number": "num", "boolean": "bool"}.get(kind, kind)


def compact_args(schema: dict[str, Any]) -> str:
    """One short line per argument: `name: type (description)`. Required arguments are marked with *."""
    required = set(schema.get("required", []))
    lines = []
    for name, spec in (schema.get("properties") or {}).items():
        note = spec.get("description")
        lines.append(f"{name}{'*' if name in required else ''}: {type_label(spec)}" + (f" ({note})" if note else ""))
    return "; ".join(lines)


def compact_schema(schema: dict[str, Any]) -> str:
    """The argument list in the same short form (used in correction prompts)."""
    return compact_args(schema)


def catalogue(tools: list[ToolInfo], brief_chars: int = 220) -> str:
    """The tool catalogue shown to the Planner: name, a brief description, and a compact argument list."""
    return "\n".join(f"- {t.name}: {brief(t.description, brief_chars)}\n  args: {compact_args(t.input_schema)}" for t in tools)
