#!/usr/bin/env python3
"""Suspend, rename, and resume one POSIX LingTai agent workdir."""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path


HEARTBEAT_MAX_AGE = 5
DEFAULT_TIMEOUT = 30
INCOMPLETE_MARKER = ".change-name-incomplete"
SHEBANG_SAFE_LIMIT = 255
_NAME = re.compile(r"[\w-]{1,64}\Z", re.UNICODE)
_GATE_SCRIPT = """\
import os
import sys
fd = int(sys.argv[1])
token = os.read(fd, 1)
os.close(fd)
if token != b"1":
    raise SystemExit(125)
runtime, root = sys.argv[2], sys.argv[3]
os.execv(runtime, [runtime, "-m", "lingtai", "run", root])
"""


class ChangeNameError(RuntimeError):
    pass


@dataclass(frozen=True)
class FileRewrite:
    relative_path: Path
    before: bytes
    after: bytes
    mode: int
    kind: str


@dataclass(frozen=True)
class InventoryEntry:
    relative_path: Path
    kind: str
    mode: int
    payload: str


@dataclass(frozen=True)
class MarkerProof:
    device: int
    inode: int


@dataclass(frozen=True)
class PendingLaunch:
    child: subprocess.Popen
    gate_fd: int
    log: Path
    started: float


@dataclass(frozen=True)
class Plan:
    old: Path
    new: Path
    agent_id: str
    agent_name: str
    runtime: Path
    resumed_runtime: Path
    expected_import_source: Path
    rewrites: tuple[FileRewrite, ...] = ()
    inventory_venv_relative: Path | None = None
    runtime_inventory: tuple[InventoryEntry, ...] = ()


def _rebase_absolute_path(value: str, old: Path, new: Path) -> str | None:
    """Rebase one canonical absolute path field, never an embedded substring."""
    if not os.path.isabs(value):
        return None
    path = Path(value)
    try:
        relative = path.relative_to(old)
    except ValueError:
        return None
    if ".." in relative.parts or str(path) != value:
        raise ChangeNameError(f"old-root path is not canonical: {value!r}")
    return str(new / relative)


