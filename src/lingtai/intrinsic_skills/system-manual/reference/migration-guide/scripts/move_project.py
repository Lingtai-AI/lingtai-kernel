#!/usr/bin/env python3
"""Cooperatively move one POSIX Project root and relaunch its initiating Agent."""
from __future__ import annotations

import argparse
import copy
import ctypes
import errno
import json
import math
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from lingtai import venv_resolve
from lingtai.adapters.posix.process_identity import process_identity
from lingtai.kernel.process_match import match_agent_run

if os.name == "posix":
    import fcntl
else:
    fcntl = None

HEARTBEAT_MAX_AGE = 5
DEFAULT_TIMEOUT = 30
_PROCESS_OBSERVATION_ENV: dict[str, str] | None = None

class MoveProjectError(RuntimeError):
    """A refusal or failed project-root cutover."""

def _process_observation_environment() -> dict[str, str]:
    global _PROCESS_OBSERVATION_ENV
    if _PROCESS_OBSERVATION_ENV is not None: return _PROCESS_OBSERVATION_ENV
    candidates = (("en_US.UTF-8",) if sys.platform == "darwin" else ("C.UTF-8", "C.utf8", "en_US.UTF-8") if sys.platform.startswith("linux") else ())
    for spelling in candidates:
        environment = os.environ.copy(); environment["LC_ALL"] = spelling
        try:
            result = subprocess.run(["locale", "charmap"], stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", errors="strict", timeout=2, check=False, env=environment)
        except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
            raise MoveProjectError("verified UTF-8 process-observation locale probe failed") from exc
        if result.returncode == 0 and result.stdout.strip().upper().replace("_", "-") == "UTF-8":
            _PROCESS_OBSERVATION_ENV = environment; return environment
    raise MoveProjectError("no verified UTF-8 process-observation locale is available")

@dataclass(frozen=True)
class Plan:
    source: Path
    target: Path
    agent: Path
    target_agent: Path
    identity: tuple[str, str, str]
    init: dict
    rebased_init: dict
    resumed_runtime: Path
    environment: dict[str, str]
    workdirs: tuple[Path, ...]
    pid: int
    process_token: str

def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

def _require_process_path(path: Path, label: str) -> None:
    text = str(path)
    if text != text.strip() or not text.isprintable():
        raise MoveProjectError(f"{label} is unsafe for canonical process-table round-trip")

def _canonical_directory(value: str | Path, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir() or path != path.resolve():
        raise MoveProjectError(f"{label} must be an existing canonical absolute non-symlink directory")
    return path

def _roots(agent_arg: str | Path, target_arg: str | Path) -> tuple[Path, Path, Path]:
    if os.name != "posix":
        raise MoveProjectError("POSIX only")
    agent = _canonical_directory(agent_arg, "--agent-dir")
    if agent.parent.name != ".lingtai":
        raise MoveProjectError("--agent-dir parent must be named .lingtai")
    source = _canonical_directory(agent.parent.parent, "source Project root")

    target = Path(target_arg)
    if not target.is_absolute() or target != target.resolve(strict=False):
        raise MoveProjectError("--to must be a canonical absolute path")
    for path, label in ((source, "source Project root"), (target, "target Project root"), (agent, "initiating Agent workdir")): _require_process_path(path, label)
    target_parent = _canonical_directory(target.parent, "target parent")
    if target.exists() or target.is_symlink():
        raise MoveProjectError(f"target must be absent, including no symlink: {target}")
    if target == source or _within(target, source):
        raise MoveProjectError("target must be outside the source Project root")
    if source.stat().st_dev != target_parent.stat().st_dev:
        raise MoveProjectError("source and target parent must be on the same filesystem")
    return source, target, agent

def _read_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MoveProjectError(f"cannot read {label} from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MoveProjectError(f"{label} must contain a JSON object")
    return value

def _identity(agent: Path) -> tuple[str, str, str]:
    manifest = _read_object(agent / ".agent.json", ".agent.json identity")
    agent_id = manifest.get("agent_id")
    agent_name = manifest.get("agent_name")
    address = manifest.get("address")
    if not isinstance(agent_id, str) or not agent_id:
        raise MoveProjectError(".agent.json must contain a non-empty agent_id")
    if not isinstance(agent_name, str) or not agent_name:
        raise MoveProjectError(".agent.json must contain a non-empty agent_name")
    if address != agent.name:
        raise MoveProjectError(".agent.json address must match the Agent basename")
    return agent_id, agent_name, address

def _record_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MoveProjectError(f"cannot classify Agent record: {path}") from exc
    return True

def _agent_workdirs(project: Path) -> tuple[Path, ...]:
    try:
        children = sorted((project / ".lingtai").iterdir(), key=lambda path: path.name)
    except OSError as exc:
        raise MoveProjectError(f"cannot enumerate Project Agent workdirs: {exc}") from exc
    found = []
    for child in children:
        records = (child / ".agent.json", child / "init.json")
        presence = tuple(_record_present(record) for record in records)
        if not any(presence):
            continue
        _require_process_path(child, "Agent-present direct child")
        if child.is_symlink() or not child.is_dir() or child != child.resolve():
            raise MoveProjectError(f"Agent-present workdir is noncanonical: {child.name}")
        if child.name == "human":
            if presence != (True, False):
                raise MoveProjectError("canonical human pseudo-agent must be record-only")
            if records[0].is_symlink() or not records[0].is_file():
                raise MoveProjectError("canonical human manifest must be a regular non-symlink file")
            try:
                human = _read_object(records[0], "human .agent.json")
            except MoveProjectError as exc:
                raise MoveProjectError("canonical human pseudo-agent cannot be safely classified") from exc
            if human.get("agent_name") != "human" or human.get("address") != "human" or human.get("admin") is not None:
                raise MoveProjectError("canonical human pseudo-agent identity or policy is invalid")
            continue
        if not all(presence) or any(path.is_symlink() or not path.is_file() for path in records):
            raise MoveProjectError(f"Agent-present workdir records are incomplete or noncanonical: {child.name}")
        try:
            _, name, _ = _identity(child)
            manifest = _read_object(records[1], "init.json").get("manifest")
            configured = manifest.get("agent_name") if isinstance(manifest, dict) else None
        except MoveProjectError as exc:
            raise MoveProjectError(f"Agent-present workdir cannot be safely classified: {child.name}") from exc
        if configured != name:
            raise MoveProjectError(f"Agent-present workdir identities disagree: {child.name}")
        found.append(child)
    return tuple(found)

def _rebased_init(init: dict, source: Path, target: Path) -> dict:
    value = init.get("venv_path")
    if "venv_path" in init and not isinstance(value, str):
        raise MoveProjectError("init.json venv_path must be a string when present")
    fallback = not value
    configured = venv_resolve._DEFAULT_RUNTIME_DIR if fallback else Path(value)
    try:
        canonical = configured.resolve(strict=True)
    except OSError as exc:
        raise MoveProjectError(f"cannot resolve init.json venv_path: {exc}") from exc
    if (
        not configured.is_absolute()
        or not configured.is_dir()
        or configured.is_symlink()
        or configured != canonical
    ):
        raise MoveProjectError("init.json venv_path must be an existing canonical absolute non-symlink directory")
    if fallback and _within(canonical, source):
        raise MoveProjectError("managed default venv must remain outside the source Project")
    rebased = copy.deepcopy(init)
    if _within(canonical, source):
        rebased["venv_path"] = str(target / canonical.relative_to(source))
    return rebased

def _relaunch_environment(source: Path, target: Path) -> tuple[dict[str, str], tuple[Path, ...]]:
    environment = os.environ.copy()
    rebased_roots: list[Path] = []
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        if name not in environment:
            continue
        entries = environment[name].split(os.pathsep)
        for index, raw in enumerate(entries):
            if not raw or not Path(raw).is_absolute():
                continue
            path = Path(raw)
            lexical_source = _within(path, source)
            try:
                canonical = path.resolve(strict=True)
            except OSError as exc:
                if lexical_source:
                    raise MoveProjectError(f"{name} has an unusable old-root path") from exc
                continue
            if lexical_source or _within(canonical, source):
                if path.is_symlink() or path != canonical or not _within(canonical, source):
                    raise MoveProjectError(f"{name} has a noncanonical old-root path")
                entries[index] = str(target / canonical.relative_to(source))
                rebased_roots.append(canonical)
        environment[name] = os.pathsep.join(entries)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment, tuple(rebased_roots)

def _probe(runtime: Path, cwd: Path, environment: dict[str, str]) -> tuple[Path, Path]:
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise MoveProjectError(f"configured venv has no executable Python: {runtime}")
    code = (
        "import json,os,sys,lingtai;from pathlib import Path;"
        "from lingtai.venv_resolve import _env_marker_status_detail as check,"
        "_is_default_runtime_dir as default,_python_selection_policy as select;"
        "venv=Path(sys.argv[1]);managed=default(venv);policy=None\nif managed:\n try:policy=select()\n except RuntimeError:pass\n"
        "status,detail=check(venv,policy=policy);"
        "print(json.dumps([os.path.realpath(sys.executable),"
        "os.path.realpath(lingtai.__file__),status,detail,managed]))"
    )
    try:
        result = subprocess.run(
            [str(runtime), "-c", code, str(runtime.parent.parent)], cwd=cwd, env=environment,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=3, check=True,
        )
        values = json.loads(result.stdout)
        if not isinstance(values, list) or len(values) != 5 or not isinstance(values[4], bool):
            raise ValueError("unexpected probe result")
        executable, imported = (Path(value).resolve(strict=True) for value in values[:2])
        if executable != runtime.resolve(strict=True):
            raise ValueError("runtime executable mismatch")
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        raise MoveProjectError(f"configured runtime cannot prove its executable and lingtai import: {runtime}") from exc
    if values[4] and values[2] != "match":
        raise MoveProjectError(f"managed default venv requires a matching environment marker; status {values[2]}: {values[3]}")
    if values[2] == "mismatch":
        raise MoveProjectError(f"configured venv environment marker mismatch: {values[3]}")
    return executable, imported

def _project_processes(project: Path, workdirs: tuple[Path, ...]) -> dict[int, Path]:
    try:
        result = subprocess.run(["ps", "-ax", "-o", "pid=,command="], capture_output=True,
            encoding="utf-8", errors="strict", timeout=2, check=True, env=_process_observation_environment())
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise MoveProjectError(f"process scan failed; refusing to infer absence: {exc}") from exc

    agents_root = project / ".lingtai"
    found: dict[int, Path] = {}
    for line in result.stdout.splitlines():
        fields = line.split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            raise MoveProjectError("process scan returned an unparseable row")
        command = fields[1]
        candidates = [Path(command[index:]) for index, char in enumerate(command)
                      if char == "/" and (index == 0 or command[index - 1].isspace())
                      and match_agent_run(command, command[index:]) is not None]
        if not candidates:
            continue
        if len(candidates) != 1:
            raise MoveProjectError("process scan returned an ambiguous LingTai command")
        candidate = candidates[0]
        try:
            canonical = candidate.resolve(strict=True)
        except OSError as exc:
            if _within(candidate, agents_root):
                raise MoveProjectError("process scan returned an unusable Project Agent path") from exc
            continue
        if canonical not in workdirs:
            if _within(candidate, agents_root) or _within(canonical, agents_root):
                raise MoveProjectError("process scan returned an unexpected Project Agent path")
            continue
        if candidate != canonical or candidate.parent != agents_root:
            raise MoveProjectError("process scan returned an ambiguous Project Agent path")
        found[int(fields[0])] = candidate
    return found

def _initiating_pid(project: Path, agent: Path, workdirs: tuple[Path, ...]) -> int:
    processes = _project_processes(project, workdirs)
    others = {pid: path for pid, path in processes.items() if path != agent}
    if others:
        raise MoveProjectError("another live Project Agent exists; refusing migration")
    initiating = [pid for pid, path in processes.items() if path == agent]
    if len(initiating) != 1:
        raise MoveProjectError("initiating Agent must have exactly one live process")
    return initiating[0]

def _process_token(pid: int) -> str:
    token = process_identity(pid)
    if not isinstance(token, str) or not token:
        raise MoveProjectError("stable initiating process identity is unavailable")
    return token

def _require_self_caller(plan: Plan) -> None:
    try:
        result = subprocess.run(["ps", "-ax", "-o", "pid=,ppid="], capture_output=True,
            encoding="utf-8", errors="strict", timeout=2, check=True, env=_process_observation_environment())
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        raise MoveProjectError(f"cannot prove initiating Agent ancestry: {exc}") from exc
    rows = [line.split() for line in result.stdout.splitlines()]
    if any(len(row) != 2 or not all(field.isdigit() for field in row) for row in rows):
        raise MoveProjectError("cannot prove initiating Agent ancestry from process table")
    parents = {int(pid): int(ppid) for pid, ppid in rows}
    current = os.getpid()
    for _ in parents:
        current = parents.get(current, 0)
        if current == plan.pid and process_identity(current) == plan.process_token:
            return
    raise MoveProjectError("public self-migration caller is not descended from the exact initiating Agent")

def _validate_observed_processes(plan: Plan, processes: dict[int, Path], post_suspend: bool = False) -> None:
    if any(pid != plan.pid or path != plan.agent for pid, path in processes.items()):
        raise MoveProjectError("Project process identity changed or another live Project Agent appeared")
    token = process_identity(plan.pid) if plan.pid in processes else plan.process_token
    if post_suspend and token is None:
        rescanned = _project_processes(plan.source, plan.workdirs)
        _validate_observed_processes(plan, rescanned)
        if plan.pid not in rescanned: return
    if token != plan.process_token:
        raise MoveProjectError("initiating process incarnation changed")

def _require_prompt_absent(agent: Path) -> None:
    if _record_present(agent / ".prompt"):
        raise MoveProjectError("initiating Agent already has a prompt; preserving it")

def _fresh(agent: Path) -> bool:
    try:
        age = time.time() - (agent / ".agent.heartbeat").stat().st_mtime
    except OSError:
        return False
    return 0 <= age < HEARTBEAT_MAX_AGE

def _try_lock(agent: Path):
    if fcntl is None:
        raise MoveProjectError("POSIX only")
    try:
        stream = (agent / ".agent.lock").open("a+")
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return stream
    except (OSError, BlockingIOError):
        try:
            stream.close()
        except UnboundLocalError:
            pass
        return None

def _lock_held(agent: Path) -> bool:
    if not (agent / ".agent.lock").is_file():
        return False
    stream = _try_lock(agent)
    if stream is None:
        return True
    stream.close()
    return False

def preflight(agent_arg: str | Path, target_arg: str | Path) -> Plan:
    source, target, agent = _roots(agent_arg, target_arg)
    _process_observation_environment()
    if (agent / ".suspend").exists() or (agent / ".suspend").is_symlink():
        raise MoveProjectError("initiating Agent already has a suspend request")
    _require_prompt_absent(agent)
    logs = agent / "logs"
    if not logs.is_dir() or logs.is_symlink() or logs != logs.resolve():
        raise MoveProjectError("initiating Agent must have an existing canonical logs directory")

    identity = _identity(agent)
    agent_name = identity[1]
    init = _read_object(agent / "init.json", "init.json")
    try:
        configured_name = init["manifest"]["agent_name"]
    except (KeyError, TypeError) as exc:
        raise MoveProjectError("init.json must contain manifest.agent_name") from exc
    if configured_name != agent_name:
        raise MoveProjectError("init.json and .agent.json Agent identities disagree")
    rebased_init = _rebased_init(init, source, target)
    configured_venv, resumed_venv = (Path(init.get("venv_path") or venv_resolve._DEFAULT_RUNTIME_DIR),
                                      Path(rebased_init.get("venv_path") or venv_resolve._DEFAULT_RUNTIME_DIR))
    runtime = configured_venv / "bin" / "python"
    resumed_runtime = resumed_venv / "bin" / "python"
    environment, rebased_roots = _relaunch_environment(source, target)
    probe_environment = os.environ.copy()
    probe_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    _, imported = _probe(runtime, agent, probe_environment)
    if (
        _within(imported, source)
        and not _within(imported, configured_venv)
        and not any(_within(imported, root) for root in rebased_roots)
    ):
        raise MoveProjectError("project-local lingtai import is outside the venv and has no safely rebased path")

    workdirs = _agent_workdirs(source)
    if agent not in workdirs:
        raise MoveProjectError("initiating Agent is not a valid direct Project workdir")
    pid = _initiating_pid(source, agent, workdirs)
    token = _process_token(pid)
    if not _fresh(agent):
        raise MoveProjectError("initiating Agent heartbeat is missing, stale, or future-dated")
    if not _lock_held(agent):
        raise MoveProjectError("initiating Agent lease is not held")
    return Plan(source=source, target=target, agent=agent, target_agent=target / ".lingtai" / agent.name,
        identity=identity, init=init, rebased_init=rebased_init, resumed_runtime=resumed_runtime,
        environment=environment, workdirs=workdirs, pid=pid, process_token=token)

def _deadline(timeout: float) -> float:
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise MoveProjectError("timeout must be finite and positive")
    return time.monotonic() + timeout

def _wait_stopped(plan: Plan, timeout: float):
    deadline = _deadline(timeout)
    while time.monotonic() < deadline:
        processes = _project_processes(plan.source, plan.workdirs)
        _validate_observed_processes(plan, processes, post_suspend=True)
        lock = _try_lock(plan.agent)
        if not processes and not _fresh(plan.agent) and lock is not None:
            return lock
        if lock is not None:
            lock.close()
        time.sleep(0.1)
    raise MoveProjectError("suspend did not release the exact process, heartbeat, and lease before timeout")

def _fence_siblings(plan: Plan) -> list:
    if _agent_workdirs(plan.source) != plan.workdirs:
        raise MoveProjectError("Project Agent workdir set changed before sibling fencing")
    leases = []
    try:
        for sibling in plan.workdirs:
            if sibling == plan.agent:
                continue
            lease = _try_lock(sibling)
            if lease is None:
                raise MoveProjectError(f"stopped sibling Agent lease is unavailable: {sibling.name}")
            leases.append(lease)
    except BaseException:
        for lease in leases:
            lease.close()
        raise
    return leases

def _rename_no_replace(source: Path, target: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        rename = getattr(libc, "renamex_np", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renamex_np unavailable")
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = rename(os.fsencode(source), os.fsencode(target), 0x00000004)  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renameat2 unavailable")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = rename(-100, os.fsencode(source), -100, os.fsencode(target), 1)  # RENAME_NOREPLACE
    else:
        raise OSError(errno.ENOTSUP, f"no no-replace rename on {sys.platform}")
    if result:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))

def _fsync_parent(path: Path) -> None:
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try: os.fsync(fd)
    finally: os.close(fd)

def _write_exclusive_prompt(path: Path, content: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        remaining = (content + "\n").encode()
        while remaining: remaining = remaining[os.write(fd, remaining):]
        os.fsync(fd)
    finally: os.close(fd)
    _fsync_parent(path)

def _continuation_prompt(source: Path, target: Path) -> str:
    return (f"Project-root migration continuation: this same Agent's Project moved from {source} to {target}. "
            f"From the new root {target}, continue only the remaining stale-reference migration, then verify and "
            "report. Do not move again, roll back, create symlinks, delete or clean anything, change the Agent "
            "address, or alter unrelated config or auth.")

def _write_json(path: Path, data: dict) -> None:
    mode = path.stat().st_mode & 0o7777
    fd, temporary = tempfile.mkstemp(prefix=".move-project-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_parent(path)
    except BaseException:
        try: os.close(fd)
        except OSError: pass
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise

def _revalidate_cutover(plan: Plan) -> None:
    source, target, agent = _roots(plan.agent, plan.target)
    if (source, target, agent) != (plan.source, plan.target, plan.agent):
        raise MoveProjectError("Project paths changed before cutover")
    if _agent_workdirs(plan.source) != plan.workdirs:
        raise MoveProjectError("Project Agent workdir set changed before cutover")
    _require_prompt_absent(plan.agent)
    if _project_processes(plan.source, plan.workdirs):
        raise MoveProjectError("Project is not process-quiescent before cutover")
    if _fresh(plan.agent):
        raise MoveProjectError("initiating Agent heartbeat is still fresh before cutover")
    if _identity(plan.agent) != plan.identity:
        raise MoveProjectError("Agent identity or address changed before cutover")
    if _read_object(plan.agent / "init.json", "init.json") != plan.init:
        raise MoveProjectError("init.json changed before cutover")

def _reject_old_source_processes(plan: Plan) -> None:
    try:
        observed = _project_processes(plan.source, plan.workdirs)
    except MoveProjectError as exc:
        raise MoveProjectError(f"old source Agent process observation is unsafe: {exc}") from exc
    if observed:
        raise MoveProjectError("an Agent-run process still names the old source root")

def _resume(plan: Plan, timeout: float) -> int:
    if plan.rebased_init != plan.init:
        _write_json(plan.target_agent / "init.json", plan.rebased_init)
    if _identity(plan.target_agent) != plan.identity:
        raise MoveProjectError("Agent identity or address changed during cutover")
    executable, imported = _probe(plan.resumed_runtime, plan.target_agent, plan.environment)
    if _within(executable, plan.source) or _within(imported, plan.source):
        raise MoveProjectError("target probe observed an old-root executable or import")
    _reject_old_source_processes(plan)
    prompt = plan.target_agent / ".prompt"
    _write_exclusive_prompt(prompt, _continuation_prompt(plan.source, plan.target))
    (plan.target_agent / ".suspend").unlink(missing_ok=True)

    log = plan.target_agent / "logs" / "move-project.log"
    started = time.time()
    with log.open("a", encoding="utf-8") as stream:
        child = subprocess.Popen([str(plan.resumed_runtime), "-m", "lingtai", "run", str(plan.target_agent)],
            cwd=plan.target_agent, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            start_new_session=True, env=plan.environment)
    deadline = _deadline(timeout)
    while time.monotonic() < deadline:
        _reject_old_source_processes(plan)
        if child.poll() is not None:
            raise MoveProjectError(f"relaunched Agent exited ({child.returncode}); inspect {log}")
        if _identity(plan.target_agent) != plan.identity:
            raise MoveProjectError("relaunched Agent identity or address changed")
        try:
            heartbeat_after_launch = (plan.target_agent / ".agent.heartbeat").stat().st_mtime >= started
        except OSError:
            heartbeat_after_launch = False
        processes = _project_processes(plan.target, tuple(plan.target / path.relative_to(plan.source) for path in plan.workdirs))
        unexpected = {pid: path for pid, path in processes.items() if pid != child.pid or path != plan.target_agent}
        if unexpected:
            raise MoveProjectError("another Agent or unexpected process appeared after cutover")
        observed_pid = child.pid if processes == {child.pid: plan.target_agent} else None
        if (observed_pid == child.pid and heartbeat_after_launch and _fresh(plan.target_agent)
                and _lock_held(plan.target_agent) and not _record_present(prompt)):
            return child.pid
        time.sleep(0.1)
    raise MoveProjectError(f"relaunched Agent did not prove fresh target liveness and identity; inspect {log}")

def supervise(agent_arg: str | Path, target_arg: str | Path, timeout: float, expected_pid: int,
              expected_token: str, gate_fd: int | None = None) -> int:
    renamed = False
    sibling_leases = []
    try:
        _deadline(timeout)
        if gate_fd is not None and os.read(gate_fd, 1) != b"P":
            raise MoveProjectError("outer handoff ended before supervisor preflight")
        plan = preflight(agent_arg, target_arg)
        if (plan.pid, plan.process_token) != (expected_pid, expected_token):
            raise MoveProjectError("initiating process incarnation changed after handoff")
        sibling_leases = _fence_siblings(plan)
        if gate_fd is not None:
            if os.write(gate_fd, b"R") != 1:
                raise MoveProjectError("cannot acknowledge supervisor readiness")
            if os.read(gate_fd, 1) != b"C":
                raise MoveProjectError("outer handoff ended before suspend marker commit")
            if os.read(gate_fd, 1):
                raise MoveProjectError("invalid trailing supervisor commit data")
        marker = plan.agent / ".suspend"
        try:
            marker_mode = marker.lstat().st_mode
        except FileNotFoundError:
            if gate_fd is None:
                raise MoveProjectError("outer handoff did not commit an exact suspend marker")
        except OSError as exc:
            raise MoveProjectError("cannot classify outer suspend marker") from exc
        else:
            if not stat.S_ISREG(marker_mode):
                raise MoveProjectError("outer handoff did not commit an exact suspend marker")
        _validate_observed_processes(plan, _project_processes(plan.source, plan.workdirs), post_suspend=True)
        initiator_lease = _wait_stopped(plan, timeout)
        try:
            _revalidate_cutover(plan)
            _rename_no_replace(plan.source, plan.target)
            renamed = True
        finally:
            initiator_lease.close()
        resumed_pid = _resume(plan, timeout)
        print(f"project move complete: {plan.source} -> {plan.target}; relaunched pid {resumed_pid}")
        return 0
    except (MoveProjectError, OSError) as exc:
        state = "target is authoritative; no automatic rollback or repair" if renamed else "source is authoritative; no automatic restart or retry"
        print(f"project move failed: {exc}\n{state}", file=sys.stderr)
        return 1
    finally:
        if gate_fd is not None:
            try: os.close(gate_fd)
            except OSError: pass
        for sibling_lease in sibling_leases:
            sibling_lease.close()

def handoff(agent_arg: str | Path, target_arg: str | Path, timeout: float) -> int:
    gate_parent = gate_child = None
    try:
        deadline = _deadline(timeout)
        plan = preflight(agent_arg, target_arg)
        _require_self_caller(plan)
        gate_parent, gate_child = socket.socketpair()
        command = [
            sys.executable, str(Path(__file__).resolve()), "--_supervise", "--agent-dir", str(plan.agent),
            "--to", str(plan.target), "--timeout", str(timeout), "--_expected-pid", str(plan.pid),
            "--_expected-token", plan.process_token, "--_gate-fd", str(gate_child.fileno()),
        ]
        log = plan.agent / "logs" / "move-project.log"
        with log.open("a", encoding="utf-8") as stream:
            child = subprocess.Popen(command, cwd=plan.target.parent, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True,
                pass_fds=(gate_child.fileno(),))
        gate_child.close()
        gate_child = None
        gate_parent.settimeout(max(deadline - time.monotonic(), 0))
        gate_parent.sendall(b"P")
        if gate_parent.recv(1) != b"R":
            raise MoveProjectError("supervisor did not prove preflight and sibling fences before timeout")
        if _agent_workdirs(plan.source) != plan.workdirs:
            raise MoveProjectError("Project Agent workdir set changed before suspend")
        _require_prompt_absent(plan.agent)
        processes = _project_processes(plan.source, plan.workdirs)
        _validate_observed_processes(plan, processes)
        if processes != {plan.pid: plan.agent} or not _fresh(plan.agent) or not _lock_held(plan.agent):
            raise MoveProjectError("initiating Agent lost process, heartbeat, or lease before suspend")
        _require_self_caller(plan)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        marker_fd = os.open(plan.agent / ".suspend", flags, 0o600)
        try:
            os.fchmod(marker_fd, 0o600)
            if stat.S_IMODE(os.fstat(marker_fd).st_mode) != 0o600: raise MoveProjectError("suspend marker mode finalization failed")
        finally:
            os.close(marker_fd)
        gate_parent.sendall(b"C")
        gate_parent.close()
        gate_parent = None
        print(f"project move supervisor started ({child.pid}); "
              f"log moves to {plan.target_agent / 'logs' / 'move-project.log'}")
        return 0
    except (MoveProjectError, OSError) as exc:
        print(f"project move did not start: {exc}", file=sys.stderr)
        return 1
    finally:
        for gate in (gate_parent, gate_child):
            if gate is not None: gate.close()

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-dir", required=True)
    parser.add_argument("--to", required=True, dest="target")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--_supervise", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--_expected-pid", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--_expected-token", help=argparse.SUPPRESS)
    parser.add_argument("--_gate-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if args._supervise:
        if None in (args._expected_pid, args._gate_fd) or not args._expected_token:
            parser.error("hidden supervisor PID, token, and gate descriptor are required")
        return supervise(args.agent_dir, args.target, args.timeout, args._expected_pid, args._expected_token, args._gate_fd)
    return handoff(args.agent_dir, args.target, args.timeout)

if __name__ == "__main__":
    raise SystemExit(main())
