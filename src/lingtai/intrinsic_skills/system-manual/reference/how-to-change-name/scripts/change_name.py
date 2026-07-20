#!/usr/bin/env python3
"""Safely change a POSIX LingTai agent workdir basename and resume it.

The normal command is only a hand-off: it performs all read-only checks, then
starts an external, session-detached supervisor.  The supervisor owns the
cooperative shutdown, atomic rename, path rebase, and relaunch.  ``--foreground``
is intended for an already external operator and waits for the final result.
This file intentionally uses only the POSIX filesystem/process primitives; it
never calls ``resolve_venv`` and never edits contacts, ledgers, or history.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


HEARTBEAT_MAX_AGE = 2.0
DEFAULT_TIMEOUT = 30.0
IMPORT_TIMEOUT = 3.0
RECEIPT_NAME = "name-change.json"
SUPERVISOR_LOG_NAME = "name-change-supervisor.log"
RELAUNCH_LOG_NAME = "name-change-relaunch.log"
MARKERS = (".suspend", ".sleep", ".interrupt", ".refresh")

# This is the avatar basename policy, kept local so the helper does not import
# the framework (which could resolve configuration or create a virtualenv).
_NAME_RE = re.compile(r"^[\w-]+$", re.UNICODE)
_NAME_MAX = 64


class ChangeNameError(RuntimeError):
    """A planned, operator-actionable failure."""

    def __init__(self, message: str, phase: str = "preflight", *,
                 pid: int | None = None, log: Path | None = None) -> None:
        super().__init__(message)
        self.phase = phase
        self.pid = pid
        self.log = log


@dataclass(frozen=True)
class Identity:
    agent_id: str
    agent_name: str
    address: str


@dataclass(frozen=True)
class RuntimeChoice:
    executable: Path
    rebased_executable: Path
    source: str


@dataclass(frozen=True)
class Preflight:
    old: Path
    new: Path
    identity: Identity
    init_text: str
    runtime: RuntimeChoice
    pid: int


def parse_jsonc(text: str) -> Any:
    """Parse the accepted JSONC subset without touching the source text."""
    out: list[str] = []
    quoted = False
    escaped = False
    comment = False
    i = 0
    while i < len(text):
        ch = text[i]
        if comment:
            if ch == "\n":
                comment = False
                out.append(ch)
            i += 1
            continue
        if quoted:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quoted = False
            i += 1
            continue
        if ch == '"':
            quoted = True
            out.append(ch)
            i += 1
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            comment = True
            i += 2
        else:
            out.append(ch)
            i += 1
    return json.loads(re.sub(r",\s*([}\]])", r"\1", "".join(out)))


def _json_string_spans(text: str) -> Iterable[tuple[int, int, str]]:
    """Yield (start, end, decoded value) for JSON strings, not comment text."""
    i = 0
    n = len(text)
    in_comment = False
    while i < n:
        if in_comment:
            if text[i] == "\n":
                in_comment = False
            i += 1
            continue
        if text[i] == "/" and i + 1 < n and text[i + 1] == "/":
            in_comment = True
            i += 2
            continue
        if text[i] != '"':
            i += 1
            continue
        start = i
        i += 1
        escaped = False
        while i < n:
            ch = text[i]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                i += 1
                token = text[start:i]
                try:
                    yield start, i, json.loads(token)
                except json.JSONDecodeError:
                    pass
                break
            i += 1
        else:
            break


def _under(path_value: str, old: Path) -> bool:
    old_s = str(old)
    return path_value == old_s or path_value.startswith(old_s + os.sep)


def rebase_jsonc_paths(text: str, old: Path, new: Path) -> str:
    """Rebase only quoted absolute strings under *old*; preserve JSONC text.

    Comments, whitespace, commas, relative paths, external paths, and unrelated
    string tokens are retained byte-for-byte.  Replacement is lexical by design:
    symlink resolution is not allowed to redirect an operator's configured path.
    """
    old = Path(old)
    new = Path(new)
    replacements: list[tuple[int, int, str]] = []
    for start, end, value in _json_string_spans(text):
        if not isinstance(value, str) or not os.path.isabs(value) or not _under(value, old):
            continue
        suffix = value[len(str(old)):]
        replacement = str(new) + suffix
        replacements.append((start, end, json.dumps(replacement, ensure_ascii=False)))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    # Fail closed: the exact accepted parser must understand the result before
    # a lifecycle marker or rename is allowed.
    parse_jsonc(text)
    return text


def _atomic_write(path: Path, text: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if mode is None:
        try:
            mode = path.stat().st_mode & 0o777
        except FileNotFoundError:
            mode = 0o600
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def _receipt_path(root: Path) -> Path:
    return root / "logs" / RECEIPT_NAME


def write_receipt(root: Path, *, status: str, phase: str, old: Path, new: Path,
                  pid: int | None = None, log: Path | None = None,
                  error: str | None = None) -> Path:
    payload: dict[str, Any] = {
        "status": status,
        "phase": phase,
        "old": str(old),
        "new": str(new),
        "pid": pid,
        "log": str(log) if log else None,
        "error": error,
        "written_at": time.time(),
    }
    path = _receipt_path(root)
    _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _read_manifest(old: Path) -> Identity:
    try:
        data = json.loads((old / ".agent.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChangeNameError(f"cannot read valid .agent.json: {exc}") from exc
    if not isinstance(data, dict):
        raise ChangeNameError(".agent.json must contain an object")
    values = (data.get("agent_id"), data.get("agent_name"), data.get("address"))
    if not all(isinstance(value, str) and value for value in values):
        raise ChangeNameError(".agent.json must contain non-empty agent_id, agent_name, and address")
    if data["address"] != old.name:
        raise ChangeNameError(
            f"manifest address {data['address']!r} does not match old basename {old.name!r}"
        )
    return Identity(data["agent_id"], data["agent_name"], data["address"])


def _heartbeat(root: Path) -> float | None:
    try:
        return float((root / ".agent.heartbeat").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def heartbeat_fresh(root: Path, now: float | None = None) -> bool:
    value = _heartbeat(root)
    return value is not None and (time.time() if now is None else now) - value < HEARTBEAT_MAX_AGE


def _processes() -> list[tuple[int, str]]:
    try:
        result = subprocess.run(
            ["ps", "-ax", "-o", "pid=,command="],
            check=True, capture_output=True, text=True, timeout=2,
        )
    except subprocess.TimeoutExpired as exc:
        raise ChangeNameError("process scan failed: ps timed out", phase="process-scan") from exc
    except subprocess.CalledProcessError as exc:
        raise ChangeNameError(
            f"process scan failed: ps exited with status {exc.returncode}", phase="process-scan"
        ) from exc
    except OSError as exc:
        raise ChangeNameError(f"process scan failed: cannot execute ps: {exc}", phase="process-scan") from exc
    except subprocess.SubprocessError as exc:
        raise ChangeNameError(f"process scan failed: {exc}", phase="process-scan") from exc
    found: list[tuple[int, str]] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(.*)", line)
        if not match:
            raise ChangeNameError("process scan failed: ps returned an unparseable row", phase="process-scan")
        found.append((int(match.group(1)), match.group(2)))
    return found


def _is_exact_run(command: str, root: Path) -> bool:
    target = os.path.normpath(str(root))
    # Keep the repository's conservative matcher semantics: module launch is
    # exact and accepts any interpreter path, while console forms are anchored.
    for token, anchored in ((" -m lingtai run ", False), ("lingtai-agent run ", True), ("lingtai run ", True)):
        at = command.find(token)
        while at >= 0:
            if not anchored or at == 0 or command[at - 1] in "/\\":
                tail = command[at + len(token):].strip()
                if tail and os.path.normpath(tail) == target:
                    return True
            at = command.find(token, at + 1)
    return False


def exact_processes(root: Path) -> list[tuple[int, str]]:
    return [(pid, cmd) for pid, cmd in _processes() if _is_exact_run(cmd, root)]


def _lock_is_held(root: Path) -> bool:
    lock_path = root / ".agent.lock"
    if not lock_path.is_file():
        return False
    try:
        with lock_path.open("r+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(stream, fcntl.LOCK_UN)
            return False
    except BlockingIOError:
        return True
    except OSError:
        return False


def _acquire_lock(root: Path) -> Any:
    path = root / ".agent.lock"
    stream = path.open("a+")
    try:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        stream.close()
        raise
    return stream


def _valid_name(name: str) -> bool:
    return (
        isinstance(name, str) and bool(name) and name not in (".", "..")
        and not name.startswith(".") and len(name) <= _NAME_MAX and bool(_NAME_RE.fullmatch(name))
    )


def _runtime_inside(path: Path, root: Path) -> bool:
    return _under(str(path), root)


def _candidate_path(raw: str, old: Path) -> Path:
    value = Path(raw).expanduser()
    return value if value.is_absolute() else old / value


def _usable_runtime(path: Path, *, cwd: Path) -> bool:
    if not path.is_file() or not os.access(path, os.X_OK):
        return False
    env = os.environ.copy()
    # The preflight is advertised as read-only, including for an in-workdir
    # runtime/package tree.  Keep the ambient environment otherwise unchanged.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        subprocess.run(
            [str(path), "-c", "import lingtai"], cwd=str(cwd), env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=IMPORT_TIMEOUT, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def select_runtime(init_data: dict[str, Any], old: Path) -> RuntimeChoice:
    """Select an importable interpreter, without resolving/creating a venv."""
    candidates: list[tuple[str, Path]] = []
    configured = init_data.get("venv_path")
    if configured is not None:
        if not isinstance(configured, str) or not configured:
            raise ChangeNameError("init.json venv_path must be a non-empty string")
        configured_path = _candidate_path(configured, old)
        # cli.run's configured value is normally a venv directory. Accept an
        # explicit executable too, but never create or repair either shape.
        executable = configured_path / "bin" / "python" if configured_path.is_dir() else configured_path
        candidates.append(("init.json venv_path", executable))
    inherited = os.environ.get("LINGTAI_RUNTIME_PYTHON")
    if inherited:
        candidates.append(("LINGTAI_RUNTIME_PYTHON", Path(inherited).expanduser()))
    candidates.append(("current interpreter", Path(sys.executable)))
    candidates.append(("managed tui runtime", Path.home() / ".lingtai-tui/runtime/venv/bin/python"))
    seen: set[str] = set()
    for source, candidate in candidates:
        candidate = candidate.absolute()
        if str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if _usable_runtime(candidate, cwd=old):
            return RuntimeChoice(candidate, candidate, source)
        # cli.run treats an explicit configured venv as authoritative and may
        # otherwise repair/create it. Refuse that mutation rather than silently
        # selecting a fallback that cannot resume the same init.json.
        if source == "init.json venv_path":
            raise ChangeNameError(
                f"configured init.json venv_path is not usable: {candidate}; refusing fallback or venv mutation"
            )
    raise ChangeNameError("no candidate runtime can execute `import lingtai` without mutating configuration")


def _rebased_runtime(choice: RuntimeChoice, old: Path, new: Path) -> RuntimeChoice:
    if _runtime_inside(choice.executable, old):
        suffix = str(choice.executable)[len(str(old)):]
        return RuntimeChoice(choice.executable, Path(str(new) + suffix), choice.source)
    return RuntimeChoice(choice.executable, choice.executable, choice.source)


def preflight(old_arg: str | Path, new_name: str) -> Preflight:
    if os.name != "posix":
        raise ChangeNameError("this first version supports POSIX only")
    old = Path(old_arg)
    if not old.is_absolute():
        raise ChangeNameError("old agent directory must be an absolute path")
    if old.is_symlink() or not old.is_dir():
        raise ChangeNameError("old agent directory must be an existing, non-symlink directory")
    canonical = old.resolve()
    if old != canonical:
        raise ChangeNameError(
            f"old agent directory must be its canonical path (no '..' or symlinked components); use {canonical}"
        )
    old = canonical
    if not _valid_name(new_name):
        raise ChangeNameError("new basename must be one safe, non-dot path segment (letters, digits, underscore, hyphen; max 64)")
    new = old.parent / new_name
    if new == old:
        raise ChangeNameError("new basename is unchanged")
    if new.exists() or new.is_symlink():
        raise ChangeNameError(f"destination already exists: {new}")
    if old.parent != new.parent:
        raise ChangeNameError("old and new directories must have the same parent")
    identity = _read_manifest(old)
    init_path = old / "init.json"
    try:
        init_text = init_path.read_text(encoding="utf-8")
        init_data = parse_jsonc(init_text)
    except (OSError, ValueError) as exc:
        raise ChangeNameError(f"cannot read valid init.json/JSONC: {exc}") from exc
    if not isinstance(init_data, dict):
        raise ChangeNameError("init.json must contain an object")
    rebased_init = rebase_jsonc_paths(init_text, old, new)
    if not heartbeat_fresh(old):
        raise ChangeNameError("agent heartbeat is absent or stale")
    procs = exact_processes(old)
    if len(procs) != 1:
        raise ChangeNameError(f"expected exactly one exact agent process, found {len(procs)}")
    if not _lock_is_held(old):
        raise ChangeNameError(".agent.lock is not held by the running agent")
    runtime = _rebased_runtime(select_runtime(init_data, old), old, new)
    return Preflight(old, new, identity, rebased_init, runtime, procs[0][0])


def _wait_shutdown(old: Path, expected_pid: int, timeout: float, poll: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            current = exact_processes(old)
        except ChangeNameError as exc:
            raise ChangeNameError(
                f"cannot prove cooperative shutdown because process scan failed: {exc}",
                phase="shutdown",
            ) from exc
        gone = all(pid != expected_pid for pid, _ in current) and not current
        stale = not heartbeat_fresh(old)
        try:
            lease = _acquire_lock(old)
        except (OSError, BlockingIOError):
            lease = None
        if gone and stale and lease is not None:
            return lease
        if lease is not None:
            lease.close()
        time.sleep(poll)
    raise ChangeNameError(
        "agent did not finish cooperative shutdown before timeout; inspect .suspend and retry",
        phase="shutdown",
    )


def _update_manifest(path: Path, identity: Identity, new_address: str) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ChangeNameError(f"manifest became unreadable after rename: {exc}", "post-rename") from exc
    if (data.get("agent_id"), data.get("agent_name")) != (identity.agent_id, identity.agent_name):
        raise ChangeNameError("manifest identity changed unexpectedly; no rollback was attempted", "post-rename")
    data["address"] = new_address
    _atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _remove_markers(root: Path) -> None:
    for marker in MARKERS:
        try:
            (root / marker).unlink()
        except FileNotFoundError:
            pass


def _rename_no_replace(old: Path, new: Path) -> None:
    """Atomically rename one directory without ever replacing a destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    old_bytes = os.fsencode(old)
    new_bytes = os.fsencode(new)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renamex_np is unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(old_bytes, new_bytes, 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renameat2 is unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, old_bytes, -100, new_bytes, 0x00000001)  # AT_FDCWD, RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, f"atomic no-replace rename is unsupported on {sys.platform}")
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(new))


