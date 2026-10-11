"""The snapshot is fetched from a private dataset and verified by SHA-256; the Space repository never contains it."""
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_mod = load("fetch_snapshot", "deploy/fetch_snapshot.py")
SnapshotError = fetch_mod.SnapshotError


def make_source(tmp_path, content=b"frozen snapshot bytes"):
    src = tmp_path / "cached" / "full.jsonl.gz"
    src.parent.mkdir()
    src.write_bytes(content)
    checksum = tmp_path / "snapshot.sha256"
    checksum.write_text(f"{hashlib.sha256(b'frozen snapshot bytes').hexdigest()}  full.jsonl.gz\n", encoding="utf-8")
    return src, checksum


def downloader_for(src, calls=None):
    def download(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return str(src)
    return download


def test_the_recorded_checksum_in_the_repository_matches_the_local_snapshot_when_present():
    recorded = fetch_mod.expected_sha256()
    assert len(recorded) == 64
    local = ROOT / "data" / "raw" / "full.jsonl.gz"
    if local.is_file():
        assert fetch_mod.sha256_of(local) == recorded                          # the value in git is the snapshot of 8 Oct


def test_a_matching_file_is_downloaded_from_the_private_dataset_and_installed(tmp_path):
    src, checksum = make_source(tmp_path)
    calls = []
    dest = fetch_mod.fetch(dest=tmp_path / "data" / "raw" / "full.jsonl.gz", token="hf_secret", checksum_file=checksum,
                           downloader=downloader_for(src, calls))
    assert dest.read_bytes() == b"frozen snapshot bytes"
    assert calls == [{"repo_id": "bmitigoa/zabuni-ocds-snapshot", "filename": "full.jsonl.gz", "repo_type": "dataset", "token": "hf_secret"}]


def test_a_file_with_a_different_checksum_is_refused_and_deleted(tmp_path):
    src, checksum = make_source(tmp_path, content=b"a newer export of the same publication")
    dest = tmp_path / "data" / "raw" / "full.jsonl.gz"
    with pytest.raises(SnapshotError, match="does not match the recorded SHA-256"):
        fetch_mod.fetch(dest=dest, token="t", checksum_file=checksum, downloader=downloader_for(src))
    assert not dest.exists()


def test_a_download_failure_is_reported_without_echoing_the_token(tmp_path):
    _, checksum = make_source(tmp_path)

    def failing(**kwargs):
        raise OSError("401 Unauthorized for token hf_supersecret")
    with pytest.raises(SnapshotError) as exc:
        fetch_mod.fetch(dest=tmp_path / "x.gz", token="hf_supersecret", checksum_file=checksum, downloader=failing)
    assert "hf_supersecret" not in str(exc.value) and "read access" in str(exc.value)


def test_the_token_comes_from_the_build_secret_file_or_the_environment_and_is_required(tmp_path):
    secret = tmp_path / "HF_TOKEN"
    secret.write_text("from-secret-file\n", encoding="utf-8")
    assert fetch_mod.read_token({}, secret) == "from-secret-file"
    assert fetch_mod.read_token({"HF_TOKEN": "from-env"}, tmp_path / "absent") == "from-env"
    with pytest.raises(SnapshotError, match="No HF_TOKEN"):
        fetch_mod.read_token({}, tmp_path / "absent")


def test_a_malformed_checksum_file_is_rejected(tmp_path):
    bad = tmp_path / "bad.sha256"
    bad.write_text("not-a-hash  full.jsonl.gz\n", encoding="utf-8")
    with pytest.raises(SnapshotError, match="64-character"):
        fetch_mod.expected_sha256(bad)
    with pytest.raises(SnapshotError, match="missing or empty"):
        fetch_mod.expected_sha256(tmp_path / "nope")


def test_the_script_exits_non_zero_with_a_clear_message_when_no_token_is_available(tmp_path):
    env = {"PATH": "", "SYSTEMROOT": "C:\\Windows"}
    out = subprocess.run([sys.executable, str(ROOT / "deploy" / "fetch_snapshot.py")], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 1 and "Snapshot fetch failed: No HF_TOKEN" in out.stderr


def test_the_staged_space_repository_has_no_snapshot_and_nothing_large(tmp_path):
    out = tmp_path / "space"
    result = subprocess.run([sys.executable, str(ROOT / "deploy" / "hf" / "prepare_space.py"), "--out", str(out)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert not (out / "data" / "raw").exists() and not list(out.rglob("*.gz"))
    assert (out / "data" / "snapshot.sha256").is_file() and (out / "deploy" / "fetch_snapshot.py").is_file()
    assert all(p.stat().st_size < 1_000_000 for p in out.rglob("*") if p.is_file())
    assert not (out / ".env").exists() and not (out / "CLAUDE.md").exists()


def test_the_dockerfile_fetches_the_snapshot_with_a_build_secret_and_never_copies_it():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--mount=type=secret,id=HF_TOKEN" in text and "deploy/fetch_snapshot.py" in text
    assert "HF_TOKEN=" not in text and "ENV HF_TOKEN" not in text                            # the token is never baked in
    assert "full.jsonl.gz" in text.split("fetch_snapshot.py")[1]                             # the raw file is deleted after loading
