"""Focused tests for the POSIX nested how-to-change-name helper."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py"
spec = importlib.util.spec_from_file_location("lingtai_change_name", SCRIPT)
change_name = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = change_name
assert spec.loader is not None
spec.loader.exec_module(change_name)


def _manifest(root: Path, *, address: str | None = None) -> None:
    (root / ".agent.json").write_text(json.dumps({
        "agent_id": "agent-123", "agent_name": "true-name",
        "address": address or root.name, "state": "ASLEEP",
    }), encoding="utf-8")


def test_jsonc_rebase_is_targeted_and_parseable(tmp_path: Path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    text = (
        '{\n'
        '  // "/tmp/old/in-comment" must remain untouched\n'
        '  "venv_path": "/tmp/old/runtime/venv",\n'
        '  "nested": {"cache": "/tmp/old/cache"},\n'
        '  "external": "/tmp/old-neighbor",\n'
        '  "relative": "runtime/old", // trailing comma\n'
        '}\n'
    ).replace("/tmp/old", str(old))
    result = change_name.rebase_jsonc_paths(text, old, new)
    assert '"venv_path": "' + str(new) + '/runtime/venv"' in result
    assert '"cache": "' + str(new) + '/cache"' in result
    assert f'"external": "{old}-neighbor"' in result
    assert '"relative": "runtime/old"' in result
    assert f'// "{old}/in-comment" must remain untouched' in result
    assert change_name.parse_jsonc(result)["venv_path"] == str(new / "runtime/venv")
    assert result.count("//") == text.count("//")


def test_rebase_does_not_rewrite_external_or_relative_strings(tmp_path: Path):
    old, new = tmp_path / "agent", tmp_path / "renamed"
    text = json.dumps({"a": str(old), "b": str(old / "x"), "c": "/outside/agent", "d": "agent/x"})
    result = change_name.rebase_jsonc_paths(text, old, new)
    data = json.loads(result)
    assert data == {"a": str(new), "b": str(new / "x"), "c": "/outside/agent", "d": "agent/x"}


@pytest.mark.parametrize("name", ["", ".hidden", "..", "a/b", "a b", "a." , "x" * 65])
def test_basename_policy_rejects_unsafe_names(name: str):
    assert not change_name._valid_name(name)


@pytest.mark.parametrize("name", ["new-agent", "agent_2", "研究员"])
def test_basename_policy_accepts_avatar_names(name: str):
    assert change_name._valid_name(name)


def test_exact_process_match_is_conservative(tmp_path: Path):
    root = tmp_path / "agent"
    assert change_name._is_exact_run(f"/usr/bin/python -m lingtai run {root}", root)
    assert change_name._is_exact_run(f"worker -m lingtai run {root}", root)  # module form is interpreter-agnostic
    assert not change_name._is_exact_run(f"/usr/bin/python -m lingtai run {root}-other", root)
    assert change_name._is_exact_run(f"/usr/local/bin/lingtai-agent run {root}", root)


def test_runtime_precedence_and_no_venv_resolution(tmp_path: Path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    in_dir = old / "runtime" / "venv" / "bin"
    in_dir.mkdir(parents=True)
    configured = in_dir / "python"
    configured.write_text("", encoding="utf-8")
    configured.chmod(0o755)
    external = tmp_path / "external-python"
    external.write_text("", encoding="utf-8")
    external.chmod(0o755)
    checked: list[Path] = []
    monkeypatch.setenv("LINGTAI_RUNTIME_PYTHON", str(external))
    monkeypatch.setattr(change_name, "_usable_runtime", lambda path, cwd: checked.append(path) or path == configured)
    choice = change_name.select_runtime({"venv_path": str(old / "runtime" / "venv")}, old)
    assert choice.executable == configured
    assert checked[0] == configured


def test_runtime_rejects_unusable_configured_path_instead_of_fallback(tmp_path: Path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    fallback = tmp_path / "fallback-python"
    monkeypatch.setenv("LINGTAI_RUNTIME_PYTHON", str(fallback))
    monkeypatch.setattr(change_name, "_usable_runtime", lambda path, cwd: path == fallback)
    with pytest.raises(change_name.ChangeNameError, match="refusing fallback"):
        change_name.select_runtime({"venv_path": str(old / "missing-venv")}, old)


def test_runtime_probe_does_not_write_bytecode_in_workdir_venv(tmp_path: Path, monkeypatch):
    old = tmp_path / "old"
    venv = old / "runtime" / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--copies", "--without-pip", str(venv)],
        check=True, text=True, capture_output=True, timeout=60,
    )
    site = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    package = site / "lingtai"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("value = 1\n", encoding="utf-8")
    runtime = venv / "bin" / "python"
    monkeypatch.setenv("PYTHONPATH", str(site))
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)

    before = {
        path.relative_to(old): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in old.rglob("*") if path.is_file()
    }
    before_pycache = {path.relative_to(old) for path in old.rglob("__pycache__")}
    assert change_name._usable_runtime(runtime, cwd=old)
    after = {
        path.relative_to(old): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in old.rglob("*") if path.is_file()
    }
    assert after == before
    assert {path.relative_to(old) for path in old.rglob("__pycache__")} == before_pycache


def test_preflight_captures_identity_and_rebases_runtime(tmp_path: Path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    (old / "init.json").write_text(json.dumps({"venv_path": str(old / "runtime" / "venv")}))
    _manifest(old)
    (old / ".agent.heartbeat").write_text(str(time.time()))
    runtime = old / "runtime" / "venv" / "bin" / "python"
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    runtime.chmod(0o755)
    monkeypatch.setattr(change_name, "exact_processes", lambda root: [(321, "python -m lingtai run " + str(root))])
    monkeypatch.setattr(change_name, "_lock_is_held", lambda root: True)
    monkeypatch.setattr(change_name, "select_runtime", lambda data, root: change_name.RuntimeChoice(runtime, runtime, "test"))
    pf = change_name.preflight(old, "new")
    assert pf.identity == change_name.Identity("agent-123", "true-name", "old")
    assert str(tmp_path / "new" / "runtime" / "venv" / "bin" / "python") in pf.runtime.rebased_executable.as_posix()
    assert change_name.parse_jsonc(pf.init_text)["venv_path"] == str(tmp_path / "new" / "runtime" / "venv")


def test_preflight_rejects_manifest_identity_mismatch(tmp_path: Path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "init.json").write_text("{}")
    _manifest(old, address="wrong")
    with pytest.raises(change_name.ChangeNameError, match="manifest address"):
        change_name.preflight(old, "new")


def test_shutdown_timeout_does_not_rename_and_receipt_is_truthful(tmp_path: Path, monkeypatch):
    old = tmp_path / "old"
    old.mkdir()
    _manifest(old)
    (old / "logs").mkdir()
    pf = change_name.Preflight(
        old, tmp_path / "new", change_name.Identity("id", "name", "old"), "{}",
        change_name.RuntimeChoice(Path(sys.executable), Path(sys.executable), "test"), 44,
    )
    monkeypatch.setattr(change_name, "preflight", lambda *args: pf)
    monkeypatch.setattr(change_name, "exact_processes", lambda root: [(44, "python -m lingtai run " + str(root))])
    monkeypatch.setattr(change_name, "heartbeat_fresh", lambda root: True)
    result = change_name.supervise(old, "new", timeout=0.02)
    assert result == 1
    assert old.is_dir() and not (tmp_path / "new").exists()
    receipt = json.loads((old / "logs" / change_name.RECEIPT_NAME).read_text())
    assert receipt["status"] == "failure"
    assert receipt["phase"] == "shutdown"
    assert (old / ".suspend").exists()


def test_receipt_is_machine_readable_and_atomic(tmp_path: Path):
    root = tmp_path / "old"
    root.mkdir()
    path = change_name.write_receipt(root, status="failure", phase="preflight", old=root, new=tmp_path / "new", error="bad")
    data = json.loads(path.read_text())
    assert data["status"] == "failure"
    assert data["old"] == str(root)
    assert data["error"] == "bad"


def test_manifest_update_preserves_identity_and_changes_only_address(tmp_path: Path):
    root = tmp_path / "new"
    root.mkdir()
    path = root / ".agent.json"
    path.write_text(json.dumps({"agent_id": "id", "agent_name": "name", "address": "old", "custom": 7}))
    change_name._update_manifest(path, change_name.Identity("id", "name", "old"), "new")
    data = json.loads(path.read_text())
    assert data["agent_id"] == "id" and data["agent_name"] == "name"
    assert data["address"] == "new" and data["custom"] == 7


def test_marker_cleanup_is_limited_to_lifecycle_markers(tmp_path: Path):
    root = tmp_path / "new"
    root.mkdir()
    for marker in change_name.MARKERS:
        (root / marker).touch()
    (root / "contacts.json").write_text("keep")
    change_name._remove_markers(root)
    assert not any((root / marker).exists() for marker in change_name.MARKERS)
    assert (root / "contacts.json").read_text() == "keep"


def test_detached_handoff_uses_new_session_devnull_and_append_log(tmp_path: Path, monkeypatch, capsys):
    old = tmp_path / "old"
    old.mkdir()
    (old / "logs").mkdir()
    pf = change_name.Preflight(old, tmp_path / "new", change_name.Identity("id", "name", "old"), "{}", change_name.RuntimeChoice(Path(sys.executable), Path(sys.executable), "test"), 9)
    monkeypatch.setattr(change_name, "preflight", lambda *args: pf)
    seen = {}

    class FakePopen:
        pid = 987
        def __init__(self, argv, **kwargs):
            seen["argv"] = argv
            seen["kwargs"] = kwargs

    monkeypatch.setattr(change_name.subprocess, "Popen", FakePopen)
    assert change_name._handoff(old, "new", 4) == 0
    assert seen["argv"][1:3] == [str(SCRIPT), "--_supervise"]
    assert seen["kwargs"]["start_new_session"] is True
    assert seen["kwargs"]["stdin"] is change_name.subprocess.DEVNULL
    assert "name-change-supervisor.log" in str(seen["kwargs"]["stdout"].name)
    seen["kwargs"]["stdout"].close()
    assert "handed off" in capsys.readouterr().out
