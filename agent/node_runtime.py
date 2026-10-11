"""Find a Node.js new enough to run the official Filesystem MCP server (via npx).

The pinned server (`@modelcontextprotocol/server-filesystem`) depends on `minimatch` 10, which declares Node 20 or newer, while
the operating-system package `nodejs` on a Debian-based host (Streamlit Community Cloud's `packages.txt`) may be 18. So the agent
looks for the first usable Node in this order: the system `node`/`npx` if its major version is at least MIN_NODE_MAJOR, then the
copy shipped by the `nodejs-wheel-binaries` Python package (a pinned, self-contained Node installed by pip). If neither works the
caller gets a message that says what to install, instead of an unexplained failure when the server starts.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MIN_NODE_MAJOR = 20
VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
EXECUTABLE_SUFFIXES = ("", ".cmd", ".exe")


class NodeNotFound(RuntimeError):
    """No Node.js >= MIN_NODE_MAJOR was found; the message says how to get one."""


def node_major(node: str) -> int | None:
    """The major version printed by `node --version`, or None if it cannot be run."""
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = VERSION_RE.search(out or "")
    return int(m.group(1)) if m else None


def _which_in(name: str, directories: list[str]) -> str | None:
    for directory in directories:
        for suffix in EXECUTABLE_SUFFIXES:
            candidate = Path(directory) / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
    return None


def wheel_directories() -> list[str]:
    """Where the nodejs-wheel-binaries package puts `node` and `npx`: next to this Python, and inside the package."""
    dirs = [str(Path(sys.executable).resolve().parent)]
    try:
        import nodejs_wheel                                   # type: ignore[import-not-found]
        base = Path(nodejs_wheel.__file__).resolve().parent
        dirs += [str(base / "bin"), str(base)]
    except ImportError:
        pass
    return dirs


def find_npx(min_major: int = MIN_NODE_MAJOR) -> str:
    """The path of an `npx` whose Node is at least `min_major`. Raises NodeNotFound otherwise."""
    tried = []
    node, npx = shutil.which("node"), shutil.which("npx")
    if node and npx:
        major = node_major(node)
        tried.append(f"system node {major if major is not None else 'unknown'}")
        if major is not None and major >= min_major:
            return npx
    dirs = wheel_directories()
    node, npx = _which_in("node", dirs), _which_in("npx", dirs)
    if node and npx:
        major = node_major(node)
        tried.append(f"pip-installed node {major if major is not None else 'unknown'}")
        if major is not None and major >= min_major:
            # npx finds `node` on PATH: put the chosen Node first so it is the one that runs
            os.environ["PATH"] = str(Path(node).parent) + os.pathsep + os.environ.get("PATH", "")
            return npx
    raise NodeNotFound(f"The Filesystem MCP server needs Node.js {min_major} or newer ({'; '.join(tried) or 'none found'}). "
                       "Install it with the Python package `nodejs-wheel-binaries` (listed in requirements.txt) or a system Node >= "
                       f"{min_major} (on Streamlit Community Cloud: packages.txt).")