def _launch_and_verify(pf: Preflight, lease: Any, timeout: float,
                       *, on_rename: Callable[[], None] | None = None) -> tuple[int, Path]:
    old, new = pf.old, pf.new
    try:
        _rename_no_replace(old, new)
        if on_rename is not None:
            on_rename()
    except OSError as exc:
        lease.close()
        raise ChangeNameError(f"atomic same-parent no-replace rename failed: {exc}", "rename") from exc
    child: subprocess.Popen[Any] | None = None
    log: Path | None = None
    try:
        _atomic_write(new / "init.json", pf.init_text)
        _update_manifest(new / ".agent.json", pf.identity, new.name)
        _remove_markers(new)
        lease.close()
        heartbeat_before = _heartbeat(new)
        launch_wall = time.time()
        log = new / "logs" / RELAUNCH_LOG_NAME
        log.parent.mkdir(parents=True, exist_ok=True)
        stream = log.open("a", encoding="utf-8")
        runtime = str(pf.runtime.rebased_executable)
        argv = [runtime, "-m", "lingtai", "run", str(new)]
        child = subprocess.Popen(
            argv, cwd=str(new), stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
        )
        stream.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ChangeNameError(
                    f"resumed agent exited with status {child.returncode}; inspect {log}",
                    "post-rename", pid=child.pid, log=log,
                )
            matching = exact_processes(new)
            value = _heartbeat(new)
            fresh_after_launch = heartbeat_fresh(new) and (new / ".agent.heartbeat").stat().st_mtime >= launch_wall
            manifest = json.loads((new / ".agent.json").read_text(encoding="utf-8"))
            if (any(pid == child.pid for pid, _ in matching)
                    and fresh_after_launch and value is not None and value >= (heartbeat_before or 0)
                    and manifest.get("address") == new.name
                    and manifest.get("agent_id") == pf.identity.agent_id
                    and manifest.get("agent_name") == pf.identity.agent_name):
                return child.pid, log
            time.sleep(0.1)
        raise ChangeNameError(
            "resumed process did not prove exact process, fresh heartbeat, and identity in time; new directory retained",
            "post-rename", pid=child.pid if child else None, log=log,
        )
    except ChangeNameError:
        raise
    except Exception as exc:
        raise ChangeNameError(
            f"post-rename verification failed; new directory retained: {exc}",
            "post-rename", pid=child.pid if child else None, log=log,
        ) from exc


