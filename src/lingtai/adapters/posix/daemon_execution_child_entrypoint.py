"""Exact run-owned execution child for a detached daemon supervisor."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from threading import Event

from .process_identity import process_identity

_MAX_CAPSULE_BYTES = 4 * 1024 * 1024


def _read_capsule() -> dict:
    raw = os.environ.pop("LINGTAI_DAEMON_CAPSULE_FD", None)
    if raw is None:
        return {}
    fd = int(raw)
    chunks = []
    total = 0
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_CAPSULE_BYTES:
                raise ValueError("daemon runtime capsule exceeds size limit")
            chunks.append(chunk)
    finally:
        os.close(fd)
    value = json.loads(b"".join(chunks).decode("utf-8"))
    return value if isinstance(value, dict) else {}


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        raise SystemExit("usage: daemon_execution_child <manifest> <run_id> <mode> [generation]")
    manifest_path, run_id, mode = argv[:3]
    generation = argv[3] if len(argv) == 4 else None
    capsule = _read_capsule()
    from lingtai.kernel.daemon_supervisor.manifest import read_manifest
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    manifest = read_manifest(Path(manifest_path))
    if manifest.get("run_id") != run_id:
        raise ValueError("execution child run identity mismatch")
    run_dir = DaemonRunDir.attach(Path(manifest["run_dir"]))
    if run_dir.run_id != run_id:
        raise ValueError("execution child directory identity mismatch")
    credential_env = capsule.get("credential_env")
    if isinstance(credential_env, dict):
        credential_env = {
            key: value for key, value in credential_env.items()
            if isinstance(key, str) and isinstance(value, str)
        }
    else:
        credential_env = {}
    from lingtai.kernel.daemon_supervisor.manifest import secret_argv_values
    runtime_redactions = list(credential_env.values())
    runtime_redactions.extend(secret_argv_values(capsule.get("backend_argv")))
    run_dir.set_ephemeral_redactions(runtime_redactions)
    # This interpreter is the dedicated execution child.  The long-lived
    # supervisor never receives these values; runners below copy them only when
    # creating the final backend process.
    os.environ.update(credential_env)
    pid = os.getpid()
    pgid = os.getpgid(pid)
    run_dir.update_state(
        execution_pid=pid, execution_pgid=pgid,
        execution_start_identity=process_identity(pid),
        execution_registered_at=run_dir._now_iso(),
        execution_registration="registered",
    )
    try:
        from lingtai.tools.daemon.supervisor_runtime import _maybe_register_test_fake_llm
        _maybe_register_test_fake_llm()
        from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
        from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
        from lingtai.adapters.posix.interactive_terminal import (
            PosixInteractiveTerminalAdapter,
        )
        # This child already owns the detached session/process group. Both
        # production Ports therefore inherit it; the host binds its immutable
        # observation callback before the first backend spawn.
        host = DetachedDaemonExecutionHost(
            run_dir, manifest, Event(), Event(), capsule=capsule,
            process_port=PosixDaemonProcessPort(start_new_session=False),
            interactive_terminal_port=PosixInteractiveTerminalAdapter(
                start_new_session=False,
            ),
        )
        if mode == "resume":
            host.run_resume(generation or "")
        else:
            host.run_with_events(Event(), Event())
    except BaseException as exc:
        # Terminal gates in DaemonRunDir make this safe if a supervisor has
        # already committed timeout/cancelled while this child was unwinding.
        try:
            run_dir.append_event(
                "daemon_execution_child_error", exception=type(exc).__name__,
            )
        except Exception:
            pass
        try:
            run_dir.mark_failed(exc)
        except Exception:
            pass
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
