"""Resolve the Python venv for running lingtai agents.

Resolution order:
1. init.json → venv_path → test → use if working
2. ~/.lingtai-tui/runtime/venv/ → test → use if working
3. Neither → create ~/.lingtai-tui/runtime/venv/ automatically
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path


_DEFAULT_RUNTIME_DIR = Path.home() / ".lingtai-tui" / "runtime" / "venv"
_ENV_MARKER_FILE = ".lingtai-env.json"
_ENV_MARKER_SCHEMA = "lingtai.runtime-env"
_ENV_MARKER_SCHEMA_VERSION = 1
_LINGTAI_ENV_VERSION = 1
_MARKER_MISSING = "missing"
_MARKER_MATCH = "match"
_MARKER_MISMATCH = "mismatch"
_MARKER_ERROR = "error"

_PYTHON_ENV_PROBE = r"""
import json
import platform
import sys
import sysconfig

def _goos():
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform

def _goarch():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("i386", "i686", "x86"):
        return "386"
    if machine.startswith("arm"):
        return "arm"
    return machine

print(json.dumps({
    "os": _goos(),
    "arch": _goarch(),
    "python": {
        "sys_platform": sys.platform,
        "machine": platform.machine(),
        "sysconfig_platform": sysconfig.get_platform(),
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "version_major": sys.version_info.major,
        "version_minor": sys.version_info.minor,
        "version_micro": sys.version_info.micro,
    },
}, sort_keys=True))
"""


def resolve_venv(init_data: dict | None = None) -> Path:
    """Return the path to a working venv directory.

    Tries init.json venv_path first, then ~/.lingtai-tui/runtime/venv/.
    Auto-creates the global venv if nothing works.
    """
    # 1. init.json venv_path
    if init_data and init_data.get("venv_path"):
        venv = Path(init_data["venv_path"]).expanduser()
        # cli.run writes the managed runtime path into init.json. Keep that path
        # on the managed branch so marker mismatches can self-heal by recreating it.
        if not _is_default_runtime_dir(venv):
            ok, detail = _test_venv_detail(venv, warn_marker_error=True)
            if ok:
                return venv
            raise RuntimeError(f"Configured venv_path is not usable: {venv}: {detail}")

    # 2. ~/.lingtai-tui/runtime/venv/
    if _test_venv(_DEFAULT_RUNTIME_DIR):
        return _DEFAULT_RUNTIME_DIR

    # 3. Auto-create
    _remove_mismatched_managed_venv(_DEFAULT_RUNTIME_DIR)
    _create_venv(_DEFAULT_RUNTIME_DIR)
    return _DEFAULT_RUNTIME_DIR


def venv_python(venv_dir: Path) -> str:
    """Return the path to the Python executable inside a venv."""
    if sys.platform == "win32":
        return str(venv_dir / "Scripts" / "python.exe")
    return str(venv_dir / "bin" / "python")


def _is_default_runtime_dir(venv_dir: Path) -> bool:
    try:
        return (
            venv_dir.expanduser().resolve(strict=False)
            == _DEFAULT_RUNTIME_DIR.expanduser().resolve(strict=False)
        )
    except OSError:
        return venv_dir.expanduser() == _DEFAULT_RUNTIME_DIR.expanduser()


def _test_venv(venv_dir: Path) -> bool:
    """Test that a venv exists and has lingtai importable."""
    ok, _detail = _test_venv_detail(venv_dir, warn_marker_error=False)
    return ok


def _test_venv_detail(venv_dir: Path, *, warn_marker_error: bool) -> tuple[bool, str]:
    """Return whether a venv is usable plus a short failure reason."""
    python = venv_python(venv_dir)
    if not os.path.isfile(python):
        return False, f"python executable missing at {python}"
    marker_status, marker_detail = _env_marker_status_detail(venv_dir)
    if marker_status == _MARKER_MISMATCH:
        return False, marker_detail or "environment marker mismatch"
    try:
        result = subprocess.run(
            [python, "-c", "import lingtai"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return False, detail or "import lingtai failed"
        if warn_marker_error and marker_status == _MARKER_ERROR:
            detail = marker_detail or "environment marker could not be verified"
            print(
                "warning: configured venv_path environment marker could not be "
                f"verified; using venv after import check: {detail}",
                file=sys.stderr,
            )
        if marker_status == _MARKER_MISSING:
            _write_env_marker_best_effort(venv_dir)
        return True, ""
    except OSError as exc:
        return False, str(exc)
    except subprocess.TimeoutExpired:
        return False, "import lingtai timed out"


def _create_venv(venv_dir: Path) -> None:
    """Create a fresh venv and install lingtai into it."""
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"Creating lingtai runtime at {venv_dir} ...", file=sys.stderr)

    # Find a working Python 3.11+
    python = _find_python()
    if not python:
        raise RuntimeError(
            "Cannot create venv: Python 3.11+ not found. "
            "Install Python from python.org and try again."
        )

    # Create venv
    subprocess.run(
        [python, "-m", "venv", str(venv_dir)],
        check=True,
    )

    # Install lingtai
    pip = str(venv_dir / "bin" / "pip")
    if sys.platform == "win32":
        pip = str(venv_dir / "Scripts" / "pip.exe")

    print("Installing lingtai...", file=sys.stderr)
    subprocess.run(
        [pip, "install", "lingtai"],
        check=True,
    )
    _write_env_marker_best_effort(venv_dir)
    print("Runtime ready.", file=sys.stderr)


def _find_python() -> str | None:
    """Find a Python ≥ 3.11 on the system."""
    for name in ("python3", "python"):
        path = shutil.which(name)
        if path:
            try:
                result = subprocess.run(
                    [path, "-c",
                     "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"],
                    capture_output=True, timeout=5,
                )
                if result.returncode == 0:
                    return path
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


def _marker_path(venv_dir: Path) -> Path:
    return venv_dir / _ENV_MARKER_FILE


def _goos() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


def _goarch(machine: str | None = None) -> str:
    value = (machine or platform.machine()).lower()
    if value in ("x86_64", "amd64"):
        return "amd64"
    if value in ("aarch64", "arm64"):
        return "arm64"
    if value in ("i386", "i686", "x86"):
        return "386"
    if value.startswith("arm"):
        return "arm"
    return value


def _current_process_env_marker() -> dict:
    return {
        "schema": _ENV_MARKER_SCHEMA,
        "schema_version": _ENV_MARKER_SCHEMA_VERSION,
        "lingtai_env_version": _LINGTAI_ENV_VERSION,
        "os": _goos(),
        "arch": _goarch(),
        "python": {
            "sys_platform": sys.platform,
            "machine": platform.machine(),
            "sysconfig_platform": sysconfig.get_platform(),
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "version_major": sys.version_info.major,
            "version_minor": sys.version_info.minor,
            "version_micro": sys.version_info.micro,
        },
    }


def _current_venv_env_marker(venv_dir: Path) -> dict:
    result = subprocess.run(
        [venv_python(venv_dir), "-c", _PYTHON_ENV_PROBE],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or "environment marker probe failed")
    marker = json.loads(result.stdout)
    marker["schema"] = _ENV_MARKER_SCHEMA
    marker["schema_version"] = _ENV_MARKER_SCHEMA_VERSION
    marker["lingtai_env_version"] = _LINGTAI_ENV_VERSION
    return marker


def _env_marker_matches(marker: dict, current: dict | None = None) -> bool:
    current = current or _current_process_env_marker()
    if marker.get("schema") != _ENV_MARKER_SCHEMA:
        return False
    if marker.get("schema_version") != _ENV_MARKER_SCHEMA_VERSION:
        return False
    if marker.get("lingtai_env_version") != _LINGTAI_ENV_VERSION:
        return False
    if marker.get("os") != current.get("os"):
        return False
    if marker.get("arch") != current.get("arch"):
        return False
    python = marker.get("python") or {}
    current_python = current.get("python") or {}
    for key in (
        "sys_platform",
        "machine",
        "sysconfig_platform",
        "implementation",
        "version_major",
        "version_minor",
    ):
        if python.get(key) != current_python.get(key):
            return False
    return True


def _env_marker_status(venv_dir: Path) -> str:
    status, _detail = _env_marker_status_detail(venv_dir)
    return status


def _env_marker_status_detail(venv_dir: Path) -> tuple[str, str]:
    try:
        raw = _marker_path(venv_dir).read_text(encoding="utf-8")
    except FileNotFoundError:
        return _MARKER_MISSING, ""
    except OSError as exc:
        return _MARKER_ERROR, f"cannot read environment marker: {exc}"
    try:
        marker = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _MARKER_ERROR, f"invalid environment marker JSON: {exc}"
    if marker.get("schema") != _ENV_MARKER_SCHEMA:
        return _MARKER_ERROR, "unsupported environment marker schema"
    if marker.get("schema_version") != _ENV_MARKER_SCHEMA_VERSION:
        return _MARKER_ERROR, "unsupported environment marker schema_version"
    if marker.get("lingtai_env_version") != _LINGTAI_ENV_VERSION:
        return _MARKER_ERROR, "unsupported environment marker version"
    if marker.get("os") != _goos() or marker.get("arch") != _goarch():
        return _MARKER_MISMATCH, "environment marker platform does not match this host"
    try:
        current = _current_venv_env_marker(venv_dir)
    except OSError as exc:
        return _MARKER_ERROR, f"environment marker probe failed: {exc}"
    except subprocess.TimeoutExpired:
        return _MARKER_ERROR, "environment marker probe timed out"
    except RuntimeError as exc:
        return _MARKER_ERROR, f"environment marker probe failed: {exc}"
    except json.JSONDecodeError as exc:
        return _MARKER_ERROR, f"environment marker probe returned invalid JSON: {exc}"
    if _env_marker_matches(marker, current):
        return _MARKER_MATCH, ""
    return _MARKER_MISMATCH, "environment marker Python identity does not match this venv"


def _write_env_marker(venv_dir: Path) -> None:
    marker = _current_venv_env_marker(venv_dir)
    venv_dir.mkdir(parents=True, exist_ok=True)
    _marker_path(venv_dir).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_env_marker_best_effort(venv_dir: Path) -> None:
    try:
        _write_env_marker(venv_dir)
    except (OSError, subprocess.TimeoutExpired, RuntimeError, json.JSONDecodeError):
        return


def _remove_mismatched_managed_venv(venv_dir: Path) -> None:
    if _env_marker_status(venv_dir) == _MARKER_MISMATCH:
        shutil.rmtree(venv_dir)


def _print_env_marker_payload(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True))


def _env_marker_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] != "env-marker":
        print("usage: python -m lingtai.venv_resolve env-marker {check,stamp} --venv PATH", file=sys.stderr)
        return 2
    action = args[1]
    try:
        venv_index = args.index("--venv")
        venv_dir = Path(args[venv_index + 1])
    except (ValueError, IndexError):
        print("missing --venv PATH", file=sys.stderr)
        return 2

    if action == "check":
        status, detail = _env_marker_status_detail(venv_dir)
        payload = {"status": status}
        if detail:
            payload["detail"] = detail
        _print_env_marker_payload(payload)
        return 0
    if action == "stamp":
        try:
            _write_env_marker(venv_dir)
        except (OSError, subprocess.TimeoutExpired, RuntimeError, json.JSONDecodeError) as exc:
            _print_env_marker_payload({
                "status": _MARKER_ERROR,
                "detail": str(exc),
                "stamp_status": "failed",
            })
            return 0
        _print_env_marker_payload({"status": _MARKER_MATCH, "stamp_status": "stamped"})
        return 0

    print(f"unknown env-marker action: {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_env_marker_main())
