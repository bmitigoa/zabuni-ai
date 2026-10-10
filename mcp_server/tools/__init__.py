"""zabuni-mcp tools: one module per tool. Importing this package registers every tool (see registry.py).

To add a tool: create a module here with a function decorated by `@tool(...)`, add its section to rules.py, and add
its row to docs/red_flags.md. Nothing else (server, tests, README) needs editing.
"""
from mcp_server.records import release_record  # noqa: F401  (re-exported for the resource and tests)
from mcp_server.registry import TOOLS, discover_tools

discover_tools()
globals().update({name: spec.fn for name, spec in TOOLS.items()})   # tools.search_awards(...) etc. stay importable
