"""zabuni-mcp: custom MCP server (FastMCP, stdio) for procurement red-flag screening.

Run:  python -m mcp_server.server        (stdio; an MCP client launches it)
Dev:  mcp dev mcp_server/server.py       (MCP Inspector)

Tools are read-only screening checks over the frozen PPRA Kenya OCDS snapshot. Every response is
{result, evidence, params_used, warnings}; evidence cites OCDS records. Nothing here recommends or awards a tender.
Write/generate tools (flag_finding, draft_eval_memo) are added on Day 4.
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

from mcp_server import tools  # noqa: E402
from mcp_server.common import ToolInputError  # noqa: E402
from mcp_server.rules import load_rules  # noqa: E402

mcp = FastMCP(
    "zabuni-mcp",
    instructions=(
        "Screening tools for public-procurement value-for-money and integrity risks over Kenya PPRA OCDS data. "
        "Start with search_awards to find ocid/award_id values. Every result carries evidence (OCDS ocid/award_id); "
        "cite it. Flags are prompts for human review, never findings; never recommend or award a tender. "
        "Read rules://ppada for thresholds (provisional)."
    ),
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
for _fn in (tools.search_awards, tools.compute_price_benchmark, tools.detect_splitting,
            tools.detect_noncompetitive_method, tools.supplier_concentration):
    mcp.tool(annotations=_READ_ONLY)(_fn)


@mcp.resource("ocds://release/{ocid}", mime_type="application/json")
def release(ocid: str) -> str:
    """The stored OCDS record (tender, awards, supplier names) for an ocid, for citing a flag's source."""
    try:
        return json.dumps(tools.release_record(ocid), default=str, indent=2)
    except ToolInputError as exc:
        return json.dumps({"error": str(exc)})


@mcp.resource("rules://ppada", mime_type="application/json")
def ppada_rules() -> str:
    """All screening thresholds and rules used by the tools. Provisional: to be validated with a practitioner."""
    return json.dumps(load_rules(), indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
