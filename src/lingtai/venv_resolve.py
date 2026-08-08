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
from dataclasses import dataclass
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
    # Select before filesystem mutation so an unsupported target or exhausted PATH
    # leaves no misleading managed-runtime directory behind.
    python = _find_python()

    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    print(f"Creating lingtai runtime at {venv_dir} ...", file=sys.stderr)

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


_PYTHON_SELECTOR_PROBE = (
    "import json, platform, sys; "
    "print(json.dumps({'version': list(sys.version_info[:3]), "
    "'sys_platform': sys.platform, 'machine': platform.machine(), "
    "'macos_version': platform.mac_ver()[0] if sys.platform == 'darwin' else ''}))"
)


@dataclass(frozen=True)
class _PythonSelectionPolicy:
    target_os: str
    architecture: str
    macos_version: str | None
    macos_major: int | None
    minimum: tuple[int, int]
    maximum: tuple[int, int] | None
    candidate_names: tuple[str, ...]

    @property
    def supported_range(self) -> str:
        if self.maximum is None:
            return "3.11 or newer"
        return f"3.11-{self.maximum[0]}.{self.maximum[1]}"


def _normalize_selector_architecture(machine: str) -> str:
    """Normalize only architecture aliases relevant to macOS wheel selection."""
    value = machine.strip().lower()
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64"}:
        return "x86_64"
    return value


def _parse_macos_major(version: str, *, child: bool = False) -> int:
    label = "child macOS version" if child else "macOS version"
    if not version.strip():
        raise ValueError(f"{label} is missing")
    major_text = version.split(".", 1)[0]
    try:
        major = int(major_text)
    except ValueError as exc:
        raise ValueError(f"Cannot parse {label} {version!r}") from exc
    if major_text != str(major) or major < 0:
        raise ValueError(f"Cannot parse {label} {version!r}")
    return major


def _python_selection_policy() -> _PythonSelectionPolicy:
    """Return the interpreter policy for the current process target."""
    target_os = sys.platform
    architecture = _normalize_selector_architecture(platform.machine())
    minimum = (3, 11)
    generic_names = (
        "python3",
        "python",
        "python3.14",
        "python3.13",
        "python3.12",
        "python3.11",
    )
    if target_os != "darwin":
        return _PythonSelectionPolicy(
            target_os=target_os,
            architecture=architecture,
            macos_version=None,
            macos_major=None,
            minimum=minimum,
            maximum=None,
            candidate_names=generic_names,
        )

    macos_version = platform.mac_ver()[0]
    try:
        macos_major = _parse_macos_major(macos_version)
    except ValueError as exc:
        raise RuntimeError(
            f"Cannot select a managed Python on darwin/{architecture}: {exc}. "
            "Use a supported macOS host or configure a prebuilt compatible venv_path."
        ) from exc
    if macos_major < 13:
        raise RuntimeError(
            f"Cannot select a managed Python on darwin/{architecture}, macOS "
            f"{macos_version}: macOS 13 or newer is required by the confirmed "
            "onnxruntime wheels. Upgrade macOS or configure a prebuilt compatible "
            "venv_path."
        )
    if architecture not in {"arm64", "x86_64"}:
        raise RuntimeError(
            f"Unsupported macOS architecture {architecture!r} on macOS "
            f"{macos_version}; supported process architectures are arm64 and "
            "x86_64. Use a compatible interpreter process or configure a prebuilt "
            "compatible venv_path."
        )

    if architecture == "arm64" and macos_major >= 14:
        maximum = (3, 14)
        candidate_names = (
            "python3.14",
            "python3.13",
            "python3.12",
            "python3.11",
            "python3",
            "python",
        )
    else:
        maximum = (3, 13)
        candidate_names = (
            "python3.13",
            "python3.12",
            "python3.11",
            "python3",
            "python",
        )
    return _PythonSelectionPolicy(
        target_os=target_os,
        architecture=architecture,
        macos_version=macos_version,
        macos_major=macos_major,
        minimum=minimum,
        maximum=maximum,
        candidate_names=candidate_names,
    )


