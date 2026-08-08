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


_SELECTOR_PROBE = (
    "import json, platform, sys; "
    "print(json.dumps({'version': list(sys.version_info[:3]), "
    "'sys_platform': sys.platform, 'machine': platform.machine(), "
    "'macos_version': platform.mac_ver()[0] if sys.platform == 'darwin' else ''}))"
)


def _set_selector_host(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sys_platform: str,
    machine: str,
    macos_version: str = "",
) -> None:
    monkeypatch.setattr(venv_resolve.sys, "platform", sys_platform)
    monkeypatch.setattr(venv_resolve.platform, "machine", lambda: machine)
    monkeypatch.setattr(
        venv_resolve.platform,
        "mac_ver",
        lambda: (macos_version, ("", "", ""), machine),
    )


def _selector_payload(
    version: tuple[int, int, int],
    *,
    sys_platform: str,
    machine: str,
    macos_version: str = "",
) -> str:
    return json.dumps(
        {
            "version": list(version),
            "sys_platform": sys_platform,
            "machine": machine,
            "macos_version": macos_version,
        }
    )


def _completed_probe(
    path: str,
    version: tuple[int, int, int],
    *,
    sys_platform: str,
    machine: str,
    macos_version: str = "",
) -> subprocess.CompletedProcess[str]:
    args = [path, "-c", _SELECTOR_PROBE]
    return subprocess.CompletedProcess(
        args,
        0,
        stdout=_selector_payload(
            version,
            sys_platform=sys_platform,
            machine=machine,
            macos_version=macos_version,
        ),
        stderr="",
    )


def _assert_selector_probe_call(args: list[str], kwargs: dict, path: str) -> None:
    assert args == [path, "-c", _SELECTOR_PROBE]
    assert kwargs == {"capture_output": True, "text": True, "timeout": 5}


@pytest.mark.parametrize(
    ("sys_platform", "machine", "macos_version", "expected_names"),
    [
        (
            "darwin",
            "aarch64",
            "14.7",
            ("python3.14", "python3.13", "python3.12", "python3.11", "python3", "python"),
        ),
        (
            "darwin",
            "arm64",
            "13.6",
            ("python3.13", "python3.12", "python3.11", "python3", "python"),
        ),
        (
            "darwin",
            "amd64",
            "15.1",
            ("python3.13", "python3.12", "python3.11", "python3", "python"),
        ),
        (
            "linux",
            "x86_64",
            "",
            ("python3", "python", "python3.14", "python3.13", "python3.12", "python3.11"),
        ),
        (
            "win32",
            "amd64",
            "",
            ("python3", "python", "python3.14", "python3.13", "python3.12", "python3.11"),
        ),
    ],
)
def test_find_python_uses_exact_target_candidate_order(
    monkeypatch: pytest.MonkeyPatch,
    sys_platform: str,
    machine: str,
    macos_version: str,
    expected_names: tuple[str, ...],
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform=sys_platform,
        machine=machine,
        macos_version=macos_version,
    )
    searched: list[str] = []

    def fake_which(name: str) -> None:
        searched.append(name)
        return None

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)

    with pytest.raises(RuntimeError, match="No compatible Python interpreter found"):
        venv_resolve._find_python()

    assert searched == list(expected_names)


def test_arm_macos14_prefers_compatible_python314(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine="arm64",
        macos_version="14.6",
    )
    searched: list[str] = []

    def fake_which(name: str) -> str:
        searched.append(name)
        return f"/mock/{name}"

    def fake_run(args: list[str], **kwargs):
        _assert_selector_probe_call(args, kwargs, "/mock/python3.14")
        return _completed_probe(
            args[0],
            (3, 14, 2),
            sys_platform="darwin",
            machine="arm64",
            macos_version="14.6",
        )

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve._find_python() == "/mock/python3.14"
    assert searched == ["python3.14"]


@pytest.mark.parametrize("machine", ["arm64", "x86_64"])
def test_restricted_macos_rejects_generic_python314_then_uses_python313(
    monkeypatch: pytest.MonkeyPatch,
    machine: str,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine=machine,
        macos_version="13.6",
    )
    paths = {"python3": "/mock/generic", "python": "/mock/fallback"}
    probed: list[str] = []

    def fake_which(name: str) -> str | None:
        return paths.get(name)

    def fake_run(args: list[str], **kwargs):
        path = args[0]
        _assert_selector_probe_call(args, kwargs, path)
        probed.append(path)
        version = (3, 14, 1) if path == "/mock/generic" else (3, 13, 7)
        return _completed_probe(
            path,
            version,
            sys_platform="darwin",
            machine=machine,
            macos_version="13.6",
        )

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve._find_python() == "/mock/fallback"
    assert probed == ["/mock/generic", "/mock/fallback"]