def _strict_json_loads(value: str | bytes, label: str):
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key!r}")
            result[key] = item
        return result

    def reject_constant(constant: str):
        raise ValueError(f"non-standard JSON constant {constant!r}")

    try:
        return json.loads(
            value,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except ValueError as exc:
        raise ChangeNameError(f"malformed {label}: {exc}") from exc


def _clean_python_env() -> dict[str, str]:
    """Use the same explicit import environment for probes and launched Agents."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


def _identity(path: Path) -> tuple[dict, str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        agent_id, agent_name = data["agent_id"], data["agent_name"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ChangeNameError(f"cannot read identity from {path}: {exc}") from exc
    if not isinstance(agent_id, str) or not agent_id or not isinstance(agent_name, str) or not agent_name:
        raise ChangeNameError(".agent.json must contain non-empty agent_id and agent_name")
    return data, agent_id, agent_name


def _probe(runtime: Path, cwd: Path) -> Path:
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ChangeNameError(f"configured venv has no executable Python: {runtime}")
    env = _clean_python_env()
    script = (
        "import json, pathlib, lingtai; "
        "print(json.dumps(str(pathlib.Path(lingtai.__file__).resolve())))"
    )
    try:
        result = subprocess.run(
            [str(runtime), "-c", script], cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", timeout=3, check=True,
        )
        source = json.loads(result.stdout)
        if not isinstance(source, str) or not os.path.isabs(source):
            raise ValueError("lingtai.__file__ was not an absolute path")
        return Path(source)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ChangeNameError(
            f"configured runtime cannot import lingtai without inherited Python paths or writes: {runtime}"
        ) from exc


def _processes(root: Path) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="], capture_output=True, text=True,
            timeout=2, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ChangeNameError(f"process scan failed; refusing to infer absence: {exc}") from exc
    suffix = f" -m lingtai run {root}"
    found = []
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise ChangeNameError("process scan returned an unparseable row")
        if fields[1].endswith(suffix):
            found.append(int(fields[0]))
    return found


def _fresh(root: Path) -> bool:
    try:
        return time.time() - (root / ".agent.heartbeat").stat().st_mtime < HEARTBEAT_MAX_AGE
    except OSError:
        return False


def _try_lock(root: Path):
    try:
        stream = (root / ".agent.lock").open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except OSError:
        try:
            stream.close()
        except UnboundLocalError:
            pass
        return None


def _lock_held(root: Path) -> bool:
    path = root / ".agent.lock"
    if not path.is_file():
        return False
    stream = _try_lock(root)
    if stream is None:
        return True
    stream.close()
    return False


def _snapshot(path: Path, root: Path, kind: str) -> tuple[bytes, int, Path]:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ChangeNameError(f"{kind} must be a regular non-symlink file: {path}")
        relative = path.relative_to(root)
        before = path.read_bytes()
    except ChangeNameError:
        raise
    except (OSError, ValueError) as exc:
        raise ChangeNameError(f"cannot preflight {kind} at {path}: {exc}") from exc
    return before, stat.S_IMODE(info.st_mode), relative


def _rewrite(path: Path, root: Path, kind: str, after: bytes) -> FileRewrite | None:
    before, mode, relative = _snapshot(path, root, kind)
    if before == after:
        return None
    return FileRewrite(relative, before, after, mode, kind)


def _json_bytes(data: dict) -> bytes:
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1:]
    return line, ""


def _site_packages_dirs(venv: Path) -> tuple[Path, ...]:
    lib = venv / "lib"
    if not lib.exists():
        return ()
    if lib.is_symlink() or not lib.is_dir():
        raise ChangeNameError(f"project-local venv lib must be a real directory: {lib}")
    found: list[Path] = []
    try:
        python_dirs = sorted(lib.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ChangeNameError(f"cannot inspect project-local venv library: {exc}") from exc
    for python_dir in python_dirs:
        if not python_dir.name.startswith("python") or python_dir.is_symlink() or not python_dir.is_dir():
            continue
        site_packages = python_dir / "site-packages"
        if site_packages.exists():
            if site_packages.is_symlink() or not site_packages.is_dir():
                raise ChangeNameError(
                    f"project-local venv site-packages must be a real directory: {site_packages}"
                )
            found.append(site_packages)
    return tuple(found)


def _plan_pth_rewrites(
    old: Path, new: Path, site_packages_dirs: tuple[Path, ...]
) -> tuple[list[FileRewrite], list[Path]]:
    rewrites: list[FileRewrite] = []
    rebound_roots: list[Path] = []
    for site_packages in site_packages_dirs:
        for path in sorted(site_packages.glob("*.pth"), key=lambda item: item.name):
            before, mode, relative = _snapshot(path, old, "editable .pth")
            try:
                text = before.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ChangeNameError(f"editable .pth is not UTF-8 text: {path}") from exc
            changed = False
            output: list[str] = []
            for line in text.splitlines(keepends=True):
                value, ending = _split_line_ending(line)
                if not value or value.startswith("#") or value.startswith(("import ", "import\t")):
                    output.append(line)
                    continue
                rebased = _rebase_absolute_path(value, old, new)
                if rebased is None:
                    output.append(line)
                    continue
                rebound_roots.append(Path(value))
                output.append(rebased + ending)
                changed = True
            after = "".join(output).encode("utf-8")
            if changed:
                rewrites.append(FileRewrite(relative, before, after, mode, "editable .pth"))
    return rewrites, rebound_roots


def _plan_direct_url_rewrites(
    old: Path, new: Path, site_packages_dirs: tuple[Path, ...]
) -> list[FileRewrite]:
    rewrites: list[FileRewrite] = []
    for site_packages in site_packages_dirs:
        for dist_info in sorted(site_packages.glob("*.dist-info"), key=lambda item: item.name):
            if dist_info.is_symlink() or not dist_info.is_dir():
                continue
            path = dist_info / "direct_url.json"
            if not path.exists():
                continue
            before, mode, relative = _snapshot(path, old, "direct_url.json")
            data = _strict_json_loads(before, f"direct_url.json at {path}")
            if not isinstance(data, dict) or not isinstance(data.get("url"), str):
                raise ChangeNameError(
                    f"malformed direct_url.json at {path}: expected an object with a string url field"
                )
            parsed = urllib.parse.urlsplit(data["url"])
            if parsed.scheme.lower() != "file" or parsed.netloc.lower() not in ("", "localhost"):
                continue
            try:
                decoded_path = urllib.parse.unquote_to_bytes(parsed.path).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ChangeNameError(f"malformed UTF-8 file URL at {path}") from exc
            rebased = _rebase_absolute_path(decoded_path, old, new)
            if rebased is None:
                continue
            data["url"] = urllib.parse.urlunsplit(
                parsed._replace(path=urllib.parse.quote(rebased, safe="/"))
            )
            rewrites.append(
                FileRewrite(relative, before, _json_bytes(data), mode, "direct_url.json")
            )
    return rewrites


def _plan_shebang_rewrites(old: Path, new: Path, venv: Path) -> list[FileRewrite]:
    bin_dir = venv / "bin"
    if bin_dir.is_symlink() or not bin_dir.is_dir():
        raise ChangeNameError(f"project-local venv bin must be a real directory: {bin_dir}")
    rewrites: list[FileRewrite] = []
    try:
        entries = sorted(bin_dir.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ChangeNameError(f"cannot inspect project-local venv launchers: {exc}") from exc
    for path in entries:
        try:
            info = path.lstat()
        except OSError as exc:
            raise ChangeNameError(f"cannot inspect project-local venv launcher {path}: {exc}") from exc
        if not stat.S_ISREG(info.st_mode) or not (stat.S_IMODE(info.st_mode) & 0o111):
            continue
        try:
            with path.open("rb") as stream:
                first_line = stream.readline(4097)
        except OSError as exc:
            raise ChangeNameError(f"cannot read project-local venv launcher {path}: {exc}") from exc
        if not first_line.startswith(b"#!"):
            continue
        if len(first_line) > 4096:
            raise ChangeNameError(f"project-local venv launcher has an overlong shebang: {path}")
        raw_interpreter = first_line[2:].rstrip(b"\r\n")
        try:
            interpreter = raw_interpreter.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ChangeNameError(f"project-local venv launcher shebang is not UTF-8: {path}") from exc
        rebased = _rebase_absolute_path(interpreter, old, new)
        if rebased is None:
            continue
        if not Path(interpreter).is_file():
            raise ChangeNameError(
                f"old-root launcher shebang is not one exact existing interpreter path: {path}"
            )
        before, mode, relative = _snapshot(path, old, "console-script shebang")
        newline_at = before.find(b"\n")
        suffix = b"" if newline_at < 0 else before[newline_at + 1:]
        ending = b"\n" if first_line.endswith(b"\n") else b""
        if first_line.endswith(b"\r\n"):
            ending = b"\r\n"
        encoded_shebang = b"#!" + rebased.encode("utf-8") + ending
        # Linux's executable-header buffer is 256 bytes and Darwin's supported
        # bound is larger. Requiring at most 255 encoded bytes, including #!
        # and the line ending, stays executable on both supported POSIX kernels.
        if len(encoded_shebang) > SHEBANG_SAFE_LIMIT:
            raise ChangeNameError(
                f"relocated console-script shebang exceeds the {SHEBANG_SAFE_LIMIT}-byte "
                f"portable limit: {path}"
            )
        rewrites.append(
            FileRewrite(relative, before, encoded_shebang + suffix, mode, "console-script shebang")
        )
    return rewrites


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _plan_runtime_rewrites(
    old: Path, new: Path, venv: Path, import_source: Path
) -> list[FileRewrite]:
    try:
        if venv.is_symlink() or not venv.is_dir() or venv.resolve(strict=True) != venv:
            raise ChangeNameError(
                f"project-local venv must be an existing canonical non-symlink directory: {venv}"
            )
    except OSError as exc:
        raise ChangeNameError(f"cannot resolve project-local venv: {exc}") from exc
    site_packages_dirs = _site_packages_dirs(venv)
    pth_rewrites, rebound_roots = _plan_pth_rewrites(old, new, site_packages_dirs)
    direct_url_rewrites = _plan_direct_url_rewrites(old, new, site_packages_dirs)
    shebang_rewrites = _plan_shebang_rewrites(old, new, venv)

    if _path_is_within(import_source, old) and not _path_is_within(import_source, venv):
        if not any(_path_is_within(import_source, root) for root in rebound_roots):
            raise ChangeNameError(
                "project-local venv imports lingtai through an unsupported old-root editable binding; "
                "expected one exact absolute .pth path"
            )
    return [*pth_rewrites, *direct_url_rewrites, *shebang_rewrites]


def _fingerprint(path: Path, root: Path, label: str) -> InventoryEntry:
    try:
        info = path.lstat()
        relative = path.relative_to(root)
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            return InventoryEntry(relative, "regular", mode, f"{info.st_size}:{digest.hexdigest()}")
        if stat.S_ISLNK(info.st_mode):
            return InventoryEntry(relative, "symlink", mode, os.readlink(path))
        if stat.S_ISDIR(info.st_mode):
            return InventoryEntry(relative, "directory", mode, "")
        return InventoryEntry(relative, f"mode-{stat.S_IFMT(info.st_mode):o}", mode, "")
    except (OSError, ValueError) as exc:
        raise ChangeNameError(f"cannot fingerprint {label} at {path}: {exc}") from exc


def _runtime_inventory(root: Path, venv: Path) -> tuple[InventoryEntry, ...]:
    entries: list[InventoryEntry] = []
    bin_dir = venv / "bin"
    try:
        bin_entries = sorted(bin_dir.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise ChangeNameError(f"cannot inventory project-local venv bin: {exc}") from exc
    entries.extend(_fingerprint(path, root, "venv bin entry") for path in bin_entries)
    for site_packages in _site_packages_dirs(venv):
        entries.extend(
            _fingerprint(path, root, "site-packages .pth")
            for path in sorted(site_packages.glob("*.pth"), key=lambda item: item.name)
        )
        for dist_info in sorted(site_packages.glob("*.dist-info"), key=lambda item: item.name):
            entries.append(_fingerprint(dist_info, root, "dist-info entry"))
            direct_url = dist_info / "direct_url.json"
            try:
                direct_url.lstat()
            except FileNotFoundError:
                continue
            entries.append(_fingerprint(direct_url, root, "direct_url.json"))
    return tuple(sorted(entries, key=lambda item: str(item.relative_path)))


def _rebase_init_mcp(
    init: dict, old: Path, new: Path, registry_sources: dict[str, str] | None = None
) -> bool:
    if "mcp" not in init:
        return False
    mcp = init["mcp"]
    if not isinstance(mcp, dict):
        raise ChangeNameError("init.json top-level mcp must be an object")
    sources = registry_sources or {}
    changed = False
    for name, config in mcp.items():
        if not isinstance(config, dict):
            raise ChangeNameError(f"init.json mcp entry {name!r} must be an object")
        # Curated init entries are activation/config overlays. The Agent derives
        # their complete launcher from the current catalog, so legacy launcher
        # fields must remain semantically untouched rather than be rebased.
        if sources.get(name) == "lingtai-curated":
            continue
        transport = config.get("type", "stdio")
        if transport not in ("stdio", "http"):
            raise ChangeNameError(f"init.json mcp entry {name!r} has unsupported type {transport!r}")
        if transport != "stdio" or "command" not in config:
            continue
        command = config["command"]
        if not isinstance(command, str):
            raise ChangeNameError(f"init.json stdio mcp entry {name!r} command must be a string")
        rebased = _rebase_absolute_path(command, old, new)
        if rebased is not None:
            config["command"] = rebased
            changed = True
    return changed


def _plan_registry_rewrite(old: Path, new: Path) -> tuple[FileRewrite | None, dict[str, str]]:
    path = old / "mcp_registry.jsonl"
    try:
        path.lstat()
    except FileNotFoundError:
        return None, {}
    before, mode, relative = _snapshot(path, old, "mcp_registry.jsonl")
    try:
        text = before.decode("utf-8")
        from lingtai.services.mcp_registry import validate_record
    except (UnicodeDecodeError, ImportError) as exc:
        raise ChangeNameError(f"cannot validate mcp_registry.jsonl: {exc}") from exc

    changed = False
    output: list[str] = []
    sources: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        content, ending = _split_line_ending(line)
        if not content.strip():
            output.append(line)
            continue
        try:
            record = _strict_json_loads(content, f"mcp_registry.jsonl line {line_number}")
        except ChangeNameError as exc:
            raise ChangeNameError(str(exc)) from exc
        valid, error = validate_record(record)
        if not valid:
            raise ChangeNameError(
                f"malformed mcp_registry.jsonl line {line_number}: {error or 'unknown error'}"
            )
        name = record["name"]
        if name in sources:
            raise ChangeNameError(f"duplicate mcp_registry.jsonl name on line {line_number}: {name!r}")
        sources[name] = record["source"]
        if record["transport"] == "stdio":
            rebased = _rebase_absolute_path(record["command"], old, new)
            if rebased is not None:
                record["command"] = rebased
                content = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
                changed = True
        output.append(content + ending)
    rewrite = None
    if changed:
        rewrite = FileRewrite(
            relative, before, "".join(output).encode("utf-8"), mode, "mcp_registry.jsonl"
        )
    return rewrite, sources


def _path_shape_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ChangeNameError(f"cannot inspect transaction marker {path}: {exc}") from exc


def _require_absent_marker(path: Path, label: str) -> None:
    if _path_shape_exists(path):
        raise ChangeNameError(f"{label} must be absent before name change: {path}")


def _require_no_replace_support() -> None:
    """Reject unsupported POSIX cells before any Agent lifecycle side effect."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
    except OSError as exc:
        raise ChangeNameError(f"cannot inspect no-replace rename support: {exc}") from exc
    if sys.platform == "darwin":
        symbol = "renamex_np"
    elif sys.platform.startswith("linux"):
        symbol = "renameat2"
    else:
        raise ChangeNameError(f"no supported no-replace rename on {sys.platform}")
    if getattr(libc, symbol, None) is None:
        raise ChangeNameError(f"required no-replace rename primitive {symbol} is unavailable")


def preflight(old_arg: str | Path, new_name: str) -> Plan:
    if os.name != "posix":
        raise ChangeNameError("POSIX only")
    _require_no_replace_support()
    old = Path(old_arg)
    if not old.is_absolute() or old.is_symlink() or not old.is_dir() or old != old.resolve():
        raise ChangeNameError("old workdir must be an existing canonical absolute directory")
    if not _NAME.fullmatch(new_name) or new_name.startswith("."):
        raise ChangeNameError("new name must be one non-dot letters/digits/underscore/hyphen segment (max 64)")
    new = old.parent / new_name
    if new == old or _path_shape_exists(new):
        raise ChangeNameError(f"destination must be absent: {new}")
    _require_absent_marker(old / ".suspend", "suspend marker")
    _require_absent_marker(old / INCOMPLETE_MARKER, "incomplete transaction marker")

    manifest, agent_id, agent_name = _identity(old / ".agent.json")
    if manifest.get("address") != old.name:
        raise ChangeNameError(".agent.json address does not match the old basename")
    init_path = old / "init.json"
    init_before, init_mode, init_relative = _snapshot(init_path, old, "init.json")
    init = _strict_json_loads(init_before, "init.json")
    try:
        if not isinstance(init, dict):
            raise TypeError("top level is not an object")
        venv = init["venv_path"]
        configured_name = init["manifest"]["agent_name"]
    except (KeyError, TypeError) as exc:
        raise ChangeNameError("v1 requires strict JSON init.json with venv_path and manifest.agent_name") from exc
    if not isinstance(venv, str) or not os.path.isabs(venv):
        raise ChangeNameError("v1 requires an absolute init.json venv_path")
    if configured_name != agent_name:
        raise ChangeNameError("init.json manifest.agent_name disagrees with .agent.json")

    runtime = Path(venv) / "bin" / "python"
    import_source = _probe(runtime, old)
    rebased_venv = _rebase_absolute_path(venv, old, new)
    if rebased_venv is None and _path_is_within(import_source, old):
        raise ChangeNameError(
            "external configured venv imports lingtai from the old Agent root; "
            "external environment metadata is outside this migration"
        )
    expected_import_source = import_source
    if _path_is_within(import_source, old):
        expected_import_source = new / import_source.relative_to(old)

    registry_rewrite, registry_sources = _plan_registry_rewrite(old, new)
    rewrites: list[FileRewrite] = []
    inventory_venv_relative = None
    runtime_inventory: tuple[InventoryEntry, ...] = ()
    init_changed = False
    if rebased_venv is not None:
        local_venv = Path(venv)
        rewrites.extend(_plan_runtime_rewrites(old, new, local_venv, import_source))
        inventory_venv_relative = local_venv.relative_to(old)
        runtime_inventory = _runtime_inventory(old, local_venv)
        init["venv_path"] = rebased_venv
        init_changed = True
    else:
        rebased_venv = venv
    init_changed = _rebase_init_mcp(init, old, new, registry_sources) or init_changed
    if registry_rewrite is not None:
        rewrites.append(registry_rewrite)
    if init_changed:
        rewrites.append(
            FileRewrite(init_relative, init_before, _json_bytes(init), init_mode, "init.json")
        )

    if not _fresh(old) or len(_processes(old)) != 1 or not _lock_held(old):
        raise ChangeNameError("target is not one live agent with a fresh heartbeat and held lease")
    return Plan(
        old, new, agent_id, agent_name, runtime,
        Path(rebased_venv) / "bin" / "python", expected_import_source,
        tuple(rewrites), inventory_venv_relative, runtime_inventory,
    )


def _wait_stopped(plan: Plan, timeout: float):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes = _processes(plan.old)
        lock = _try_lock(plan.old)
        if not processes and not _fresh(plan.old) and lock is not None:
            return lock
        if lock is not None:
            lock.close()
        time.sleep(0.1)
    raise ChangeNameError("suspend did not release process, heartbeat, and lease before timeout")


def _rename_no_replace(old: Path, new: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renamex_np unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(old), os.fsencode(new), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renameat2 unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(old), -100, os.fsencode(new), 1)  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, f"no no-replace rename on {sys.platform}")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(new))


