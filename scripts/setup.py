"""One-command setup from a clean clone:   python scripts/setup.py [--snapshot PATH] [--skip-tests]

Standard library only, so any Python 3.11+ can run it. It
  1. creates the virtual environment .venv312 and installs requirements.txt;
  2. puts the FROZEN OCDS snapshot at data/raw/full.jsonl.gz (from --snapshot PATH, an existing file, or the private dataset when
     HF_TOKEN is set) and checks its SHA-256 against data/snapshot.sha256;
  3. builds data/zabuni.duckdb with data/loader.py;
  4. creates .env from .env.example (local model: Ollama; put GROQ_API_KEY in it to use Groq);
  5. checks Node.js (>= 20, for the Filesystem MCP server) and Ollama, and runs the tests (skip with --skip-tests).
The snapshot is licensed open data but is not redistributed in this repository: see README "Data source" for where it comes from.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv312"
RAW = ROOT / "data" / "raw" / "full.jsonl.gz"
DB = ROOT / "data" / "zabuni.duckdb"
CHECKSUM = ROOT / "data" / "snapshot.sha256"
MIN_PYTHON = (3, 11)
REGISTRY = "https://data.open-contracting.org/en/publication/147"
OLLAMA_TAGS = "http://localhost:11434/api/tags"
STEP_LINE = "=" * 78


def say(message: str) -> None:
    print(f"\n{STEP_LINE}\n{message}\n{STEP_LINE}", flush=True)


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(cmd: list[str], **kwargs) -> None:
    print("$ " + " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def recorded_checksum() -> str:
    return CHECKSUM.read_text(encoding="utf-8").split()[0].lower()


def step_environment() -> None:
    say("1/5  Python environment")
    if sys.version_info < MIN_PYTHON:
        sys.exit(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (3.12 recommended); this is {sys.version.split()[0]}.")
    if not venv_python().is_file():
        run([sys.executable, "-m", "venv", str(VENV)])
    run([str(venv_python()), "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([str(venv_python()), "-m", "pip", "install", "-r", "requirements.txt"])


def step_snapshot(snapshot: str | None, allow_different: bool) -> None:
    say("2/5  The frozen OCDS snapshot")
    if snapshot:
        RAW.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(snapshot, RAW)
        print(f"copied {snapshot} -> {RAW}")
    if not RAW.is_file() and os.environ.get("HF_TOKEN"):
        print("HF_TOKEN is set: fetching the snapshot from the private dataset (SHA-256 verified by the fetch script)")
        run([str(venv_python()), "deploy/fetch_snapshot.py"])
    if not RAW.is_file():
        sys.exit("The snapshot is missing. Put the PPRA Kenya OCDS file `full.jsonl.gz` at data/raw/full.jsonl.gz, or run\n"
                 "    python scripts/setup.py --snapshot PATH_TO/full.jsonl.gz\n"
                 f"It comes from the Open Contracting Data Registry, publication 147: {REGISTRY}\n"
                 "(this project froze the file downloaded on 8 Oct 2026; the registry may now offer a newer export).")
    actual, expected = sha256_of(RAW), recorded_checksum()
    if actual == expected:
        print(f"snapshot verified: SHA-256 {actual[:16]}... matches data/snapshot.sha256")
    elif allow_different:
        print(f"WARNING: this snapshot differs from the frozen one (SHA-256 {actual[:16]}... vs {expected[:16]}...). Numbers in EVALS.md will not reproduce.")
    else:
        sys.exit(f"This file is not the frozen snapshot (SHA-256 {actual[:16]}... vs recorded {expected[:16]}...).\n"
                 "Use the file downloaded on 8 Oct 2026, or add --allow-different-snapshot to continue anyway (results will differ).")


def step_database() -> None:
    say("3/5  Analysis database (DuckDB)")
    if DB.is_file() and DB.stat().st_size > 0:
        print(f"{DB} already exists; keeping it (delete it to rebuild)")
        return
    run([str(venv_python()), "data/loader.py"])


def step_env_file() -> None:
    say("4/5  Settings")
    env = ROOT / ".env"
    if not env.is_file():
        shutil.copyfile(ROOT / ".env.example", env)
        print("created .env from .env.example (local model: Ollama). Put GROQ_API_KEY in it and set LLM_PROVIDER=groq to use Groq.")
    else:
        print(".env already exists; left unchanged")


def step_checks() -> None:
    say("5/5  Checks")
    code = ("from agent.node_runtime import find_npx; print('Node.js OK (npx at ' + find_npx() + ')')")
    result = subprocess.run([str(venv_python()), "-c", code], cwd=ROOT, capture_output=True, text=True)
    print(result.stdout.strip() or "WARNING: " + (result.stderr.strip().splitlines() or ["Node.js check failed"])[-1])
    try:
        urllib.request.urlopen(OLLAMA_TAGS, timeout=3)
        print("Ollama is running (local model available; `ollama pull qwen2.5:7b` if you have not yet)")
    except OSError:
        print("Ollama is not running: start it and `ollama pull qwen2.5:7b`, or set LLM_PROVIDER=groq with a GROQ_API_KEY in .env")


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up Zabuni AI from a clean clone.")
    ap.add_argument("--snapshot", help="path to the frozen full.jsonl.gz")
    ap.add_argument("--allow-different-snapshot", action="store_true", help="continue even if the snapshot checksum differs")
    ap.add_argument("--skip-tests", action="store_true", help="do not run the test suite at the end")
    args = ap.parse_args()
    step_environment()
    step_snapshot(args.snapshot, args.allow_different_snapshot)
    step_database()
    step_env_file()
    step_checks()
    if not args.skip_tests:
        say("Tests (about 5-8 minutes; the live-LLM smoke test is skipped)")
        run([str(venv_python()), "-m", "pytest", "-q"], env={**os.environ, "ZABUNI_SKIP_LIVE": "1"})
    py = "py -3.12 " if os.name == "nt" else ""
    say("Done. Next:\n"
        f"  {venv_python().relative_to(ROOT)} -m streamlit run app/streamlit_app.py     # the web app\n"
        f"  {venv_python().relative_to(ROOT)} -m agent.run \"Review Makueni County Government awards 2022-2023\" --buyer \"Makueni County Government\" --years 2022,2023\n"
        f"  {venv_python().relative_to(ROOT)} -m agent.review <run_id>                    # approve / reject / clarify")


if __name__ == "__main__":
    main()
