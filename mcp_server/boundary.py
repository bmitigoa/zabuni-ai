"""Server-side write boundary: which tools may write, enforced where the database is opened (not in the agent).

The analysis database (the frozen PPRA snapshot) is opened read-only for everyone (common.cursor). The flags database is
the only thing the server writes, and `flagstore.connect()` asks this module for permission before it opens it writable.
The registry records which tool is running (`running`); the allowlist is rules `boundaries.write_tools`, so a new writing
tool is a one-line rules change plus a test, and a read tool that tries to write fails with an error instead of writing.

Code that runs outside any tool (the web app's own housekeeping, tests) has no current tool and is not restricted here.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Iterator

from mcp_server.rules import load_rules

_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("zabuni_current_tool", default=None)


class WriteNotAllowed(PermissionError):
    """A tool that is not on the write allowlist tried to open a database for writing."""


def write_tools() -> list[str]:
    return list(load_rules()["boundaries"]["write_tools"])


def current_tool() -> str | None:
    return _current.get()


@contextmanager
def running(tool_name: str) -> Iterator[None]:
    """Mark `tool_name` as the tool running in this task/thread for the duration of the block."""
    token = _current.set(tool_name)
    try:
        yield
    finally:
        _current.reset(token)


def require_writer(what: str) -> None:
    """Raise WriteNotAllowed when a tool outside the allowlist asks to write `what`."""
    tool = _current.get()
    if tool is not None and tool not in write_tools():
        raise WriteNotAllowed(f"tool '{tool}' is read-only and may not write {what}; only {write_tools()} may.")