def _probe_python_candidate(
    path: str,
    policy: _PythonSelectionPolicy,
) -> tuple[bool, str]:
    """Probe one executable and return compatibility plus a rejection reason."""
    try:
        result = subprocess.run(
            [path, "-c", _PYTHON_SELECTOR_PROBE],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return False, "probe timed out after 5 seconds"
    except OSError as exc:
        return False, f"probe launch failed: {exc}"

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        return False, f"probe exited with status {result.returncode}{suffix}"
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        return False, f"malformed JSON: {exc}"
    if not isinstance(payload, dict):
        return False, "malformed JSON payload: expected an object"

    version = payload.get("version")
    child_platform = payload.get("sys_platform")
    child_machine = payload.get("machine")
    child_macos_version = payload.get("macos_version")
    if (
        not isinstance(version, list)
        or len(version) != 3
        or any(not isinstance(value, int) or isinstance(value, bool) for value in version)
        or not isinstance(child_platform, str)
        or not isinstance(child_machine, str)
        or not isinstance(child_macos_version, str)
    ):
        return False, "malformed JSON payload: invalid interpreter identity fields"

    python_version = (version[0], version[1])
    if python_version < policy.minimum or (
        policy.maximum is not None and python_version > policy.maximum
    ):
        return (
            False,
            f"Python {python_version[0]}.{python_version[1]} is outside supported "
            f"range {policy.supported_range}",
        )

    if policy.target_os == "darwin":
        if child_platform != "darwin":
            return False, f"child platform {child_platform!r} does not match darwin"
        child_architecture = _normalize_selector_architecture(child_machine)
        if child_architecture != policy.architecture:
            return (
                False,
                f"child architecture {child_architecture!r} does not match "
                f"target {policy.architecture!r}",
            )
        try:
            child_macos_major = _parse_macos_major(child_macos_version, child=True)
        except ValueError as exc:
            return False, str(exc)
        if policy.architecture == "arm64" and policy.macos_major == 13:
            matching_macos_class = child_macos_major == 13
        else:
            matching_macos_class = child_macos_major >= 14 if (
                policy.architecture == "arm64"
            ) else child_macos_major >= 13
        if not matching_macos_class:
            return (
                False,
                f"child macOS {child_macos_version} is outside target class for "
                f"macOS {policy.macos_version}",
            )
    elif policy.target_os == "win32":
        if child_platform != "win32":
            return False, f"child platform {child_platform!r} does not match win32"
    elif policy.target_os.startswith("linux"):
        if not child_platform.startswith("linux"):
            return False, f"child platform {child_platform!r} does not match linux"
    elif child_platform != policy.target_os:
        return (
            False,
            f"child platform {child_platform!r} does not match {policy.target_os!r}",
        )

    return True, ""


def _selection_remedy(policy: _PythonSelectionPolicy) -> str:
    if policy.maximum is None:
        return (
            "Install Python 3.11 or newer, put python3 or a versioned executable "
            "on PATH, or configure a prebuilt compatible venv_path."
        )
    oldest = f"{policy.minimum[0]}.{policy.minimum[1]}"
    newest = f"{policy.maximum[0]}.{policy.maximum[1]}"
    return (
        f"Install {policy.architecture} Python {oldest}-{newest}, put its versioned "
        "executable on PATH, or configure a prebuilt compatible venv_path."
    )


def _find_python() -> str:
    """Return the first interpreter compatible with the current target policy."""
    policy = _python_selection_policy()
    rejected: list[str] = []
    seen_paths: set[str] = set()
    for name in policy.candidate_names:
        path = shutil.which(name)
        if not path:
            continue
        try:
            resolved_path = str(Path(path).resolve(strict=False))
        except OSError:
            resolved_path = os.path.realpath(path)
        if resolved_path in seen_paths:
            continue
        seen_paths.add(resolved_path)
        accepted, reason = _probe_python_candidate(path, policy)
        if accepted:
            return path
        rejected.append(f"{path}: {reason}")

    target = f"{policy.target_os}, architecture {policy.architecture}"
    if policy.macos_version is not None:
        target += f", macOS {policy.macos_version}"
    rejection_detail = "; ".join(rejected) if rejected else "none found on PATH"
    raise RuntimeError(
        f"No compatible Python interpreter found for target {target}. "
        f"Supported Python range: {policy.supported_range}. "
        f"Searched executable names: {', '.join(policy.candidate_names)}. "
        f"Candidate rejections: {rejection_detail}. Remedy: "
        f"{_selection_remedy(policy)}"
    )


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
