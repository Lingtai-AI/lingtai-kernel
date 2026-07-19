"""Windows exact run-owned execution child for a detached daemon supervisor.

Mirror of ``lingtai.adapters.posix.daemon_execution_child_entrypoint`` with
exactly three Windows deltas; everything else (identity checks, redaction
installation, credential restoration, terminal error commit) is kept
line-for-line so the two children cannot drift behaviorally:

- capsule acquisition converts the inherited pipe HANDLE to a CRT fd
  (``strict=True`` — a bad capsule descriptor fails the child loudly, same as
  the POSIX ``int(raw)``/``os.read`` path) and then reuses the POSIX module's
  mechanism-free ``_read_capsule`` loop;
- registration records ``execution_pgid=None`` — Windows has no POSIX process
  group; group ownership on this platform is the process Port's Job scope;
- host composition injects ``WindowsDaemonProcessPort`` with the
  ``INHERITED_SUPERVISOR_GROUP`` scope and NO interactive terminal port
  (ConPTY is out of scope; the claude-interactive bridge fails loudly when
  its terminal port is ``None``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from threading import Event

from lingtai.adapters.windows.daemon_supervisor import adopt_capsule_handle_to_fd
from lingtai.adapters.windows.process_identity import process_identity


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        raise SystemExit("usage: daemon_execution_child <manifest> <run_id> <mode> [generation]")
    if os.name != "nt":
        raise OSError("Windows daemon execution child requires Windows")
    manifest_path, run_id, mode = argv[:3]
    generation = argv[3] if len(argv) == 4 else None
    adopt_capsule_handle_to_fd(strict=True)
    from lingtai.adapters.posix.daemon_execution_child_entrypoint import _read_capsule
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
    run_dir.update_state(
        execution_pid=pid, execution_pgid=None,
        execution_start_identity=process_identity(pid),
        execution_registered_at=run_dir._now_iso(),
        execution_registration="registered",
    )
    try:
        from lingtai.tools.daemon.supervisor_runtime import _maybe_register_test_fake_llm
        _maybe_register_test_fake_llm()
        from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
        from lingtai.tools.daemon.process_port import DaemonProcessTerminationScope
        from lingtai.tools.daemon.windows_process import WindowsDaemonProcessPort
        # This child is the supervisor-owned execution scope. The production
        # Port therefore signals only exact children (never a Job it does not
        # own); the host binds its immutable observation callback before the
        # first backend spawn. Interactive terminal support stays absent on
        # Windows — the bridge refuses a None port loudly.
        host = DetachedDaemonExecutionHost(
            run_dir, manifest, Event(), Event(), capsule=capsule,
            process_port=WindowsDaemonProcessPort(
                termination_scope=(
                    DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP
                ),
            ),
            interactive_terminal_port=None,
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


__all__ = ["main"]
