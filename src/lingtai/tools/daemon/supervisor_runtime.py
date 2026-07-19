"""Detached daemon-run supervisor: the stable owner of one emanation.

``run_supervisor`` is the whole lifetime of the spawned process: read the run
manifest, reconstruct only the daemon-scoped runtime inputs the run needs
(tool surface, LLM session), run one emanation, watch its own local control
spool for an explicit reclaim/ask request, enforce its own deadline, commit
terminal state through the same ``DaemonRunDir`` markers the in-process path
uses, publish the terminal notification directly (no live parent agent
required), and exit.

All backend selection and execution is composed by
``lingtai.tools.daemon.execution_host``. This module owns only the detached
lifetime, identity, control, deadline, durable terminal commit, and
notification boundary; it contains no backend parser or manager-specific
execution policy. The composition import is deliberately lazy so the Core
request Port remains usable without importing concrete runtime adapters.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from lingtai.kernel import daemon_supervisor as _port_mod
from lingtai.kernel.daemon_supervisor import control
from lingtai.kernel.daemon_supervisor.agent_stub import DaemonSupervisorAgentStub
from lingtai.kernel.daemon_supervisor.manifest import read_manifest
from lingtai.adapters.posix.process_identity import (
    process_identity,
    process_identity_matches,
)

_STARTUP_HEARTBEAT_FIELD = "supervisor_pid"

# How often the supervisor polls its own control spool and re-checks its
# deadline while a lingtai-backend tool loop or a codex CLI child is running.
_CONTROL_POLL_INTERVAL_S = 0.5


_TEST_FAKE_LLM_ENV = "LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM"


def _process_start_identity(pid: int) -> str | None:
    """Return the shared stable process-incarnation identity, if observable."""
    return process_identity(pid)


def _process_group_of(pid: int) -> int | None:
    """Return the POSIX process group of *pid*; ``None`` on Windows.

    Windows has no PGID concept — supervision scope there is the process
    Port's Job Object plus exact-PID identity guards, and every recorded
    ``*_pgid`` field stays ``None`` so signal paths refuse group semantics.
    """
    if os.name == "nt":
        return None
    return os.getpgid(pid)


def select_daemon_supervisor_adapter():
    """Return the platform's production detached-supervisor adapter.

    Capability-local composition seam shared by the parent manager's spawn
    sites and this runtime's execution-child/resume spawns. POSIX and Windows
    each have exactly one production adapter; any other platform fails loudly
    instead of pretending detached supervision exists.
    """
    if os.name == "posix":
        from lingtai.adapters.posix.daemon_supervisor import PosixDaemonSupervisorAdapter
        return PosixDaemonSupervisorAdapter()
    if os.name == "nt":
        from lingtai.adapters.windows.daemon_supervisor import WindowsDaemonSupervisorAdapter
        return WindowsDaemonSupervisorAdapter()
    raise NotImplementedError(
        f"detached daemon supervision is unsupported on {os.name!r}"
    )


def _maybe_register_test_fake_llm() -> None:
    """Test-only seam: register a deterministic fake LLM adapter, if asked.

    A monkeypatch from the test process cannot cross the process boundary a
    detached supervisor runs in, so ``tests/test_daemon_detached_supervisor.py``
    instead sets ``LINGTAI_DAEMON_SUPERVISOR_TEST_FAKE_LLM=1`` (plus
    ``tests/`` on ``PYTHONPATH``) in the spawned subprocess's environment and
    this hook imports ``tests._fake_llm_adapter`` to register it into
    ``LLMService``'s adapter registry before the manifest's ``llm.provider``
    is ever constructed. A no-op (silently returns) when the env var is
    unset or the test module is not importable — production supervisor runs
    never set this variable, so this is inert outside the test suite.
    """
    import os

    if os.environ.get(_TEST_FAKE_LLM_ENV) != "1":
        return
    try:
        import _fake_llm_adapter  # tests/_fake_llm_adapter.py, via test PYTHONPATH
    except ImportError:
        return
    _fake_llm_adapter.register()


def run_supervisor(
    request: "_port_mod.DaemonSupervisorRequest", *, capsule: dict | None = None
) -> None:
    """Entry point called by the POSIX entrypoint module after decode.

    Any exception escaping this function is caught here (never propagated to
    the interpreter's default traceback-to-stderr, since stderr is DEVNULL
    for a detached supervisor and would be silently lost anyway) and — on a
    best-effort basis — recorded onto the run's ``daemon.json`` so a fresh
    manager's startup reconciliation can classify the run as failed/lost
    rather than leaving it stuck at ``running`` forever with no explanation.
    """
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    _maybe_register_test_fake_llm()

    manifest_path = Path(request.manifest_path)
    try:
        manifest = read_manifest(manifest_path)
    except Exception:
        # Nothing durable to attach an error to — the manifest itself is
        # unreadable. There is no run_dir path we can trust here.
        return

    run_dir_path = Path(manifest["run_dir"])
    try:
        run_dir = DaemonRunDir.attach(run_dir_path)
    except Exception:
        return

    if request.run_id != manifest.get("run_id"):
        run_dir.update_state(owner="supervisor", supervisor_pid=os.getpid())
        run_dir.mark_failed(ValueError("supervisor request/manifest run_id mismatch"))
        _publish_terminal_notification_if_needed(run_dir, manifest)
        return

    canonical_manifest = manifest_path.resolve()
    canonical_run = Path(manifest["run_dir"]).resolve()
    if canonical_manifest.parent != canonical_run or canonical_run != run_dir_path.resolve():
        run_dir.mark_failed(ValueError("supervisor manifest/run directory identity mismatch"))
        _publish_terminal_notification_if_needed(run_dir, manifest)
        return
    run_dir.update_state(
        owner="supervisor", supervisor_pid=os.getpid(),
        supervisor_start_identity=_process_start_identity(os.getpid()),
        supervisor_manifest_path=str(canonical_manifest),
    )

    try:
        _run_one_emanation(run_dir, manifest, capsule or {})
    except Exception as e:
        # A bug in the supervisor itself (not a normal task-loop exception —
        # those are already caught and committed as `failed` inside
        # `_run_one_emanation`'s try/except around the tool loop). Commit a
        # terminal `failed` state here too so the run is never left silently
        # stuck at `running` with a dead supervisor and no explanation.
        try:
            run_dir.mark_failed(e)
        except Exception:
            pass
    finally:
        _publish_terminal_notification_if_needed(run_dir, manifest)


def run_resume_owner(manifest_path: str, run_id: str, generation: str,
                     capsule: dict | None = None) -> None:
    """Own one bounded post-terminal CLI resume generation."""
    from lingtai.tools.daemon.run_dir import DaemonRunDir
    from lingtai.kernel.daemon_supervisor.manifest import read_manifest

    manifest = read_manifest(Path(manifest_path))
    if manifest.get("run_id") != run_id:
        return
    run_dir = DaemonRunDir.attach(Path(manifest["run_dir"]))
    state = run_dir.read_state_from_disk(run_dir.path)
    if state.get("run_id") != run_id or state.get("backend") in {"qwen-code", "kimicode"}:
        return
    claim = state.get("resume_claim")
    if not isinstance(claim, dict) or claim.get("generation") != generation:
        return
    nonce = (capsule or {}).get("claim_nonce")
    owner_identity = _process_start_identity(os.getpid())
    if not run_dir.activate_resume_generation(generation, nonce):
        return
    try:
        run_dir.update_state(execution_registration="spawned")
        child = select_daemon_supervisor_adapter().spawn_execution_child(
            python_executable=sys.executable,
            manifest_path=str(Path(manifest_path).resolve()),
            run_id=run_id, run_dir=run_dir.path,
            capsule=capsule or {}, mode="resume", generation=generation,
        )
        if run_dir.read_state_from_disk(run_dir.path).get("execution_registration") != "registered":
            run_dir.update_state(
                execution_registration="spawned", execution_pid=child.pid,
                execution_pgid=_process_group_of(child.pid),
                execution_start_identity=_process_start_identity(child.pid),
            )
        deadline = time.monotonic() + float(manifest.get("timeout_s", 30)) + 5.0
        while child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if child.poll() is None:
            run_dir.record_followup(generation, status="timeout", error="detached resume timed out")
            _terminate_exact_run_children(run_dir, owned_procs=(child,))
        else:
            state = run_dir.read_state_from_disk(run_dir.path)
            if state.get("followup_generation") != generation:
                run_dir.record_followup(
                    generation, status="failed",
                    error=f"resume child exited before receipt (returncode={child.returncode})",
                )
        try:
            child.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            _terminate_exact_run_children(run_dir, owned_procs=(child,))
    except Exception as exc:
        run_dir.record_followup(generation, status="failed", error=f"{type(exc).__name__}: {exc}")
    finally:
        state = run_dir.read_state_from_disk(run_dir.path)
        status = state.get("followup_status") or "failed"
        run_dir.release_resume_generation(
            generation, nonce, owner_pid=os.getpid(),
            owner_identity=owner_identity, result_status=status,
        )
        try:
            state = run_dir.read_state_from_disk(run_dir.path)
            _publish_daemon_notification(
                run_dir, manifest, status=f"follow-up {status}",
                state=state, idempotency_key=f"daemon-followup:{run_id}:{generation}",
            )
        except Exception:
            pass


def _run_one_emanation(
    run_dir, manifest: dict, capsule: dict | None = None
) -> None:
    backend = manifest["backend"]
    cancel_event = threading.Event()
    timeout_event = threading.Event()
    deadline = time.monotonic() + float(manifest["timeout_s"])

    # Never fork after creating watcher threads.  The supervisor launches a
    # fresh interpreter child, which is the only process allowed to construct
    # the manager-shaped execution host and enter provider/CLI code.
    run_dir.update_state(execution_registration="spawned")
    child = select_daemon_supervisor_adapter().spawn_execution_child(
        python_executable=sys.executable,
        manifest_path=str(Path(manifest["run_dir"]) / "supervisor_manifest.json"),
        run_id=run_dir.run_id,
        run_dir=run_dir.path,
        capsule=capsule or {},
    )
    if run_dir.read_state_from_disk(run_dir.path).get("execution_registration") != "registered":
        run_dir.update_state(
            execution_registration="spawned",
            execution_pid=child.pid,
            execution_pgid=_process_group_of(child.pid),
            execution_start_identity=_process_start_identity(child.pid),
        )
    registration_deadline = time.monotonic() + 5.0
    while time.monotonic() < registration_deadline:
        state = run_dir.read_state_from_disk(run_dir.path)
        if state.get("execution_registration") == "registered":
            break
        if child.poll() is not None:
            break
        time.sleep(0.02)
    state = run_dir.read_state_from_disk(run_dir.path)
    if state.get("execution_registration") != "registered":
        if state.get("state") not in {"done", "failed", "cancelled", "timeout"}:
            run_dir.mark_failed(RuntimeError("execution child did not register within 5 seconds"))
        _terminate_exact_run_children(run_dir, owned_procs=(child,))
        child.wait(timeout=3.0)
        return

    watcher = threading.Thread(
        target=_control_and_deadline_watcher,
        args=(run_dir, cancel_event, timeout_event, deadline, (child,)),
        daemon=True,
    )
    watcher.start()

    while child.poll() is None:
        time.sleep(0.05)
    cancel_event.set()
    state = run_dir.read_state_from_disk(run_dir.path)
    if state.get("state") not in {"done", "failed", "cancelled", "timeout"}:
        code = child.returncode
        run_dir.mark_failed(RuntimeError(
            f"execution child exited before terminal state (returncode={code})"
        ))


def _terminate_exact_run_children_windows(run_dir, owned_procs=()) -> None:
    """Windows reclaim: identity-guarded ``TerminateProcess`` of exact PIDs.

    Deliberately narrower than the POSIX ``killpg`` reclaim: only the exact
    recorded nested-CLI child and execution-child PIDs are terminated, each
    gated on its recorded start identity (fail-closed on ``None``/mismatch).
    Grandchildren those children spawned outside the execution Port's Job are
    NOT swept — there is no inherited process group to signal on Windows, and
    this supervisor holds no Job handle of its own. Documented as a residual
    in ``tools/daemon/CONTRACT.md``.
    """
    from lingtai.adapters.windows import _win32

    state = run_dir.read_state_from_disk(run_dir.path)
    rows: list[tuple[int, object]] = []
    pid = state.get("child_pid") or state.get("cli_pid")
    if isinstance(pid, int) and not isinstance(pid, bool):
        # The nested CLI is terminated first so the live execution child can
        # reap its exact subprocess before the execution interpreter itself is
        # terminated — same ordering rationale as the POSIX branch.
        rows.append((pid, state.get("child_start_identity")))
    execution_pid = state.get("execution_pid")
    if isinstance(execution_pid, int) and not isinstance(execution_pid, bool):
        rows.append((execution_pid, state.get("execution_start_identity")))
    owned_by_pid = {
        getattr(proc, "pid", None): proc for proc in owned_procs
        if isinstance(getattr(proc, "pid", None), int)
    }
    for pid, expected_identity in rows:
        if not isinstance(expected_identity, str) or not expected_identity:
            continue
        if not process_identity_matches(pid, expected_identity):
            continue
        owned = owned_by_pid.get(pid)
        if owned is not None and owned.poll() is not None:
            continue
        try:
            _win32.terminate_pid(pid)
        except OSError:
            continue
        if owned is not None:
            try:
                owned.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass


def _terminate_exact_run_children(run_dir, owned_procs=()) -> None:
    """Terminate only exact execution and nested CLI groups for this run."""
    if os.name == "nt":
        _terminate_exact_run_children_windows(run_dir, owned_procs)
        return
    state = run_dir.read_state_from_disk(run_dir.path)
    identities = []
    execution_pid = state.get("execution_pid")
    execution_pgid = state.get("execution_pgid")
    execution_identity = state.get("execution_start_identity")
    execution_identity_row = None
    if isinstance(execution_pid, int) and isinstance(execution_pgid, int):
        execution_identity_row = (execution_pid, execution_pgid, execution_identity)
    try:
        pid = state.get("child_pid") or state.get("cli_pid")
        pgid = state.get("child_pgid") or state.get("cli_pgid") or pid
        expected_identity = state.get("child_start_identity")
        if not isinstance(pid, int) or not isinstance(pgid, int):
            pass
        else:
            # A nested CLI is started with its own session.  Terminate it first
            # so the live execution child can reap its exact subprocess before
            # the supervisor terminates the execution interpreter itself.
            identities.append((pid, pgid, expected_identity))
    except (ProcessLookupError, PermissionError, OSError):
        return
    if execution_identity_row is not None:
        identities.append(execution_identity_row)
    owned_by_pid = {
        getattr(proc, "pid", None): proc for proc in owned_procs
        if isinstance(getattr(proc, "pid", None), int)
    }
    for pid, pgid, expected_identity in identities:
        try:
            if not isinstance(expected_identity, str) or not expected_identity:
                continue
            if os.getpgid(pid) != pgid:
                continue
            if not process_identity_matches(pid, expected_identity):
                continue
            owned = owned_by_pid.get(pid)
            if owned is not None and owned.poll() is not None:
                continue
            os.killpg(pgid, signal.SIGTERM)
            if owned is not None:
                try:
                    owned.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
                if owned.poll() is not None:
                    continue
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                try:
                    if process_identity_matches(pid, expected_identity) and os.getpgid(pid) == pgid:
                        os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
        except (ProcessLookupError, PermissionError, OSError):
            pass


def _control_and_deadline_watcher(
    run_dir, cancel_event, timeout_event, deadline: float, owned_procs=()
) -> None:
    """Background thread: enforce the deadline and drain reclaim/ask requests.

    Runs for the lifetime of the emanation. Setting ``cancel_event`` (either
    because a reclaim request arrived or the deadline passed) is observed by
    the lingtai tool loop between turns and by the codex CLI runner's
    poll loop, exactly like the in-process watchdog today.
    """
    run_path = run_dir.path
    while not cancel_event.is_set():
        now = time.monotonic()
        if now >= deadline:
            timeout_event.set()
            cancel_event.set()
            run_dir.mark_timeout()
            _terminate_exact_run_children(run_dir, owned_procs=owned_procs)
            return
        for req_path in control.pending_requests(run_path):
            try:
                req = control.read_request(req_path)
            except Exception:
                control.mark_request_done(req_path, {"status": "error", "error": "unreadable request"})
                continue
            if req.get("run_id") != run_dir.run_id:
                control.mark_request_done(req_path, {"status": "rejected", "error": "run_id mismatch"})
                continue
            current_state = run_dir.read_state_from_disk(run_dir.path).get("state")
            if current_state not in {"running", "active"}:
                control.mark_request_done(req_path, {"status": "rejected", "error": f"run is already {current_state!r}"})
                continue
            kind = req.get("kind")
            if kind == "reclaim":
                cancel_event.set()
                run_dir.mark_cancelled()
                _terminate_exact_run_children(run_dir, owned_procs=owned_procs)
                control.mark_request_done(req_path, {"status": "accepted"})
                return
            elif kind == "ask":
                message = req.get("message", "")
                if run_dir.enqueue_followup(message):
                    control.mark_request_done(req_path, {"status": "queued"})
                else:
                    control.mark_request_done(
                        req_path, {"status": "rejected", "error": "run is no longer active"}
                    )
            else:
                control.mark_request_done(req_path, {"status": "error", "error": f"unknown kind {kind!r}"})
        time.sleep(_CONTROL_POLL_INTERVAL_S)


_FOLLOWUP_LOCK = threading.Lock()
_FOLLOWUP_BUFFERS: dict[str, str] = {}


def _enqueue_followup(run_dir, message: str) -> None:
    if not message:
        return
    with _FOLLOWUP_LOCK:
        prior = _FOLLOWUP_BUFFERS.get(run_dir.run_id, "")
        _FOLLOWUP_BUFFERS[run_dir.run_id] = (prior + "\n" + message) if prior else message


def _drain_followup(run_dir) -> str | None:
    with _FOLLOWUP_LOCK:
        buf = _FOLLOWUP_BUFFERS.pop(run_dir.run_id, "")
    return buf or None


def _mark_cancelled_or_timeout(run_dir, timeout_event: threading.Event | None) -> str:
    if timeout_event is not None and timeout_event.is_set():
        run_dir.mark_timeout()
    else:
        run_dir.mark_cancelled()
    return "[cancelled]"


# ---------------------------------------------------------------------
# lingtai backend
# ---------------------------------------------------------------------

def _run_shared_backend(run_dir, manifest: dict, cancel_event, timeout_event) -> None:
    """Compatibility seam: compose the production host for direct callers."""
    from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
    if os.name == "posix":
        from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
        from lingtai.adapters.posix.interactive_terminal import PosixInteractiveTerminalAdapter
        process_port = PosixDaemonProcessPort(start_new_session=False)
        interactive_terminal_port = PosixInteractiveTerminalAdapter(
            start_new_session=False,
        )
    elif os.name == "nt":
        from lingtai.tools.daemon.process_port import DaemonProcessTerminationScope
        from lingtai.tools.daemon.windows_process import WindowsDaemonProcessPort
        process_port = WindowsDaemonProcessPort(
            termination_scope=DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP,
        )
        # ConPTY is out of scope: no interactive terminal port exists on
        # Windows and the claude-interactive bridge refuses None loudly.
        interactive_terminal_port = None
    else:
        raise RuntimeError(
            "detached POSIX execution composition is unsupported on this platform"
        )
    host = DetachedDaemonExecutionHost(
        run_dir, manifest, cancel_event, timeout_event,
        process_port=process_port,
        interactive_terminal_port=interactive_terminal_port,
    )
    host.run_with_events(cancel_event, timeout_event)


# ---------------------------------------------------------------------
# Terminal notification — published directly by this process
# ---------------------------------------------------------------------

def _publish_terminal_notification_if_needed(run_dir, manifest: dict) -> None:
    try:
        state = run_dir.read_state_from_disk(run_dir.path)
    except (OSError, json.JSONDecodeError, ValueError):
        state = run_dir.state_snapshot()
    status = state.get("state")
    if status not in {"done", "failed", "cancelled", "timeout"}:
        return
    idempotency_key = run_dir.claim_terminal_notification(status)
    if idempotency_key is None:
        return  # already claimed/published by a concurrent path
    published = _publish_daemon_notification(run_dir, manifest, status=status, state=state, idempotency_key=idempotency_key)
    if published:
        run_dir.mark_terminal_notification_published(idempotency_key)
    else:
        run_dir.clear_terminal_notification_claim()


_NOTIFICATION_PREVIEW_MAX = 500


def _publish_daemon_notification(run_dir, manifest: dict, *, status: str, state: dict, idempotency_key: str) -> bool:
    """Publish the terminal event directly via the notification store Port.

    Mirrors ``DaemonManager._publish_daemon_notification``'s body/text
    shape and ``_enqueue_system_notification``'s payload mutator so
    ``.notification/system.json`` looks identical regardless of whether the
    in-process callback or this detached supervisor produced it — the
    fresh-manager reconciliation path and the wire's rendering both already
    depend on that shape.
    """
    from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
    from lingtai.kernel.notification_store import UNCONDITIONAL

    result_path = (
        state.get("followup_result_path")
        if str(status).startswith("follow-up")
        else state.get("result_path")
    )
    text = ""
    if isinstance(result_path, str) and result_path:
        try:
            with open(result_path, encoding="utf-8") as f:
                text = f.read(2000)
        except (OSError, UnicodeDecodeError):
            pass
    if not text:
        text = state.get("result_preview") or state.get("last_output") or ""
        error = state.get("error")
        if not text and isinstance(error, dict):
            text = error.get("message") or ""

    preview = text or ""
    if len(preview) > _NOTIFICATION_PREVIEW_MAX:
        preview = preview[:_NOTIFICATION_PREVIEW_MAX] + f"...[truncated; {len(preview)} chars total]"

    em_id = run_dir.handle
    parts = [
        f"Daemon {em_id} {status}.",
        f"Inspect with daemon(action=\"check\", id=\"{em_id}\").",
    ]
    task = (state.get("task") or "").strip()
    if task:
        if len(task) > _NOTIFICATION_PREVIEW_MAX:
            task = task[:_NOTIFICATION_PREVIEW_MAX] + "..."
        parts.append(f"Task: {task}")
    parts.append(f"Run directory: {run_dir.path}")
    if result_path:
        parts.append(f"Result file: {result_path}")
    error = state.get("error")
    if error:
        err_type = error.get("type", "error")
        err_msg = (error.get("message") or "")[:_NOTIFICATION_PREVIEW_MAX]
        parts.append(f"Error: {err_type}: {err_msg}".rstrip(": "))
    if preview:
        parts.append(f"Preview:\n{preview}")
    body = "\n".join(parts)

    store = PosixNotificationStoreAdapter(Path(manifest["parent_working_dir"]))

    import secrets
    from datetime import datetime, timezone

    event_id = f"evt_{int(time.time()*1000):x}_{secrets.token_hex(8)}"
    received_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _mutator(current_payload: dict):
        current = current_payload if isinstance(current_payload, dict) else {}
        events = list(current.get("data", {}).get("events", []))
        for ev in events:
            if ev.get("idempotency_key") == idempotency_key:
                # The sink may have accepted the event before the receipt
                # write completed. Treat an existing idempotency key as a
                # successful receipt recovery, never as a publish failure.
                return current_payload, False, idempotency_key
        event = {
            "event_id": event_id,
            "source": "daemon",
            "ref_id": em_id,
            "body": body,
            "at": received_at,
            "idempotency_key": idempotency_key,
        }
        events.append(event)
        events = events[-20:]
        envelope_priority = (
            "high" if any(
                isinstance(ev, dict) and (ev.get("severity") == "high" or ev.get("priority") == "high")
                for ev in events
            ) else "normal"
        )
        payload = {
            "header": f"{len(events)} system notification{'s' if len(events) != 1 else ''}",
            "icon": "\U0001f514",
            "priority": envelope_priority,
            "published_at": received_at,
            "data": {"events": events},
        }
        return payload, True, event_id

    try:
        result = store.compare_update_channel("system", UNCONDITIONAL, _mutator)
    except Exception:
        return False
    applied_event_id = result.value if isinstance(result.value, str) else ""
    return bool(applied_event_id)


__all__ = ["run_supervisor", "select_daemon_supervisor_adapter"]