def _fsync_parent(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".change-name-", dir=path.parent)
    open_fd = True
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            open_fd = False
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = ""
        _fsync_parent(path)
    except BaseException:
        if open_fd:
            try:
                os.close(fd)
            except OSError:
                pass
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        raise


def _create_transaction_marker(path: Path, content: bytes) -> MarkerProof:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise ChangeNameError("supported POSIX marker creation requires O_NOFOLLOW")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | no_follow
    fd = None
    proof = None
    try:
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise ChangeNameError(f"transaction marker appeared concurrently: {path}") from exc
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        proof = MarkerProof(info.st_dev, info.st_ino)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ChangeNameError(f"transaction marker is not a regular mode-0600 file: {path}")
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short transaction-marker write")
            view = view[written:]
        os.fsync(fd)
        os.close(fd)
        fd = None
        current = path.lstat()
        if (
            not stat.S_ISREG(current.st_mode)
            or stat.S_IMODE(current.st_mode) != 0o600
            or (current.st_dev, current.st_ino) != (proof.device, proof.inode)
        ):
            raise ChangeNameError(f"transaction marker changed during creation: {path}")
        _fsync_parent(path)
        return proof
    except BaseException as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if proof is not None:
            try:
                _remove_proven_marker(path, proof)
            except BaseException:
                pass
        if isinstance(exc, (ChangeNameError, OSError)):
            raise
        raise ChangeNameError(f"cannot create durable transaction marker {path}: {exc}") from exc