@pytest.mark.parametrize(
    ("sys_platform", "version"),
    [
        ("linux", (3, 14, 0)),
        ("linux", (3, 99, 1)),
        ("win32", (3, 14, 0)),
        ("win32", (3, 99, 1)),
    ],
)
def test_non_macos_accepts_generic_open_ended_python(
    monkeypatch: pytest.MonkeyPatch,
    sys_platform: str,
    version: tuple[int, int, int],
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform=sys_platform,
        machine="x86_64",
    )
    child_platform = "win32" if sys_platform == "win32" else "linux"
    searched: list[str] = []

    def fake_which(name: str) -> str:
        searched.append(name)
        return "/mock/generic"

    def fake_run(args: list[str], **kwargs):
        _assert_selector_probe_call(args, kwargs, "/mock/generic")
        return _completed_probe(
            args[0],
            version,
            sys_platform=child_platform,
            machine="x86_64",
        )

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve._find_python() == "/mock/generic"
    assert searched == ["python3"]


@pytest.mark.parametrize(
    "bad_result",
    [
        "timeout",
        "launch-error",
        "nonzero",
        "malformed-json",
        "too-old",
        "too-new",
        "wrong-platform",
        "wrong-architecture",
        "wrong-macos-class",
    ],
)
def test_bad_probe_rejects_only_candidate_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
    bad_result: str,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine="arm64",
        macos_version="14.4",
    )
    paths = {"python3.14": "/mock/bad", "python3.13": "/mock/good"}
    probed: list[str] = []

    def fake_which(name: str) -> str | None:
        return paths.get(name)

    def fake_run(args: list[str], **kwargs):
        path = args[0]
        _assert_selector_probe_call(args, kwargs, path)
        probed.append(path)
        if path == "/mock/good":
            return _completed_probe(
                path,
                (3, 13, 9),
                sys_platform="darwin",
                machine="aarch64",
                macos_version="15.0",
            )
        if bad_result == "timeout":
            raise subprocess.TimeoutExpired(args, 5)
        if bad_result == "launch-error":
            raise OSError("permission denied")
        if bad_result == "nonzero":
            return subprocess.CompletedProcess(args, 7, stdout="", stderr="broken")
        if bad_result == "malformed-json":
            return subprocess.CompletedProcess(args, 0, stdout="not-json", stderr="")
        if bad_result == "too-old":
            version = (3, 10, 14)
            child_platform, machine, macos_version = "darwin", "arm64", "14.4"
        elif bad_result == "too-new":
            version = (3, 15, 0)
            child_platform, machine, macos_version = "darwin", "arm64", "14.4"
        elif bad_result == "wrong-platform":
            version = (3, 14, 0)
            child_platform, machine, macos_version = "linux", "arm64", ""
        elif bad_result == "wrong-architecture":
            version = (3, 14, 0)
            child_platform, machine, macos_version = "darwin", "x86_64", "14.4"
        else:
            version = (3, 14, 0)
            child_platform, machine, macos_version = "darwin", "arm64", "13.6"
        return _completed_probe(
            path,
            version,
            sys_platform=child_platform,
            machine=machine,
            macos_version=macos_version,
        )

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve._find_python() == "/mock/good"
    assert probed == ["/mock/bad", "/mock/good"]


def test_find_python_deduplicates_aliases_by_resolved_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine="arm64",
        macos_version="14.4",
    )
    shared = tmp_path / "shared-python"
    shared.write_text("", encoding="utf-8")
    alias_one = tmp_path / "python3.14"
    alias_two = tmp_path / "python3.13"
    alias_one.symlink_to(shared)
    alias_two.symlink_to(shared)
    good = tmp_path / "python3.12"
    good.write_text("", encoding="utf-8")
    paths = {
        "python3.14": str(alias_one),
        "python3.13": str(alias_two),
        "python3.12": str(good),
    }
    probed: list[str] = []

    def fake_which(name: str) -> str | None:
        return paths.get(name)

    def fake_run(args: list[str], **kwargs):
        path = args[0]
        _assert_selector_probe_call(args, kwargs, path)
        probed.append(path)
        if path == str(alias_one):
            return subprocess.CompletedProcess(args, 0, stdout="not-json", stderr="")
        return _completed_probe(
            path,
            (3, 12, 8),
            sys_platform="darwin",
            machine="arm64",
            macos_version="14.4",
        )

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    assert venv_resolve._find_python() == str(good)
    assert probed == [str(alias_one), str(good)]