def _failure_receipt(root: Path, *, old: Path, new: Path, phase: str, error: str,
                     pid: int | None = None, log: Path | None = None) -> Path:
    """Write a failure receipt only when its selected root already exists."""
    path = _receipt_path(root)
    if not root.is_dir():
        return path
    try:
        return write_receipt(root, status="failure", phase=phase, old=old, new=new,
                             pid=pid, log=log, error=error)
    except OSError:
        # The path remains actionable even if a permissions/full-disk problem
        # prevents the receipt; importantly, do not create a missing workdir.
        return path


def supervise(old_arg: str | Path, new_name: str, *, timeout: float = DEFAULT_TIMEOUT,
              foreground: bool = True) -> int:
    old = Path(old_arg)
    new = old.parent / new_name
    renamed = False

    def mark_renamed() -> None:
        nonlocal renamed
        renamed = True

    try:
        pf = preflight(old, new_name)
        (old / ".suspend").touch()
        lease = _wait_shutdown(old, pf.pid, timeout)
        try:
            pid, log = _launch_and_verify(pf, lease, timeout, on_rename=mark_renamed)
        except ChangeNameError:
            # This supervisor's rename callback is the sole proof that *it*
            # moved old to new.  Before that point, never target a pre-existing
            # destination, even when old has since disappeared.
            error = sys.exc_info()[1]
            root = new if renamed else old
            receipt = _failure_receipt(
                root, old=old, new=new, phase=getattr(error, "phase", "post-rename"),
                pid=getattr(error, "pid", None), log=getattr(error, "log", None), error=str(error),
            )
            print(f"name change failed; receipt: {receipt}", file=sys.stderr)
            return 1
        receipt = write_receipt(new, status="success", phase="complete", old=old, new=new, pid=pid, log=log)
        print(f"name change complete: {old} -> {new}\nreceipt: {receipt}\nlog: {log}")
        return 0
    except ChangeNameError as exc:
        root = new if renamed else old
        receipt = _failure_receipt(root, old=old, new=new, phase=exc.phase,
                                   pid=getattr(locals().get("pf"), "pid", None), error=str(exc))
        print(f"name change failed ({exc.phase}): {exc}\nreceipt: {receipt}", file=sys.stderr)
        return 1