def _remove_proven_marker(path: Path, proof: MarkerProof) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ChangeNameError(f"cannot verify helper-created marker before removal {path}: {exc}") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or (info.st_dev, info.st_ino) != (proof.device, proof.inode)
    ):
        raise ChangeNameError(f"refusing to remove replaced transaction marker: {path}")
    path.unlink()
    _fsync_parent(path)


def _ensure_incomplete_fence(path: Path) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        _create_transaction_marker(path, b"change-name transaction incomplete\n")
        return
    except OSError as exc:
        raise ChangeNameError(f"cannot verify incomplete transaction fence {path}: {exc}") from exc
    # Any existing shape is fail-closed to the CLI gate. Durably record its
    # directory entry without replacing or consuming someone else's object.
    _fsync_parent(path)


def _rewrite_still_matches(root: Path, rewrite: FileRewrite) -> bool:
    current, mode, relative = _snapshot(root / rewrite.relative_path, root, rewrite.kind)
    return relative == rewrite.relative_path and current == rewrite.before and mode == rewrite.mode


def _revalidate_cutover(plan: Plan) -> None:
    if _path_shape_exists(plan.new):
        raise ChangeNameError(f"destination appeared before cutover: {plan.new}")
    manifest, agent_id, agent_name = _identity(plan.old / ".agent.json")
    if (
        agent_id,
        agent_name,
        manifest.get("address"),
    ) != (plan.agent_id, plan.agent_name, plan.old.name):
        raise ChangeNameError("Agent identity or address changed before cutover")
    for rewrite in plan.rewrites:
        if not _rewrite_still_matches(plan.old, rewrite):
            raise ChangeNameError(
                f"{rewrite.kind} changed before cutover: {rewrite.relative_path}"
            )
    if plan.inventory_venv_relative is not None:
        current = _runtime_inventory(plan.old, plan.old / plan.inventory_venv_relative)
        if current != plan.runtime_inventory:
            raise ChangeNameError(
                "project-local venv binding inventory changed before cutover"
            )


