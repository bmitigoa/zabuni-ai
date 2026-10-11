"""Download the frozen OCDS snapshot from the private Hugging Face dataset and verify it.

    python deploy/fetch_snapshot.py            # used by the Docker build; writes data/raw/full.jsonl.gz

The snapshot (PPRA Kenya OCDS, downloaded 8 Oct 2026) is NOT in any public repository. It lives in a private dataset
(`bmitigoa/zabuni-ocds-snapshot`); the build authenticates with a read-only token and the file is accepted only if its SHA-256
equals the value recorded in this repository (`data/snapshot.sha256`). A different file, even a newer export of the same
publication, is refused: the data is frozen by content, not by name.

The token is read from the BuildKit secret mounted at /run/secrets/HF_TOKEN (Hugging Face Spaces and `docker build --secret`)
or from the HF_TOKEN environment variable; it is never printed and never written into the image.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_ID = os.environ.get("ZABUNI_SNAPSHOT_REPO", "bmitigoa/zabuni-ocds-snapshot")
FILENAME = "full.jsonl.gz"
DEST = ROOT / "data" / "raw" / FILENAME
CHECKSUM_FILE = ROOT / "data" / "snapshot.sha256"
SECRET_PATH = Path("/run/secrets/HF_TOKEN")
CHUNK = 1 << 20


class SnapshotError(RuntimeError):
    """The snapshot could not be fetched or did not match the recorded checksum (the message says what to do)."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_sha256(checksum_file: Path = CHECKSUM_FILE) -> str:
    """The recorded checksum (the first field of the first line, `sha256sum` format)."""
    try:
        first = Path(checksum_file).read_text(encoding="utf-8").split()[0].strip().lower()
    except (OSError, IndexError) as exc:
        raise SnapshotError(f"The recorded checksum is missing or empty: {checksum_file}") from exc
    if len(first) != 64 or any(c not in "0123456789abcdef" for c in first):
        raise SnapshotError(f"{checksum_file} does not start with a 64-character hexadecimal SHA-256.")
    return first


def read_token(environ: dict[str, str] | None = None, secret_path: Path = SECRET_PATH) -> str:
    """The Hugging Face token from the build secret file or the environment; an empty token is an error."""
    environ = os.environ if environ is None else environ
    token = ""
    if Path(secret_path).is_file():
        token = Path(secret_path).read_text(encoding="utf-8").strip()
    token = token or (environ.get("HF_TOKEN") or "").strip()
    if not token:
        raise SnapshotError("No HF_TOKEN: add it as a Space secret named HF_TOKEN (a read-only token for the private dataset "
                            f"{REPO_ID}), or for a local build pass `--secret id=HF_TOKEN,env=HF_TOKEN`.")
    return token


def verify(path: Path, expected: str) -> None:
    """Raise (and delete the file) unless the file's SHA-256 equals `expected`."""
    actual = sha256_of(path)
    if actual != expected:
        Path(path).unlink(missing_ok=True)
        raise SnapshotError(f"The downloaded file does not match the recorded SHA-256 (expected {expected}, got {actual}). "
                            "It was deleted. The build accepts only the frozen snapshot of 8 Oct 2026.")


def fetch(dest: Path = DEST, repo_id: str = REPO_ID, token: str | None = None, checksum_file: Path = CHECKSUM_FILE,
          downloader=None) -> Path:
    """Download `full.jsonl.gz` from the private dataset to `dest`, verify its checksum and return the path.

    `downloader` is `huggingface_hub.hf_hub_download` unless a test supplies a stand-in."""
    expected = expected_sha256(checksum_file)
    token = token or read_token()
    if downloader is None:
        from huggingface_hub import hf_hub_download as downloader   # imported here: only the build needs it
    try:
        cached = downloader(repo_id=repo_id, filename=FILENAME, repo_type="dataset", token=token)
    except Exception as exc:  # noqa: BLE001 - the Hub raises many types (auth, not found, network); never echo the token
        raise SnapshotError(f"Could not download {FILENAME} from the private dataset {repo_id} "
                            f"({type(exc).__name__}). Check that the dataset exists, that HF_TOKEN has read access to it and "
                            "that the file name is exactly full.jsonl.gz.") from None
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached, dest)
    verify(dest, expected)
    return dest


def main() -> int:
    try:
        path = fetch()
    except SnapshotError as exc:
        print(f"Snapshot fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"Snapshot verified (SHA-256 {sha256_of(path)}), {path.stat().st_size / 1e6:.1f} MB -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
