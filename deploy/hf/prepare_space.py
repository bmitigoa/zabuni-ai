"""Stage the Hugging Face Space repository:  python deploy/hf/prepare_space.py [--out build/space]

Copies exactly what the container needs into a clean folder, adds the Space's README header (sdk: docker, port 7860) and a
.gitattributes, and refuses to include anything private (.env, logs, outputs,
manuals). Nothing is uploaded: you push the folder yourself (see deploy/hf/DEPLOY.md).

The OCDS snapshot is NOT staged: the Docker build downloads it from a private Hugging Face dataset and verifies its SHA-256
(data/snapshot.sha256), see deploy/fetch_snapshot.py. It is never re-downloaded from the registry.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
INCLUDE_DIRS = ["agent", "app", "mcp_server"]
INCLUDE_FILES = ["requirements.txt", "Dockerfile", ".dockerignore", "LICENSE", "data/loader.py", "data/snapshot.sha256", "deploy/fetch_snapshot.py"]
FORBIDDEN = (".env", "CLAUDE.md", "logs", "outputs", "docs/daily", ".venv", ".venv312", "tests", "data/zabuni.duckdb", "data/raw")
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "*.duckdb", "*.sqlite")

SPACE_README = """---
title: Zabuni AI
emoji: ⚖️
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Agentic decision support for public-procurement review (Kenya OCDS)
---

# Zabuni AI (hosted demo)

Agentic **decision support** for public-procurement review: a LangGraph agent plans an investigation of published Kenyan
tender awards (PPRA, Open Contracting Data Standard), calls tools through MCP, and raises flags that each cite their OCDS
source record. A human reviewer approves, rejects or queries every flag; only approved flags enter the memo.
**It never recommends or awards a tender.**

* Model: open-weights `qwen/qwen3.8-27b` on Groq (fallback `openai/gpt-oss-120b`). The free tier is limited: if the daily quota is
  used the app says so and points to the recorded demo video.
* Data: the frozen PPRA Kenya OCDS snapshot of 8 Oct 2026 (publication 147, Open Contracting Data Registry). No live data, no personal data.
* Source code, architecture and limitations: https://github.com/bmitigoa/zabuni-ai
"""

GITATTRIBUTES = "data/raw/*.gz filter=lfs diff=lfs merge=lfs -text\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "build" / "space"))
    args = ap.parse_args()
    out = Path(args.out).resolve()
    if not (ROOT / "data" / "snapshot.sha256").is_file():
        print("data/snapshot.sha256 is missing: it records the checksum the build verifies.", file=sys.stderr)
        return 1
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for d in INCLUDE_DIRS:
        shutil.copytree(ROOT / d, out / d, ignore=IGNORE)
    for f in INCLUDE_FILES:
        (out / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, out / f)
    (out / "data").mkdir(exist_ok=True)
    (out / "README.md").write_text(SPACE_README, encoding="utf-8")
    (out / ".gitattributes").write_text(GITATTRIBUTES, encoding="utf-8")
    bad = [p for p in FORBIDDEN if (out / p).exists()]
    if bad:
        print(f"Refusing: private paths were staged: {bad}", file=sys.stderr)
        return 2
    files = sorted(p for p in out.rglob("*") if p.is_file())
    size_mb = sum(p.stat().st_size for p in files) / 1e6
    print(f"Staged {len(files)} files ({size_mb:.1f} MB) in {out}")
    big = [p for p in files if p.stat().st_size > 1_000_000]
    if big:
        print(f"Refusing: a file over 1 MB was staged (the OCDS snapshot must stay out of the Space repo): {big}", file=sys.stderr)
        return 3
    print("Next: see deploy/hf/DEPLOY.md (you push this folder to your Space; nothing was uploaded).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
