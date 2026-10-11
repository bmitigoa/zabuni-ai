"""First-start preparation for a hosted app: secrets from Streamlit or the environment, and the analysis database.

On Streamlit Community Cloud the repository contains no data (the OCDS snapshot is licensed and private). On first start the app
fetches the frozen snapshot from the private Hugging Face dataset with `deploy/fetch_snapshot.py` (SHA-256 verified), builds
`data/zabuni.duckdb` once with `data/loader.py` in a child process (so its memory is freed afterwards), and removes the raw file. The
database lives as long as the container; a restarted container repeats the preparation (about a minute).

Nothing here prints a secret: secrets are copied into the process environment (where `agent.settings` and `fetch_snapshot` read
them) and the MCP server only ever receives `ZABUNI_*` variables.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
SECRET_KEYS = ("GROQ_API_KEY", "HF_TOKEN", "LLM_PROVIDER", "MODEL", "DEMO_VIDEO_URL", "OLLAMA_BASE_URL")
LOADER_TIMEOUT_S = 900
STDERR_TAIL_CHARS = 600


class BootstrapError(RuntimeError):
    """The data could not be prepared; the message says what to check (and never contains a secret)."""


def secret(name: str, secrets: Mapping[str, Any] | None = None, environ: Mapping[str, str] | None = None) -> str | None:
    """A setting from Streamlit's secrets (if any) or else the environment; None when neither has it.

    Reading `st.secrets` raises when no secrets file exists (local runs), so any failure there means 'not set'."""
    environ = os.environ if environ is None else environ
    if secrets is not None:
        try:
            value = secrets[name]
        except Exception:  # noqa: BLE001 - missing key or missing secrets file
            value = None
        if value not in (None, ""):
            return str(value)
    value = environ.get(name)
    return value if value not in (None, "") else None


def export_secrets_to_env(secrets: Mapping[str, Any] | None = None, environ: dict[str, str] | None = None) -> list[str]:
    """Copy the known settings from Streamlit's secrets into the environment unless the environment already has them.

    Returns the NAMES that were set (never the values)."""
    environ = os.environ if environ is None else environ
    set_names = []
    for key in SECRET_KEYS:
        if environ.get(key):
            continue
        value = secret(key, secrets, environ={})
        if value:
            environ[key] = value
            set_names.append(key)
    return set_names


def database_path() -> Path:
    return Path(os.environ.get("ZABUNI_DB") or ROOT / "data" / "zabuni.duckdb")


def database_ready(path: Path | None = None) -> bool:
    path = path or database_path()
    return path.is_file() and path.stat().st_size > 0


def _load_fetch_module() -> Any:
    spec = importlib.util.spec_from_file_location("zabuni_fetch_snapshot", ROOT / "deploy" / "fetch_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_loader(src: Path, db: Path) -> None:
    """Build the DuckDB in a child process into a temporary file, then move it into place."""
    tmp = db.with_suffix(".building")
    tmp.unlink(missing_ok=True)
    result = subprocess.run([sys.executable, str(ROOT / "data" / "loader.py"), "--src", str(src), "--db", str(tmp)], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=LOADER_TIMEOUT_S)
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        tail = (result.stderr or result.stdout or "")[-STDERR_TAIL_CHARS:]
        raise BootstrapError(f"Building the analysis database failed (exit code {result.returncode}). Last output: {tail}")
    tmp.replace(db)


def ensure_database(progress: Callable[[str], None] | None = None, fetch: Callable[..., Path] | None = None,
                    build: Callable[[Path, Path], None] | None = None, db: Path | None = None) -> Path:
    """Make sure the analysis database exists; fetch and build it on first start. Returns its path.

    `fetch` and `build` are stand-ins for tests. A snapshot already on disk (local development) is used when its SHA-256 matches."""
    say = progress or (lambda message: None)
    db = db or database_path()
    if database_ready(db):
        return db
    fetch_mod = _load_fetch_module()
    raw = fetch_mod.DEST
    downloaded = False
    try:
        if raw.is_file() and fetch_mod.sha256_of(raw) == fetch_mod.expected_sha256():
            say("Using the snapshot already on disk.")
        else:
            say("Fetching the frozen snapshot from the private dataset (about 43 MB)...")
            (fetch or fetch_mod.fetch)()
            downloaded = True
    except fetch_mod.SnapshotError as exc:
        raise BootstrapError(f"The snapshot could not be fetched: {exc}") from None
    say("Building the analysis database from the snapshot (about a minute, first start only)...")
    db.parent.mkdir(parents=True, exist_ok=True)
    (build or _run_loader)(raw, db)
    if downloaded:                                # a file WE fetched is not needed once the database exists; the user's own copy is kept
        raw.unlink(missing_ok=True)
    if not database_ready(db):
        raise BootstrapError("The database file is missing after the build.")
    say("Data ready.")
    return db
