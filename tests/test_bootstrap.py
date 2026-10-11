"""First-start preparation: secrets from Streamlit or the environment, and fetching + building the analysis database once."""
import importlib.util
from pathlib import Path

import pytest

from app import bootstrap
from app.bootstrap import BootstrapError


# ----------------------------------------------------------------------------- secrets
def test_a_streamlit_secret_wins_then_the_environment_then_none():
    assert bootstrap.secret("K", {"K": "from-secrets"}, {"K": "from-env"}) == "from-secrets"
    assert bootstrap.secret("K", {}, {"K": "from-env"}) == "from-env"
    assert bootstrap.secret("K", {}, {}) is None and bootstrap.secret("K", {"K": ""}, {}) is None


def test_a_missing_secrets_file_counts_as_not_set():
    class NoFile(dict):
        def __getitem__(self, key):
            raise FileNotFoundError("no secrets.toml")
    assert bootstrap.secret("K", NoFile(), {"K": "env"}) == "env"


def test_secrets_are_exported_to_the_environment_without_overriding_it_and_only_names_are_returned():
    environ = {"MODEL": "already-set"}
    names = bootstrap.export_secrets_to_env({"GROQ_API_KEY": "gsk_x", "HF_TOKEN": "hf_x", "MODEL": "other", "UNRELATED": "no"}, environ)
    assert environ["GROQ_API_KEY"] == "gsk_x" and environ["HF_TOKEN"] == "hf_x" and environ["MODEL"] == "already-set"
    assert "UNRELATED" not in environ and sorted(names) == ["GROQ_API_KEY", "HF_TOKEN"]
    assert not any("gsk_x" in n or "hf_x" in n for n in names)


# ----------------------------------------------------------------------------- the database
def fetch_mod():
    return bootstrap._load_fetch_module()


@pytest.fixture
def no_local_snapshot(monkeypatch, tmp_path):
    """Point the snapshot destination at an empty temp folder so a developer's real data/raw file is never touched."""
    mod = fetch_mod()
    monkeypatch.setattr(bootstrap, "_load_fetch_module", lambda: mod)
    monkeypatch.setattr(mod, "DEST", tmp_path / "raw" / "full.jsonl.gz")
    return mod


def test_an_existing_database_is_left_alone(tmp_path):
    db = tmp_path / "z.duckdb"
    db.write_bytes(b"x")
    calls = []
    assert bootstrap.ensure_database(None, fetch=lambda: calls.append("fetch"), build=lambda *a: calls.append("build"), db=db) == db
    assert calls == []


def test_the_first_start_fetches_builds_reports_progress_and_removes_the_raw_file(tmp_path, no_local_snapshot):
    mod, db, messages, order = no_local_snapshot, tmp_path / "data" / "z.duckdb", [], []

    def fetch():
        mod.DEST.parent.mkdir(parents=True, exist_ok=True)
        mod.DEST.write_bytes(b"raw")
        order.append("fetch")

    def build(src, target):
        assert src == mod.DEST and src.is_file()
        target.write_bytes(b"duckdb")
        order.append("build")
    assert bootstrap.ensure_database(messages.append, fetch=fetch, build=build, db=db) == db
    assert order == ["fetch", "build"] and db.read_bytes() == b"duckdb" and not mod.DEST.exists()
    assert any("Fetching" in m for m in messages) and any("Building" in m for m in messages) and messages[-1] == "Data ready."


def test_a_snapshot_already_on_disk_with_the_right_checksum_is_used_and_kept(tmp_path, no_local_snapshot, monkeypatch):
    mod = no_local_snapshot
    mod.DEST.parent.mkdir(parents=True)
    mod.DEST.write_bytes(b"my own copy")
    monkeypatch.setattr(mod, "expected_sha256", lambda: mod.sha256_of(mod.DEST))
    fetched = []

    def build(src, target):
        target.write_bytes(b"duckdb")
    bootstrap.ensure_database(None, fetch=lambda: fetched.append(1), build=build, db=tmp_path / "z.duckdb")
    assert fetched == [] and mod.DEST.read_bytes() == b"my own copy"        # not downloaded, not deleted


def test_a_fetch_failure_becomes_a_clear_error_and_builds_nothing(tmp_path, no_local_snapshot):
    mod = no_local_snapshot

    def fetch():
        raise mod.SnapshotError("No HF_TOKEN: add it as a secret")
    with pytest.raises(BootstrapError, match="could not be fetched: No HF_TOKEN"):
        bootstrap.ensure_database(None, fetch=fetch, build=lambda *a: pytest.fail("must not build"), db=tmp_path / "z.duckdb")
    assert not (tmp_path / "z.duckdb").exists()


def test_a_failing_loader_reports_its_last_output_and_leaves_no_partial_database(tmp_path, monkeypatch):
    class Result:
        returncode, stdout, stderr = 1, "", "Traceback ... MemoryError"
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda *a, **k: Result())
    with pytest.raises(BootstrapError, match="exit code 1.*MemoryError"):
        bootstrap._run_loader(tmp_path / "raw.gz", tmp_path / "z.duckdb")
    assert not list(tmp_path.glob("*.duckdb")) and not list(tmp_path.glob("*.building"))
