"""zabuni-mcp: custom MCP server (FastMCP, stdio) for procurement red-flag screening.

Run:  python -m mcp_server.server        (stdio; an MCP client launches it)
Dev:  mcp dev mcp_server/server.py       (MCP Inspector)

Tools are discovered, not listed here: every module in mcp_server/tools/ registers itself with the registry and this
file registers whatever the registry holds, with descriptions rendered from the live rules. Analysis tools are
read-only; `flag_finding` is the one write tool (it stores a pending-review flag in a separate flags database).
Every response is {result, evidence, params_used, warnings}; evidence cites OCDS records. Nothing here recommends or
awards a tender.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allow `mcp dev mcp_server/server.py` as well as `-m`
    sys.path.insert(0, str(ROOT))

from mcp.server.fastmcp import FastMCP  # noqa: E402
from mcp.types import ToolAnnotations  # noqa: E402

from mcp_server.common import ToolInputError  # noqa: E402
from mcp_server.records import release_record  # noqa: E402
from mcp_server.registry import TOOLS, discover_tools, render_doc  # noqa: E402
from mcp_server.rules import load_rules  # noqa: E402

mcp = FastMCP(
    "zabuni-mcp",
    instructions=(
        "Screening tools for public-procurement value-for-money and integrity risks over Kenya PPRA OCDS data. "
        "Start with search_awards to find ocid/award_id values. Every result carries evidence (OCDS ocid/award_id); "
        "cite it. Flags are prompts for human review, never findings; never recommend or award a tender. "
        "The tool with role 'flag_writer' stores a flag for human review and needs evidence copied from tool results. "
        "Read rules://ppada for thresholds (provisional)."
    ),
)


def register_tools() -> None:
    """Register every discovered tool with FastMCP; descriptions are rendered from the current rules."""
    rules = load_rules()
    for spec in discover_tools().values():
        mcp.tool(
            description=render_doc(spec.doc_template, rules),
            annotations=ToolAnnotations(**spec.annotations),
            meta={"zabuni": {"role": spec.role}} if spec.role else None,
        )(spec.fn)


register_tools()


@mcp.resource("ocds://release/{ocid}", mime_type="application/json")
def release(ocid: str) -> str:
    """The stored OCDS record (tender, awards, supplier names) for an ocid, for citing a flag's source."""
    try:
        return json.dumps(release_record(ocid), default=str, indent=2)
    except ToolInputError as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("rules://ppada", mime_type="application/json")
def ppada_rules() -> str:
    """All screening thresholds, limits and agent policy. Provisional: to be validated with a practitioner."""
    return json.dumps(load_rules(), indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