def _apply_rewrites(plan: Plan) -> None:
    completed = 0
    for rewrite in plan.rewrites:
        path = plan.new / rewrite.relative_path
        try:
            if not _rewrite_still_matches(plan.new, rewrite):
                raise ChangeNameError(
                    f"{rewrite.kind} changed after preflight: {rewrite.relative_path}"
                )
            _write_atomic(path, rewrite.after, rewrite.mode)
            completed += 1
        except (ChangeNameError, OSError) as exc:
            raise ChangeNameError(
                f"target relocation stopped after {completed}/{len(plan.rewrites)} writes at "
                f"{rewrite.relative_path}: {exc}"
            ) from exc


def _write_json(path: Path, data: dict) -> None:
    _, mode, _ = _snapshot(path, path.parent, path.name)
    _write_atomic(path, _json_bytes(data), mode)


def _remove_suspend(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise ChangeNameError(f"target suspend marker is not a regular file: {path}")
    path.unlink()
    _fsync_parent(path)


def _close_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError as exc:
        if exc.errno != errno.EBADF:
            raise


def _abort_pending_launch(launch: PendingLaunch) -> None:
    """Close and reap one helper-owned child before any gate commit."""
    _close_fd(launch.gate_fd)
    try:
        launch.child.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        launch.child.terminate()
    try:
        launch.child.wait(timeout=3)
    except subprocess.TimeoutExpired as exc:
        raise ChangeNameError(
            f"uncommitted launch gate did not exit after termination; inspect {launch.log}"
        ) from exc


def _recover_uncommitted_launch(
    plan: Plan, launch: PendingLaunch, error: BaseException
) -> None:
    """Restore the target fence and reap a child whose gate was not committed."""
    errors: list[str] = []
    try:
        _ensure_incomplete_fence(plan.new / INCOMPLETE_MARKER)
    except BaseException as exc:
        errors.append(f"transaction-fence restore failed: {exc}")
    try:
        _abort_pending_launch(launch)
    except BaseException as exc:
        errors.append(f"uncommitted child cleanup failed: {exc}")
    if errors:
        raise ChangeNameError(f"{error}; {'; '.join(errors)}") from error


def _prepare_launch(plan: Plan, fence: MarkerProof) -> PendingLaunch:
    _apply_rewrites(plan)
    observed = _probe(plan.resumed_runtime, plan.new)
    if observed != plan.expected_import_source:
        raise ChangeNameError(
            "relocated runtime imported lingtai from an unexpected origin: "
            f"expected {plan.expected_import_source}, observed {observed}"
        )
    manifest, agent_id, agent_name = _identity(plan.new / ".agent.json")
    if (agent_id, agent_name) != (plan.agent_id, plan.agent_name):
        raise ChangeNameError("identity changed during suspend; target directory retained")
    manifest["address"] = plan.new.name
    _write_json(plan.new / ".agent.json", manifest)
    _remove_suspend(plan.new / ".suspend")

    log = plan.new / "logs" / "change-name.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    _fsync_parent(log)
    read_fd, write_fd = os.pipe()
    child = None
    started = time.time()
    try:
        with log.open("a", encoding="utf-8") as stream:
            child = subprocess.Popen(
                [
                    str(plan.resumed_runtime), "-c", _GATE_SCRIPT, str(read_fd),
                    str(plan.resumed_runtime), str(plan.new),
                ],
                cwd=plan.new, env=_clean_python_env(),
                stdin=subprocess.DEVNULL, stdout=stream,
                stderr=subprocess.STDOUT, start_new_session=True, pass_fds=(read_fd,),
            )
        _close_fd(read_fd)
        read_fd = -1
        if child.poll() is not None:
            raise ChangeNameError(f"launch gate exited before handoff; inspect {log}")
        pending = PendingLaunch(child, write_fd, log, started)
        # Keep the durable launch fence present through successful child setup.
        # The child cannot enter cli.run until the lease is subsequently closed
        # and the parent sends one commit byte.
        _remove_proven_marker(plan.new / INCOMPLETE_MARKER, fence)
        return pending
    except BaseException as exc:
        cleanup_errors = []
        if read_fd >= 0:
            try:
                _close_fd(read_fd)
            except BaseException as cleanup_exc:
                cleanup_errors.append(f"gate read close failed: {cleanup_exc}")
        try:
            if child is None:
                _close_fd(write_fd)
            else:
                _abort_pending_launch(PendingLaunch(child, write_fd, log, started))
        except BaseException as cleanup_exc:
            cleanup_errors.append(f"uncommitted child cleanup failed: {cleanup_exc}")
        try:
            _ensure_incomplete_fence(plan.new / INCOMPLETE_MARKER)
        except BaseException as cleanup_exc:
            cleanup_errors.append(f"transaction-fence restore failed: {cleanup_exc}")
        if cleanup_errors:
            raise ChangeNameError(f"{exc}; {'; '.join(cleanup_errors)}") from exc
        raise


def _commit_launch(launch: PendingLaunch) -> None:
    committed = False
    try:
        if os.write(launch.gate_fd, b"1") != 1:
            raise OSError("short launch-gate write")
        committed = True
    finally:
        try:
            _close_fd(launch.gate_fd)
        except OSError:
            if not committed:
                raise


def _wait_resumed(plan: Plan, launch: PendingLaunch, timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if launch.child.poll() is not None:
            raise ChangeNameError(
                f"resumed agent exited ({launch.child.returncode}); inspect {launch.log}"
            )
        try:
            manifest, agent_id, agent_name = _identity(plan.new / ".agent.json")
            heartbeat_after_launch = (
                plan.new / ".agent.heartbeat"
            ).stat().st_mtime >= launch.started
        except OSError:
            heartbeat_after_launch = False
        if (
            launch.child.pid in _processes(plan.new)
            and _fresh(plan.new)
            and heartbeat_after_launch
            and (agent_id, agent_name, manifest.get("address"))
            == (plan.agent_id, plan.agent_name, plan.new.name)
        ):
            return launch.child.pid
        time.sleep(0.1)
    raise ChangeNameError(
        f"resumed agent did not prove liveness and identity; inspect {launch.log}"
    )


def supervise(old: str | Path, new_name: str, timeout: float) -> int:
    plan = None
    lease = None
    fence = None
    renamed = False
    try:
        plan = preflight(old, new_name)
        _create_transaction_marker(plan.old / ".suspend", b"change-name suspension request\n")
        lease = _wait_stopped(plan, timeout)
        fence = _create_transaction_marker(
            plan.old / INCOMPLETE_MARKER, b"change-name transaction incomplete\n"
        )
        _revalidate_cutover(plan)
        _rename_no_replace(plan.old, plan.new)
        renamed = True
        # The no-replace publication is made durable before any target write.
        _fsync_parent(plan.new)
        launch = _prepare_launch(plan, fence)
        try:
            lease.close()
        except BaseException as exc:
            lease = None
            _recover_uncommitted_launch(plan, launch, exc)
            raise
        lease = None
        try:
            _commit_launch(launch)
        except BaseException as exc:
            _recover_uncommitted_launch(plan, launch, exc)
            raise
        pid = _wait_resumed(plan, launch, timeout)
        print(f"name change complete: {plan.old} -> {plan.new}; resumed pid {pid}")
        return 0
    except BaseException as exc:
        error = exc
        if plan is not None:
            try:
                if renamed:
                    _ensure_incomplete_fence(plan.new / INCOMPLETE_MARKER)
                elif fence is not None:
                    _remove_proven_marker(plan.old / INCOMPLETE_MARKER, fence)
            except BaseException as fence_exc:
                error = ChangeNameError(f"{exc}; transaction-fence maintenance also failed: {fence_exc}")
        state = (
            "target directory is authoritative, retained, and fenced as incomplete; "
            "inspect it before any separately authorized recovery"
            if renamed else
            "old directory remains authoritative; inspect the possibly suspended source before any "
            "separately authorized recovery"
        )
        print(f"name change failed: {error}\n{state}", file=sys.stderr)
        return 1
    finally:
        if lease is not None:
            lease.close()


def handoff(old: str, new_name: str, timeout: float) -> int:
    try:
        plan = preflight(old, new_name)
        log = plan.old / "logs" / "change-name.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as stream:
            child = subprocess.Popen(
                [str(plan.runtime), str(Path(__file__).resolve()), "--_supervise", str(plan.old), new_name, "--timeout", str(timeout)],
                cwd=plan.old, env=_clean_python_env(),
                stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        print(f"rename supervisor started ({child.pid}); log moves to {plan.new / 'logs' / 'change-name.log'}")
        return 0
    except (ChangeNameError, OSError) as exc:
        print(f"name change did not start: {exc}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_dir")
    parser.add_argument("new_basename")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--foreground", action="store_true")
    parser.add_argument("--_supervise", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.foreground or args._supervise:
        return supervise(args.old_dir, args.new_basename, args.timeout)
    return handoff(args.old_dir, args.new_basename, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
