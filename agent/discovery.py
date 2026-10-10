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


def compact_schema(schema: dict[str, Any]) -> str:
    """Argument names, types, enums, defaults and required list in one line, for the prompt."""
    props = {}
    for name, spec in (schema.get("properties") or {}).items():
        entry = {k: spec[k] for k in ("type", "enum", "default", "items", "anyOf") if k in spec}
        props[name] = entry
    return json.dumps({"properties": props, "required": schema.get("required", [])}, ensure_ascii=False)


def catalogue(tools: list[ToolInfo]) -> str:
    """The tool catalogue shown to the Planner: name, full description, argument schema."""
    blocks = []
    for t in tools:
        desc = " ".join(t.description.split())
        blocks.append(f"### {t.name}\n{desc}\nArguments (JSON schema): {compact_schema(t.input_schema)}")
    return "\n\n".join(blocks)