@pytest.mark.parametrize(
    ("macos_version", "machine", "message"),
    [
        ("", "arm64", "macOS version is missing"),
        ("not-a-version", "arm64", "Cannot parse macOS version"),
        ("12.7", "arm64", "macOS 13 or newer"),
        ("14.4", "riscv64", "Unsupported macOS architecture"),
    ],
)
def test_invalid_macos_target_fails_before_search_or_probe(
    monkeypatch: pytest.MonkeyPatch,
    macos_version: str,
    machine: str,
    message: str,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine=machine,
        macos_version=macos_version,
    )

    def fail_which(_name: str):
        raise AssertionError("invalid macOS target must fail before PATH search")

    def fail_run(*_args, **_kwargs):
        raise AssertionError("invalid macOS target must fail before probing")

    monkeypatch.setattr(venv_resolve.shutil, "which", fail_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fail_run)

    with pytest.raises(RuntimeError, match=message):
        venv_resolve._find_python()


def test_selection_remedy_lists_supported_range_in_ascending_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine="arm64",
        macos_version="13.6",
    )

    policy = venv_resolve._python_selection_policy()

    assert venv_resolve._selection_remedy(policy).startswith(
        "Install arm64 Python 3.11-3.13"
    )


def test_exhaustion_is_actionable_and_starts_no_venv_or_pip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform="darwin",
        machine="arm64",
        macos_version="13.6",
    )
    paths = {
        "python3.13": "/mock/python-too-new",
        "python3.12": "/mock/python-malformed",
        "python3.11": "/mock/python-timeout",
    }
    subprocess_calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return paths.get(name)

    def fake_run(args: list[str], **kwargs):
        path = args[0]
        _assert_selector_probe_call(args, kwargs, path)
        subprocess_calls.append(args)
        if path == "/mock/python-too-new":
            return _completed_probe(
                path,
                (3, 14, 0),
                sys_platform="darwin",
                machine="arm64",
                macos_version="13.6",
            )
        if path == "/mock/python-malformed":
            return subprocess.CompletedProcess(args, 0, stdout="{", stderr="")
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(venv_resolve.shutil, "which", fake_which)
    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        venv_resolve._create_venv(tmp_path / "managed" / "venv")

    assert not (tmp_path / "managed").exists()
    message = str(exc_info.value)
    assert "No compatible Python interpreter found" in message
    assert "darwin" in message
    assert "arm64" in message
    assert "macOS 13.6" in message
    assert "3.11-3.13" in message
    for name in ("python3.13", "python3.12", "python3.11", "python3", "python"):
        assert name in message
    assert "/mock/python-too-new: Python 3.14 is outside supported range 3.11-3.13" in message
    assert "/mock/python-malformed: malformed JSON" in message
    assert "/mock/python-timeout: probe timed out after 5 seconds" in message
    assert "Install arm64 Python 3.11-3.13" in message
    assert "PATH" in message
    assert "venv_path" in message
    assert subprocess_calls
    assert all(call[1:] == ["-c", _SELECTOR_PROBE] for call in subprocess_calls)


@pytest.mark.parametrize(
    ("sys_platform", "expected_pip"),
    [
        ("linux", Path("bin/pip")),
        ("win32", Path("Scripts/pip.exe")),
    ],
)
def test_create_venv_preserves_platform_specific_pip_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sys_platform: str,
    expected_pip: Path,
) -> None:
    _set_selector_host(
        monkeypatch,
        sys_platform=sys_platform,
        machine="x86_64",
    )
    venv = tmp_path / "venv"
    calls: list[list[str]] = []
    monkeypatch.setattr(venv_resolve, "_find_python", lambda: "/mock/python")
    monkeypatch.setattr(venv_resolve, "_write_env_marker_best_effort", lambda _path: None)

    def fake_run(args: list[str], **kwargs):
        calls.append(args)
        assert kwargs == {"check": True}
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(venv_resolve.subprocess, "run", fake_run)

    venv_resolve._create_venv(venv)

    assert calls == [
        ["/mock/python", "-m", "venv", str(venv)],
        [str(venv / expected_pip), "install", "lingtai"],
    ]