def _handoff(old: Path, new_name: str, timeout: float) -> int:
    try:
        pf = preflight(old, new_name)
        log = old / "logs" / SUPERVISOR_LOG_NAME
        log.parent.mkdir(parents=True, exist_ok=True)
        stream = log.open("a", encoding="utf-8")
        # The supervisor changes cwd to the agent directory.  Make the helper
        # path absolute so a relative operator invocation survives that cwd
        # change as well as the target directory rename.
        argv = [str(pf.runtime.executable), str(Path(__file__).resolve()), "--_supervise", str(old), new_name, "--timeout", str(timeout)]
        child = subprocess.Popen(
            argv, cwd=str(old), stdin=subprocess.DEVNULL, stdout=stream,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
        stream.close()
        print(f"supervisor handed off (pid {child.pid}); receipt will be written under old or new logs")
        print(f"supervisor log: {log}")
        return 0
    except ChangeNameError as exc:
        root = old
        receipt = _failure_receipt(root, old=old, new=old.parent / new_name,
                                   phase=exc.phase, error=str(exc))
        print(f"name change failed ({exc.phase}): {exc}\nreceipt: {receipt}", file=sys.stderr)
        return 1
    except OSError as exc:
        receipt = _failure_receipt(old, old=old, new=old.parent / new_name,
                                   phase="handoff", error=str(exc))
        print(f"supervisor handoff failed: {exc}\nreceipt: {receipt}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_dir", help="absolute existing agent directory")
    parser.add_argument("new_basename", help="new single-segment basename")
    parser.add_argument("--foreground", action="store_true", help="run supervisor synchronously and return final result")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="bounded shutdown/launch timeout in seconds")
    parser.add_argument("--_supervise", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.timeout <= 0:
        print("--timeout must be positive", file=sys.stderr)
        return 2
    if args._supervise or args.foreground:
        return supervise(args.old_dir, args.new_basename, timeout=args.timeout)
    return _handoff(Path(args.old_dir), args.new_basename, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
