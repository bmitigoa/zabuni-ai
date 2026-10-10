"""Start zabuni-mcp as a real stdio subprocess and talk MCP to it (what the agent will do on Day 3)."""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server.common import ROOT

from mcp_server.registry import TOOLS, describe, discover_tools

discover_tools()
EXPECTED_TOOLS = set(TOOLS)          # derived from the registry: adding a tool never edits this test


async def _session_checks():
    params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = (await s.list_tools()).tools
            assert {t.name for t in tools} == EXPECTED_TOOLS
            assert all(t.description and len(t.description) > 200 for t in tools)  # the agent reads these
            for t in tools:                                                   # annotations and roles mirror the registry
                spec = TOOLS[t.name]
                assert t.annotations.readOnlyHint is spec.read_only
                assert t.description == describe(t.name)                      # rendered from rules, not hand-written
                role = ((t.meta or {}).get("zabuni") or {}).get("role")
                assert role == spec.role

            res = await s.call_tool("search_awards", {"buyer": "Makueni County Government", "limit": 2})
            assert not res.isError
            payload = res.structuredContent
            if "evidence" not in payload:  # some SDK versions wrap non-object returns in {"result": ...}
                payload = payload["result"]
            assert len(payload["evidence"]) == 2 and payload["params_used"]["limit"] == 2
            ocid = payload["evidence"][0]["ocid"]

            bad = await s.call_tool("detect_splitting", {"buyer": "Zzzz Not A Buyer"})
            assert not bad.isError and "error" in json.dumps(bad.structuredContent)

            templates = {t.uriTemplate for t in (await s.list_resource_templates()).resourceTemplates}
            resources = {str(r.uri) for r in (await s.list_resources()).resources}
            assert "ocds://release/{ocid}" in templates and "rules://ppada" in resources

            rec = json.loads((await s.read_resource(f"ocds://release/{ocid}")).contents[0].text)
            assert rec["ocid"] == ocid and rec["awards"]
            rules = json.loads((await s.read_resource("rules://ppada")).contents[0].text)
            assert "provisional" in rules["status"]


def test_server_starts_lists_tools_and_serves_resources():
    asyncio.run(asyncio.wait_for(_session_checks(), timeout=120))
