"""MCP client: one `MultiServerMCPClient` (langchain-mcp-adapters) over stdio to two servers.

(a) `zabuni` - our custom FastMCP server (analysis tools, the write tools and the two resources).
(b) `filesystem` - the official Filesystem MCP server (@modelcontextprotocol/server-filesystem, run with npx),
    sandboxed to the `outputs/` directory: it refuses every path outside it.

Why borrow the Filesystem server instead of writing files ourselves: path sandboxing and file I/O are solved, widely
used code maintained by the MCP project; our agent gets a *capability-limited* way to write a draft (one directory),
and every write crosses the MCP boundary where it is logged like any other tool call. It is a pinned version.

Resilience: each server's session lives in its own supervisor task (`_ServerRunner`), so a server that dies can be
restarted from any task. A call that fails because the connection died restarts that server once and retries (rules
`agent.mcp_reconnects`); the restart and any final failure are written to the audit log.

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
from pathlib import Path
from typing import Any, Callable

import anyio
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.shared.exceptions import McpError

from agent.audit import AuditLog
from agent.discovery import ToolInfo, to_tool_info
from agent.settings import FILESYSTEM_PACKAGE, ROOT, Settings

ZABUNI = "zabuni"
FILESYSTEM = "filesystem"
ENV_PREFIX = "ZABUNI_"
STOP_WAIT_S = 10.0          # how long a server's supervisor task may take to shut down
# Failures that mean "the connection to the server is gone" (as opposed to a tool reporting an error)
TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (anyio.ClosedResourceError, anyio.BrokenResourceError, anyio.EndOfStream,
                                                     ConnectionError, BrokenPipeError, asyncio.TimeoutError)


def is_transport_error(exc: BaseException) -> bool:
    """True when the failure means the connection to the server is gone (restarting may fix it), not that the tool or the
    request was wrong. A server process that exits mid-call surfaces as McpError('Connection closed')."""
    return isinstance(exc, TRANSPORT_ERRORS) or (isinstance(exc, McpError) and "connection closed" in str(exc).lower())


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


class _ServerRunner:
    """Owns one server's MCP session inside its own task. `stop()` then `start()` is a restart, callable from any task
    (anyio requires a session context to be exited by the task that entered it, which is why it is not shared)."""

    def __init__(self, client: MultiServerMCPClient, name: str) -> None:
        self.client, self.name = client, name
        self.session: Any = None
        self.error: BaseException | None = None
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self.session, self.error = None, None
        self._ready, self._stop = asyncio.Event(), asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mcp-{self.name}")
        await self._ready.wait()
        if self.error is not None:
            raise self.error

    async def _run(self) -> None:
        try:
            async with self.client.session(self.name) as session:
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except BaseException as exc:  # noqa: BLE001 - a dying server raises odd exception groups; report, never propagate
            self.error = exc
            self._ready.set()
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), STOP_WAIT_S)
            except BaseException:  # noqa: BLE001 - shutting down a dead server may raise; nothing more to do
                self._task.cancel()
        self.session = None


class McpBackend:
    """Async context manager that owns the MCP servers, restarts a dead one once, and logs every call."""

    def __init__(self, settings: Settings, audit: AuditLog | None = None, use_filesystem: bool = True,
                 connections: dict[str, dict[str, Any]] | None = None, reconnects: int = 1,
                 runner_factory: Callable[[Any, str], Any] = _ServerRunner) -> None:
        self.settings, self.audit, self.use_filesystem = settings, audit, use_filesystem
        self.tool_timeout_s: float | None = None
        self.reconnects = int(reconnects)
        self._connections = connections
        self._runner_factory = runner_factory
        self._runners: dict[str, Any] = {}
        self._tools: dict[str, list[ToolInfo]] = {}

    async def __aenter__(self) -> "McpBackend":
        conns = self._connections or {ZABUNI: zabuni_connection()}
        if self._connections is None and self.use_filesystem:
            Path(self.settings.outputs_dir).mkdir(parents=True, exist_ok=True)
            conns[FILESYSTEM] = filesystem_connection(self.settings.outputs_dir)
        client = MultiServerMCPClient(conns)
        for name in conns:
            runner = self._runner_factory(client, name)
            self._runners[name] = runner
            await runner.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        for runner in self._runners.values():
            await runner.stop()

    @property
    def servers(self) -> list[str]:
        return list(self._runners)

    def _session(self, server: str) -> Any:
        return self._runners[server].session

    async def restart(self, server: str) -> None:
        """Stop and start one server (used when its connection has died)."""
        runner = self._runners[server]
        await runner.stop()
        await runner.start()

    async def tools(self, server: str = ZABUNI) -> list[ToolInfo]:
        """list_tools for a server (cached for the run)."""
        if server not in self._tools:
            listed = await self._session(server).list_tools()
            self._tools[server] = [to_tool_info(t, server) for t in listed.tools]
        return self._tools[server]

    async def read_resource_json(self, uri: str, server: str = ZABUNI) -> Any:
        res = await self._session(server).read_resource(uri)
        return json.loads(res.contents[0].text)

    async def call(self, server: str, tool: str, args: dict[str, Any], step: dict[str, Any] | None = None) -> Any:
        """Call a tool, log it, return its parsed output. A dead connection restarts the server once and retries.
        Raises ToolCallFailed on transport/protocol errors that survive that."""
        started = time.time()
        output: Any = None
        error: str | None = None
        try:
            for attempt in range(self.reconnects + 1):
                try:
                    coro = self._session(server).call_tool(tool, args)
                    res = await (asyncio.wait_for(coro, self.tool_timeout_s) if self.tool_timeout_s else coro)
                    break
                except Exception as exc:  # noqa: BLE001 - classified below; anything else is re-raised unchanged
                    if not is_transport_error(exc):
                        raise
                    reason = f"{type(exc).__name__}: {exc}"
                    if attempt >= self.reconnects:
                        raise ToolCallFailed(f"MCP server '{server}' is unavailable after {attempt} reconnect attempt(s): {reason}") from exc
                    if self.audit is not None:
                        self.audit.event("mcp_reconnect", server=server, tool=tool, reason=reason, attempt=attempt + 1)
                    try:
                        await self.restart(server)
                    except Exception as restart_exc:  # noqa: BLE001
                        raise ToolCallFailed(f"MCP server '{server}' died and could not be restarted: "
                                             f"{type(restart_exc).__name__}: {restart_exc}") from restart_exc
            output = parse_tool_result(res)
            if res.isError:
                error = f"tool reported isError: {output if isinstance(output, str) else json.dumps(output)[:300]}"
                raise ToolCallFailed(error)
            return output
        except ToolCallFailed as exc:
            error = error or str(exc)
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
        if FILESYSTEM not in self._runners:
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
