"""Finding a Node.js that can run the pinned Filesystem MCP server (needs Node >= 20 because of its minimatch dependency)."""
import subprocess
from types import SimpleNamespace

import pytest

from agent import node_runtime as nr


def fake_node_versions(monkeypatch, versions):
    """versions: {path: 'v18.19.0'}; any other path cannot be run."""
    def run(cmd, **kwargs):
        if cmd[0] in versions:
            return SimpleNamespace(stdout=versions[cmd[0]] + "\n")
        raise OSError("not found")
    monkeypatch.setattr(subprocess, "run", run)


def test_the_version_output_is_parsed_and_an_unrunnable_node_is_none(monkeypatch):
    fake_node_versions(monkeypatch, {"node-22": "v22.4.1", "node-junk": "hello"})
    assert nr.node_major("node-22") == 22 and nr.node_major("node-junk") is None and nr.node_major("missing") is None


def test_a_new_enough_system_node_is_used(monkeypatch):
    fake_node_versions(monkeypatch, {"/usr/bin/node": "v22.1.0"})
    monkeypatch.setattr(nr.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert nr.find_npx() == "/usr/bin/npx"


def test_an_old_system_node_falls_back_to_the_pip_installed_one_and_puts_it_first_on_the_path(monkeypatch, tmp_path):
    wheel = tmp_path / "wheel_bin"
    wheel.mkdir()
    (wheel / "node").write_text("", encoding="utf-8")
    (wheel / "npx").write_text("", encoding="utf-8")
    fake_node_versions(monkeypatch, {"/usr/bin/node": "v18.19.0", str(wheel / "node"): "v24.19.0"})
    monkeypatch.setattr(nr.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(nr, "wheel_directories", lambda: [str(wheel)])
    monkeypatch.setenv("PATH", "/usr/bin")
    assert nr.find_npx() == str(wheel / "npx")
    assert nr.os.environ["PATH"].startswith(str(wheel))                 # npx will run the new node, not the old one


def test_without_a_usable_node_the_error_says_what_to_install(monkeypatch):
    fake_node_versions(monkeypatch, {"/usr/bin/node": "v12.22.9"})
    monkeypatch.setattr(nr.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(nr, "wheel_directories", lambda: [])
    with pytest.raises(nr.NodeNotFound, match="nodejs-wheel-binaries") as exc:
        nr.find_npx()
    assert "system node 12" in str(exc.value) and "20 or newer" in str(exc.value)


def test_on_this_machine_a_node_is_found():
    assert nr.find_npx()                                               # the development machine runs the agent, so it must have one
