from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from lingtai import venv_resolve


def _write_python_executable(venv: Path) -> None:
    python = Path(venv_resolve.venv_python(venv))
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    python.chmod(0o755)


def _write_marker(venv: Path, marker: dict) -> None:
    venv.mkdir(parents=True, exist_ok=True)
    (venv / ".lingtai-env.json").write_text(
        json.dumps(marker),
        encoding="utf-8",
    )


def test_env_marker_missing_is_legacy(tmp_path: Path) -> None:
    assert venv_resolve._env_marker_status(tmp_path / "venv") == "missing"


def test_env_marker_detects_platform_mismatch(tmp_path: Path) -> None:
    marker = venv_resolve._current_process_env_marker()
    marker["os"] = "other-os"
    venv = tmp_path / "venv"
    _write_marker(venv, marker)

    assert venv_resolve._env_marker_status(venv) == "mismatch"


def test_env_marker_invalid_json_is_error_not_mismatch(tmp_path: Path) -> None:
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / ".lingtai-env.json").write_text("{", encoding="utf-8")

    assert venv_resolve._env_marker_status(venv) == "error"


def test_env_marker_probe_timeout_is_error_not_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venv = tmp_path / "venv"
    _write_python_executable(venv)
    _write_marker(venv, venv_resolve._current_process_env_marker())

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="python", timeout=10)

    monkeypatch.setattr(venv_resolve.subprocess, "run", timeout_run)

    assert venv_resolve._env_marker_status(venv) == "error"


def test_test_venv_rejects_mismatched_marker_without_deleting_explicit_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venv = tmp_path / "explicit"
    _write_python_executable(venv)
    marker = venv_resolve._current_process_env_marker()
    marker["arch"] = "other-arch"
    _write_marker(venv, marker)

    def fail_run(*_args, **_kwargs):
        raise AssertionError("mismatched marker should be rejected before import")

    monkeypatch.setattr(venv_resolve.subprocess, "run", fail_run)

    assert not venv_resolve._test_venv(venv)
    assert venv.exists()


def test_resolve_explicit_mismatched_marker_raises_without_deleting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venv = tmp_path / "explicit"
    _write_python_executable(venv)
    marker = venv_resolve._current_process_env_marker()
    marker["arch"] = "other-arch"
    _write_marker(venv, marker)
    (venv / "sentinel").write_text("keep", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("mismatched explicit venv should be rejected before import")

    monkeypatch.setattr(venv_resolve.subprocess, "run", fail_run)

    with pytest.raises(RuntimeError, match="Configured venv_path is not usable"):
        venv_resolve.resolve_venv({"venv_path": str(venv)})

    assert (venv / "sentinel").is_file()


def test_resolve_explicit_marker_error_accepts_importable_venv_without_deleting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    venv = tmp_path / "explicit"
    _write_python_executable(venv)
    (venv / "sentinel").write_text("keep", encoding="utf-8")
    (venv / ".lingtai-env.json").write_text("{", encoding="utf-8")

    def fake_run(args, **_kwargs):
        assert args[2] == "import lingtai"
        return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve.resolve_venv({"venv_path": str(venv)}) == venv
    assert (venv / "sentinel").is_file()
    assert "warning: configured venv_path environment marker" in capsys.readouterr().err


def test_resolve_written_back_default_runtime_self_heals_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = tmp_path / "runtime" / "venv"
    monkeypatch.setattr(venv_resolve, "_DEFAULT_RUNTIME_DIR", managed)
    _write_python_executable(managed)
    marker = venv_resolve._current_process_env_marker()
    marker["arch"] = "other-arch"
    _write_marker(managed, marker)
    (managed / "sentinel").write_text("stale", encoding="utf-8")
    created: list[Path] = []

    def fake_create_venv(venv_dir: Path) -> None:
        created.append(venv_dir)
        venv_dir.mkdir(parents=True, exist_ok=True)
        (venv_dir / "created").write_text("yes", encoding="utf-8")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("mismatched marker should be rejected before import")

    monkeypatch.setattr(venv_resolve, "_create_venv", fake_create_venv)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fail_run)

    assert venv_resolve.resolve_venv({"venv_path": str(managed)}) == managed
    assert created == [managed]
    assert not (managed / "sentinel").exists()
    assert (managed / "created").is_file()


def test_resolve_written_back_default_runtime_recreates_unusable_marker_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    managed = tmp_path / "runtime" / "venv"
    monkeypatch.setattr(venv_resolve, "_DEFAULT_RUNTIME_DIR", managed)
    _write_python_executable(managed)
    (managed / "sentinel").write_text("keep", encoding="utf-8")
    (managed / ".lingtai-env.json").write_text("{", encoding="utf-8")
    created: list[Path] = []

    def fake_run(args, **_kwargs):
        assert args[2] == "import lingtai"
        return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"broken")

    def fake_create_venv(venv_dir: Path) -> None:
        created.append(venv_dir)
        (venv_dir / "created").write_text("yes", encoding="utf-8")

    monkeypatch.setattr(venv_resolve, "_create_venv", fake_create_venv)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve.resolve_venv({"venv_path": str(managed)}) == managed
    assert created == [managed]
    assert (managed / "sentinel").is_file()
    assert (managed / "created").is_file()


def test_remove_mismatched_managed_venv(tmp_path: Path) -> None:
    venv = tmp_path / "managed"
    _write_python_executable(venv)
    marker = venv_resolve._current_process_env_marker()
    marker["arch"] = "other-arch"
    _write_marker(venv, marker)
    (venv / "sentinel").write_text("stale", encoding="utf-8")

    venv_resolve._remove_mismatched_managed_venv(venv)

    assert not venv.exists()


def test_remove_marker_error_does_not_delete_managed_venv(tmp_path: Path) -> None:
    venv = tmp_path / "managed"
    _write_python_executable(venv)
    (venv / "sentinel").write_text("keep", encoding="utf-8")
    (venv / ".lingtai-env.json").write_text("{", encoding="utf-8")

    venv_resolve._remove_mismatched_managed_venv(venv)

    assert (venv / "sentinel").is_file()


def test_test_venv_accepts_legacy_and_writes_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    venv = tmp_path / "legacy"
    _write_python_executable(venv)
    marker = venv_resolve._current_process_env_marker()

    def fake_run(args, **kwargs):
        script = args[2]
        if script == "import lingtai":
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "sysconfig.get_platform" in script:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout=json.dumps(
                    {
                        "os": marker["os"],
                        "arch": marker["arch"],
                        "python": marker["python"],
                    }
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected subprocess: {args!r}")

    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve._test_venv(venv)
    assert (venv / ".lingtai-env.json").is_file()
