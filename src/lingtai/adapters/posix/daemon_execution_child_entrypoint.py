"""Exact run-owned execution child for a detached daemon supervisor."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Event

from .daemon_capsule import (
    ReceivedDaemonCapsule,
    close_fd,
    receive_capsule_from_environment,
)
from .process_identity import process_identity


def _read_capsule_wire() -> ReceivedDaemonCapsule:
    wire = receive_capsule_from_environment()
    return wire if wire is not None else ReceivedDaemonCapsule(value={})


def _read_capsule() -> dict:
    """Compatibility seam for tests that need only the JSON capsule."""
    wire = _read_capsule_wire()
    try:
        return wire.value
    finally:
        wire.close()


def _run_execution_child(
    argv: list[str],
    *,
    capsule: dict,
    adopted_fd: int | None,
) -> int:
    owned_fd = adopted_fd
    host = None
    try:
        if len(argv) not in (3, 4):
            raise SystemExit(
                "usage: daemon_execution_child <manifest> <run_id> "
                "<mode> [generation]"
            )
        manifest_path, run_id, mode = argv[:3]
        generation = argv[3] if len(argv) == 4 else None
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
        from lingtai.kernel.daemon_supervisor.manifest import (
            backend_env_redaction_values,
            secret_argv_values,
        )
        runtime_redactions = list(credential_env.values())
        runtime_redactions.extend(secret_argv_values(capsule.get("backend_argv")))
        runtime_redactions.extend(backend_env_redaction_values(capsule))
        run_dir.set_ephemeral_redactions(runtime_redactions)
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
            host = DetachedDaemonExecutionHost(
                run_dir, manifest, Event(), Event(), capsule=capsule,
                process_port=PosixDaemonProcessPort(start_new_session=False),
                interactive_terminal_port=PosixInteractiveTerminalAdapter(
                    start_new_session=False,
                ),
                adopted_fd=owned_fd,
            )
            owned_fd = None
            if capsule.get("driver_authority_required") is True:
                host.adopt_derived_driver_authority()
            if mode == "resume":
                host.run_resume(generation or "")
            else:
                host.run_with_events(Event(), Event())
        except BaseException as exc:
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
    finally:
        close_fd(owned_fd)
        if host is not None:
            host.close_adopted_fd()


def main(argv: list[str]) -> int:
    wire = _read_capsule_wire()
    try:
        return _run_execution_child(
            argv,
            capsule=wire.value,
            adopted_fd=wire.take_fd(),
        )
    finally:
        wire.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
