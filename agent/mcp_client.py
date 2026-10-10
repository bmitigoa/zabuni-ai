"""MCP client: one `MultiServerMCPClient` (langchain-mcp-adapters) over stdio to two servers.

(a) `zabuni` - our custom FastMCP server (analysis tools + flag_finding + the two resources).
(b) `filesystem` - the official Filesystem MCP server (@modelcontextprotocol/server-filesystem, run with npx),
    sandboxed to the `outputs/` directory: it refuses every path outside it.

Why borrow the Filesystem server instead of writing files ourselves: path sandboxing and file I/O are solved, widely
used code maintained by the MCP project; our agent gets a *capability-limited* way to write a draft (one directory),
and every write crosses the MCP boundary where it is logged like any other tool call. It is a pinned version.

Environment: an MCP stdio client does NOT forward the parent's environment to the server subprocess. We forward only
`ZABUNI_*` settings (database path, rules override, flags database) and nothing else, so the Groq key never reaches
the server. Every call is written to the audit log.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.audit import AuditLog
from agent.discovery import ToolInfo, to_tool_info
from agent.settings import FILESYSTEM_PACKAGE, ROOT, Settings

ZABUNI = "zabuni"
FILESYSTEM = "filesystem"
ENV_PREFIX = "ZABUNI_"


class ToolCallFailed(RuntimeError):
    """A tool call failed at the transport/protocol level (not a tool-reported `result.error`)."""


def zabuni_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The settings forwarded to the zabuni-mcp subprocess: ZABUNI_* variables only."""
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items() if k.startswith(ENV_PREFIX)}


def zabuni_connection(environ: dict[str, str] | None = None) -> dict[str, Any]:
    return {"transport": "stdio", "command": sys.executable, "args": ["-m", "mcp_server.server"],
            "cwd": str(ROOT), "env": zabuni_env(environ)}


def filesystem_connection(outputs_dir: Path) -> dict[str, Any]:
    """npx runs the pinned official server; its single argument is the only directory it may access."""
    return {"transport": "stdio", "command": shutil.which("npx") or "npx",
            "args": ["-y", FILESYSTEM_PACKAGE, str(Path(outputs_dir).resolve())], "env": None}


def parse_tool_result(res: Any) -> Any:
    """Tool output as Python data: structuredContent if present, else JSON in the text content, else the text."""
    if getattr(res, "structuredContent", None) is not None:
        return res.structuredContent
    text = "\n".join(getattr(c, "text", "") for c in (res.content or []) if getattr(c, "type", "") == "text")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


class McpBackend:
    """Async context manager that owns both MCP sessions and logs every call."""

    def __init__(self, settings: Settings, audit: AuditLog | None = None, use_filesystem: bool = True,
                 connections: dict[str, dict[str, Any]] | None = None) -> None:
        self.settings, self.audit, self.use_filesystem = settings, audit, use_filesystem
        self.tool_timeout_s: float | None = None
        self._connections = connections
        self._stack = AsyncExitStack()
        self._sessions: dict[str, Any] = {}
        self._tools: dict[str, list[ToolInfo]] = {}

    async def __aenter__(self) -> "McpBackend":
        conns = self._connections or {ZABUNI: zabuni_connection()}
        if self._connections is None and self.use_filesystem:
            Path(self.settings.outputs_dir).mkdir(parents=True, exist_ok=True)
            conns[FILESYSTEM] = filesystem_connection(self.settings.outputs_dir)
        client = MultiServerMCPClient(conns)
        for name in conns:
            self._sessions[name] = await self._stack.enter_async_context(client.session(name))
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._stack.aclose()

    @property
    def servers(self) -> list[str]:
        return list(self._sessions)

    async def tools(self, server: str = ZABUNI) -> list[ToolInfo]:
        """list_tools for a server (cached for the run)."""
        if server not in self._tools:
            listed = await self._sessions[server].list_tools()
            self._tools[server] = [to_tool_info(t, server) for t in listed.tools]
        return self._tools[server]

    async def read_resource_json(self, uri: str, server: str = ZABUNI) -> Any:
        res = await self._sessions[server].read_resource(uri)
        return json.loads(res.contents[0].text)

    async def call(self, server: str, tool: str, args: dict[str, Any], step: dict[str, Any] | None = None) -> Any:
        """Call a tool, log it, return its parsed output. Raises ToolCallFailed on transport/protocol errors."""
        started = time.time()
        output: Any = None
        error: str | None = None
        try:
            coro = self._sessions[server].call_tool(tool, args)
            res = await (asyncio.wait_for(coro, self.tool_timeout_s) if self.tool_timeout_s else coro)
            output = parse_tool_result(res)
            if res.isError:
                error = f"tool reported isError: {output if isinstance(output, str) else json.dumps(output)[:300]}"
                raise ToolCallFailed(error)
            return output
        except ToolCallFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - normalise every failure for the caller and the log
            error = f"{type(exc).__name__}: {exc}"
            raise ToolCallFailed(error) from exc
        finally:
            if self.audit is not None:
                self.audit.call(server=server, tool=tool, inputs=args, output=output,
                                duration_s=time.time() - started, error=error, step=step)

    async def save_text(self, filename: str, text: str, hint: str = "write") -> str:
        """Write `text` to outputs/<filename> through the Filesystem MCP server's write tool; returns the path."""
        if FILESYSTEM not in self._sessions:
            raise ToolCallFailed("The Filesystem MCP server is not connected (use_filesystem=False).")
        candidates = [t for t in await self.tools(FILESYSTEM) if hint in t.name.lower()]
        if not candidates:
            raise ToolCallFailed(f"The Filesystem server offers no tool whose name contains {hint!r}.")
        tool = sorted(candidates, key=lambda t: (t.name != "write_file", t.name))[0]
        path = str((Path(self.settings.outputs_dir) / filename).resolve())
        props = tool.input_schema.get("properties", {})
        if not {"path", "content"} <= set(props):
            raise ToolCallFailed(f"Unexpected arguments for {tool.name}: {sorted(props)}")
        await self.call(FILESYSTEM, tool.name, {"path": path, "content": text})
        return path
