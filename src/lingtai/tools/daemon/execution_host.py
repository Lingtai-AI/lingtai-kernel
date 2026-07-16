"""Detached composition root for the production daemon execution units.

This module intentionally lives outside ``kernel.daemon_supervisor``.  The
kernel Port owns only the request/manifest contract; this composition layer may
instantiate ``DaemonManager``'s existing setup and backend runners inside the
supervisor process.  There is one parser/runner implementation for each
backend, shared by the parent-era manager and detached ownership.
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from lingtai.kernel.daemon_supervisor.agent_stub import DaemonSupervisorAgentStub
from lingtai.kernel.llm.base import FunctionSchema
from lingtai.adapters.posix.process_identity import (
    process_identity,
    process_identity_matches,
)
from lingtai.tools.daemon.process_port import (
    DaemonProcessObservation,
    DaemonProcessTerminationScope,
)


_DAEMON_COMMON_NAME = "daemon_common"
_REDACTED_MARKER = "<redacted>"


def _contains_redacted(value) -> bool:
    """Detect a public redaction marker that must never reach a runner."""
    if isinstance(value, str):
        return value == _REDACTED_MARKER
    if isinstance(value, dict):
        return any(_contains_redacted(k) or _contains_redacted(v) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_redacted(item) for item in value)
    return False


class DetachedDaemonExecutionHost:
    """Small manager-shaped host used only inside one supervisor process."""

    def __init__(
        self, run_dir, manifest: dict, cancel_event, timeout_event,
        *, capsule: dict | None = None, process_port=None,
        interactive_terminal_port=None,
    ) -> None:
        from lingtai.tools.daemon import DaemonManager

        self._run_dir = run_dir
        # Keep the owner events available to the Port observation callback even
        # during construction-time/direct composition tests; run_with_events
        # and run_resume replace these with their active loop events.
        self._cancel_event = cancel_event
        self._timeout_event = timeout_event
        self._manifest = manifest
        self._capsule = capsule if isinstance(capsule, dict) else {}
        self._agent = DaemonSupervisorAgentStub(
            Path(manifest["parent_working_dir"]),
            log_fn=lambda event, **fields: run_dir.append_event(event, **fields),
        )
        self._agent._config.language = manifest.get("language", "en") or "en"
        self._agent.service = SimpleNamespace(
            model=(manifest.get("llm") or {}).get("model", "unknown"),
        )
        self._agent._mcp_tool_names = set()
        self._max_turns = int(manifest["max_turns"])
        self._timeout = float(manifest["timeout_s"])
        self._default_model = self._agent.service.model
        self._notify_threshold = 20
        self._emanations = {}
        self._pools = []
        self._ask_pool = None
        self._cli_procs = []
        self._cli_proc_groups = {}
        self._cli_term_reasons = {}
        self._cli_lock = threading.Lock()

        # Detached execution already owns the session/process group. Compose
        # adapters with inheritance enabled and bind the immutable observation
        # callback before any backend runner can spawn a child. The callback is
        # deliberately adapter-facing: Core sees no Popen object, only a
        # portable PID/PGID/start-identity receipt.
        if process_port is None:
            if os.name != "posix":
                raise RuntimeError(
                    "detached POSIX execution composition is unsupported on this platform"
                )
            from lingtai.tools.daemon.posix_process import PosixDaemonProcessPort
            process_port = PosixDaemonProcessPort(start_new_session=False)
        if interactive_terminal_port is None and os.name == "posix":
            from lingtai.adapters.posix.interactive_terminal import (
                PosixInteractiveTerminalAdapter,
            )
            interactive_terminal_port = PosixInteractiveTerminalAdapter(
                start_new_session=False,
            )
        self._process_port = process_port
        self._interactive_terminal_port = interactive_terminal_port
        for port in (self._process_port, self._interactive_terminal_port):
            setter = getattr(port, "set_observation_callback", None)
            if callable(setter):
                setter(self._publish_process_observation)

        # Reconstruct the ordinary host tool floor through the same registry
        # setup used by the parent manager.  No full Agent/workdir lease is
        # constructed in this process.
        from lingtai.tools.daemon import EMANATION_BLACKLIST, _ToolCollector
        from lingtai.tools.registry import BUILTIN_TOOLS, canonical_capability_name, setup_capability
        collector = _ToolCollector(self._agent)
        names = set()
        for name in manifest.get("tools", []):
            name = canonical_capability_name(name)
            if name in EMANATION_BLACKLIST:
                continue
            names.add(name)
        from lingtai.tools.registry import _GROUPS
        expanded = set()
        for name in names:
            expanded.update(_GROUPS.get(name, [name]))
        if expanded.intersection(_GROUPS.get("file", ())):
            # File handlers dereference the agent-shaped host's injected
            # FileIOService at execution time. Mirror ordinary Agent
            # construction, but only when the detached tool surface needs it;
            # this remains a small host service, not a second Agent/lease.
            from lingtai.services.file_io_sidecar import default_file_io_service
            self._agent._file_io = default_file_io_service(
                root=self._agent._working_dir,
            )
        for name in sorted(expanded):
            if name in BUILTIN_TOOLS:
                setup_capability(collector, name)
        self._agent._tool_schemas = list(collector.schemas.values())
        self._agent._tool_handlers = dict(collector.handlers)

        # The manager methods are used as unbound production units below.  A
        # host does not need DaemonManager.__init__, which would create a
        # parent-owned ask executor and perform parent-record reconciliation.
        self._manager_type = DaemonManager
        self._task_mcp_clients: list[object] = []

    def __getattr__(self, name):
        """Forward unmodified parser/helper units to the production manager."""
        manager = self.__dict__.get("_manager_type")
        if manager is not None:
            attr = getattr(manager, name, None)
            if callable(attr):
                raw = manager.__dict__.get(name)
                if isinstance(raw, staticmethod):
                    return attr
                return lambda *args, **kwargs: attr(self, *args, **kwargs)
            if attr is not None:
                return attr
        raise AttributeError(name)

    def _log(self, event_type: str, **fields) -> None:
        try:
            self._run_dir.append_event(event_type, **fields)
        except Exception:
            pass

    def _completion_surface(self) -> tuple[dict[str, FunctionSchema], dict]:
        from lingtai.mcp_servers.daemon_common.server import DESCRIPTION, FINISH_SCHEMA, _validate_finish
        from lingtai.kernel._fsutil import atomic_write_json

        path = self._run_dir.path / "daemon_completion.json"

        def finish(arguments: dict) -> dict:
            payload = _validate_finish(arguments or {})
            payload["run_id"] = self._run_dir.run_id
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
            try:
                path.chmod(0o600)
            except OSError:
                pass
            return {"status": "ok", "completion_status": payload["status"]}

        return {
            "finish": FunctionSchema(
                name="finish", description=DESCRIPTION,
                parameters=FINISH_SCHEMA,
            )
        }, {"finish": finish}

    # Explicit forwarding keeps the production runner methods unmodified while
    # preventing this host from becoming a second backend implementation.
    def _expand_requested_tools(self, requested):
        return self._manager_type._expand_requested_tools(self, requested)

    def _parent_mcp_tool_names(self):
        return self._manager_type._parent_mcp_tool_names(self)

    def _daemon_intrinsic_surface(self):
        return self._manager_type._daemon_intrinsic_surface(self)

    def _daemon_provider_defaults(self, *args, **kwargs):
        return self._manager_type._daemon_provider_defaults(self, *args, **kwargs)

    def _llm_defaults_from_manifest(self, llm):
        return self._manager_type._llm_defaults_from_manifest(llm)

    def _require_done_completion(self, run_dir, final_text):
        return self._manager_type._require_done_completion(self, run_dir, final_text)

    def _fail_missing_or_bad_completion(self, run_dir, completion, final_text):
        return self._manager_type._fail_missing_or_bad_completion(
            self, run_dir, completion, final_text
        )

    def _read_daemon_completion(self, run_dir):
        return self._manager_type._read_daemon_completion(run_dir)

    def _run_has_daemon_common_mcp(self, run_dir):
        return self._manager_type._run_has_daemon_common_mcp(run_dir)

    def _close_task_mcp_clients(self, clients):
        return self._manager_type._close_task_mcp_clients(clients)

    def _cli_start_new_session(self) -> bool:
        # The execution child already owns a fresh session/process group. Keep
        # each CLI in that group from Popen onward so supervisor timeout/reclaim
        # covers the pre-registration window as well as the steady state.
        return False

    def _publish_process_observation(self, observation: DaemonProcessObservation) -> None:
        """Publish child identity before a runner performs any blocking I/O."""
        if not isinstance(observation, DaemonProcessObservation):
            raise TypeError("daemon process observation must be immutable and typed")
        pid = observation.pid
        pgid = observation.pgid
        identity = observation.start_identity
        termination_scope = observation.termination_scope
        if termination_scope is not DaemonProcessTerminationScope.INHERITED_SUPERVISOR_GROUP:
            raise ValueError(
                "detached observation must use supervisor-owned inherited-group scope"
            )
        state = self._run_dir.read_state_from_disk(self._run_dir.path)
        history = list(state.get("child_history") or [])
        identity_row = {
            "pid": pid, "pgid": pgid, "start_identity": identity,
            "termination_scope": termination_scope.value,
            "registered_at": self._run_dir._now_iso(),
        }
        history.append(identity_row)
        self._run_dir.update_state(
            cli_pid=pid, cli_pgid=pgid,
            child_pid=pid, child_pgid=pgid,
            child_start_identity=identity,
            child_termination_scope=termination_scope.value,
            child_registered_at=identity_row["registered_at"],
            child_history=history[-32:],
        )
        # Close the cancellation-before-registration race. Identity and group
        # checks are repeated immediately before signalling so PID reuse cannot
        # turn a stale callback into ownership of an unrelated process.
        cancel_event = getattr(self, "_cancel_event", None)
        if cancel_event is not None and cancel_event.is_set():
            try:
                if (
                    isinstance(pgid, int)
                    and isinstance(identity, str)
                    and os.getpgid(pid) == pgid
                    and process_identity_matches(pid, identity)
                ):
                    os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def _register_cli_proc(self, proc, group_id=None):
        # Legacy direct-Popen callers still use this manager-shaped hook; the
        # migrated Port runners use _publish_process_observation above.
        # This callback is invoked immediately after every production runner
        # creates a child and before its first blocking stdout/stderr read.
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            try:
                pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError, OSError):
                pgid = None
            identity = process_identity(pid)
            state = self._run_dir.read_state_from_disk(self._run_dir.path)
            history = list(state.get("child_history") or [])
            identity_row = {
                "pid": pid, "pgid": pgid, "start_identity": identity,
                "registered_at": self._run_dir._now_iso(),
            }
            history.append(identity_row)
            self._run_dir.update_state(
                cli_pid=pid, cli_pgid=pgid,
                child_pid=pid, child_pgid=pgid,
                child_start_identity=identity,
                child_registered_at=identity_row["registered_at"],
                child_history=history[-32:],
            )
            # Close the watcher-before-registration race.  Only this exact
            # newly created process group is touched.
            if self._cancel_event.is_set():
                try:
                    if (
                        isinstance(pgid, int)
                        and isinstance(identity, str)
                        and os.getpgid(pid) == pgid
                        and process_identity_matches(pid, identity)
                    ):
                        os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        return self._manager_type._register_cli_proc(self, proc, group_id)

    def _unregister_cli_proc(self, proc, group_id=None):
        return self._manager_type._unregister_cli_proc(self, proc, group_id)

    def _attributed_cli_exit(self, proc, backend_name, detail, run_dir=None):
        return self._manager_type._attributed_cli_exit(self, proc, backend_name, detail, run_dir)

    def _take_cli_term_reason(self, proc):
        return self._manager_type._take_cli_term_reason(self, proc)

    @staticmethod
    def _signal_exit_name(returncode):
        from lingtai.tools.daemon import DaemonManager
        return DaemonManager._signal_exit_name(returncode)

    def _drain_followup(self, em_id):
        # Follow-ups are persisted before their control request is acked.  The
        # shared LingTai runner consumes them only at a text-only boundary.
        return self._run_dir.drain_followups()

    def _build_lingtai_surface(self) -> tuple[list[FunctionSchema], dict]:
        manager = self._manager_type
        requested = list(self._manifest.get("tools") or [])
        preset_surface = None
        caps = self._manifest.get("preset_capabilities")
        preset_llm = self._manifest.get("preset_llm") or self._manifest.get("llm") or {}
        if isinstance(caps, dict):
            preset_surface = manager._instantiate_preset_capabilities(
                self, caps, preset_llm,
                required_tools=manager._expand_requested_tools(self, requested),
            )

        common_schemas, common_handlers = self._completion_surface()
        mcp_schemas = dict(common_schemas)
        mcp_handlers = dict(common_handlers)
        source_mcp = self._capsule.get("mcp")
        if not isinstance(source_mcp, list):
            source_mcp = self._manifest.get("mcp") or []
        registrations = [
            reg for reg in source_mcp
            if isinstance(reg, dict) and reg.get("name") != _DAEMON_COMMON_NAME
        ]
        if registrations:
            # Values in this list are the manifest-safe registrations.  Secret
            # env/header values are never available here; the owner can use the
            # inherited environment or native config instead.
            schemas, handlers, clients = manager._connect_task_mcp_registrations(
                self, registrations,
            )
            mcp_schemas.update(schemas)
            mcp_handlers.update(handlers)
            self._task_mcp_clients.extend(clients)
        return manager._build_tool_surface(
            self, requested, preset_surface=preset_surface,
            mcp_surface=(mcp_schemas, mcp_handlers),
        )

    def _rehydrate_native_mcp_files(self) -> None:
        """Write unavoidable run-private native files only inside the owner."""
        if not self._capsule.get("mcp"):
            return
        regs = [
            r for r in self._capsule.get("mcp", [])
            if isinstance(r, dict) and r.get("name") != _DAEMON_COMMON_NAME
        ]
        argv = self._manifest.get("backend_argv") or []
        if not isinstance(argv, list):
            return
        from lingtai.kernel._fsutil import atomic_write_json

        def write(path: Path, payload: dict) -> None:
            atomic_write_json(path, payload, ensure_ascii=False, indent=2)
            try:
                path.chmod(0o600)
            except OSError:
                pass

        backend = self._manifest.get("backend")
        if backend in {"claude-p", "claude-code"} and "--mcp-config" in argv:
            idx = argv.index("--mcp-config")
            if idx + 1 < len(argv):
                servers = {
                    r["name"]: {"command": r["command"], "args": list(r.get("args") or []), **(
                        {"env": dict(r["env"])} if r.get("env") else {}
                    )}
                    for r in regs if r.get("transport", "stdio") == "stdio" and r.get("command")
                }
                write(Path(argv[idx + 1]), {"mcpServers": servers})
        elif backend == "qwen-code" and "__lingtai_qwen_system_settings_path" in argv:
            idx = argv.index("__lingtai_qwen_system_settings_path")
            if idx + 1 < len(argv):
                servers = {
                    r["name"]: {"command": r["command"], "args": list(r.get("args") or []), "env": dict(r.get("env") or {})}
                    for r in regs if r.get("transport", "stdio") == "stdio" and r.get("command")
                }
                write(Path(argv[idx + 1]), {"mcpServers": servers})

    def run(self) -> str | None:
        from lingtai.tools.daemon import _backend_spec

        # Overlay runtime-only values after the capsule has been consumed.  No
        # overlay is written back to daemon.json or the manifest.
        runtime_task = self._capsule.get("task")
        if isinstance(runtime_task, str):
            self._manifest = dict(self._manifest, task=runtime_task)
        runtime_mcp = self._capsule.get("mcp")
        if isinstance(runtime_mcp, list):
            # The public manifest intentionally contains redacted env/header
            # values.  Overlay the owner-only registration copy before the
            # runtime marker gate and before MCP client construction.
            self._manifest = dict(self._manifest, mcp=list(runtime_mcp))
        runtime_llm = self._capsule.get("llm")
        if isinstance(runtime_llm, dict) and isinstance(self._manifest.get("llm"), dict):
            llm = dict(self._manifest["llm"])
            llm.update(runtime_llm)
            self._manifest = dict(self._manifest, llm=llm)
            if isinstance(self._manifest.get("preset_llm"), dict):
                preset = dict(self._manifest["preset_llm"])
                preset.update(runtime_llm)
                self._manifest = dict(self._manifest, preset_llm=preset)
        runtime_argv = self._capsule.get("backend_argv")
        if isinstance(runtime_argv, list) and all(isinstance(v, str) for v in runtime_argv):
            self._manifest = dict(self._manifest, backend_argv=list(runtime_argv))

        # A redaction marker is suitable for public prompt/state metadata only;
        # silently handing it to a provider/MCP/CLI is both incorrect and a
        # potential credential-corruption footgun.  The manager-created path
        # overlays the real values from the one-shot capsule before this gate.
        for field in ("llm", "mcp", "backend_argv"):
            if _contains_redacted(self._manifest.get(field)):
                raise ValueError(
                    f"runtime field {field!r} still contains a redaction marker; "
                    "the detached secret capsule was not supplied"
                )

        backend = self._manifest["backend"]
        if backend == "lingtai":
            schemas, dispatch = self._build_lingtai_surface()
            preset_llm = self._manifest.get("preset_llm") or self._manifest.get("llm")
            try:
                return self._manager_type._run_emanation(
                    self,
                    self._run_dir.handle,
                    self._run_dir,
                    schemas,
                    dispatch,
                    self._manifest["task"],
                    self._cancel_event,
                    self._timeout_event,
                    preset_llm,
                    self._max_turns,
                    self._task_mcp_clients,
                    self._manifest.get("context_token_limit"),
                )
            finally:
                self._task_mcp_clients = []

        self._rehydrate_native_mcp_files()
        spec = _backend_spec(backend)
        if spec is None or not spec.runner_attr:
            raise ValueError(f"unknown detached backend {backend!r}")
        runner = getattr(self._manager_type, spec.runner_attr)
        return runner(
            self,
            self._run_dir.handle,
            self._run_dir,
            self._manifest["task"],
            self._cancel_event,
            self._timeout_event,
            list(self._manifest.get("backend_argv") or []),
        )

    def run_with_events(self, cancel_event, timeout_event) -> str | None:
        self._cancel_event = cancel_event
        self._timeout_event = timeout_event
        return self.run()

    def run_resume(self, generation: str) -> dict:
        """Run one supported CLI resume using the production stream parser.

        The detached child owns the local executor only as an implementation
        detail; no parent manager future or process handle crosses this seam.
        Backend-specific command/parser policy remains in ``DaemonManager``.
        """
        from lingtai.tools.daemon import _backend_spec
        backend = self._manifest.get("backend")
        spec = _backend_spec(backend)
        if spec is None or not spec.is_cli or spec.ask_handler_attr is None:
            raise ValueError(
                f"detached resume is unsupported for backend {backend!r}"
            )
        state = self._run_dir.read_state_from_disk(self._run_dir.path)
        entry = {
            "detached": True, "run_dir": self._run_dir,
            "backend": backend, "task": state.get("task", ""),
            "followup_lock": threading.Lock(), "ask_in_flight": False,
            "ask_future": None, "cancel_event": threading.Event(),
        }
        self._cancel_event = entry["cancel_event"]
        self._timeout_event = threading.Event()
        self._emanations[self._run_dir.handle] = entry
        self._ask_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="daemon-detached-resume")
        message = self._capsule.get("message")
        if not isinstance(message, str) or not message:
            raise ValueError("detached resume message was not supplied through the one-shot capsule")
        try:
            handler = getattr(self._manager_type, spec.ask_handler_attr)
            response = handler(self, self._run_dir.handle, entry, message)
            if response.get("status") == "busy":
                return response
            future = entry.get("ask_future")
            if future is None:
                if response.get("status") == "error":
                    self._run_dir.record_followup(
                        generation, status="failed", error=response.get("message", "resume failed")
                    )
                return response
            result = future.result(timeout=float(self._manifest.get("timeout_s", 30)) + 5.0)
            if result.get("status") == "sent":
                self._run_dir.record_followup(
                    generation, status="done", output=result.get("output", "")
                )
            else:
                self._run_dir.record_followup(
                    generation, status="failed", error=result.get("message", "resume failed")
                )
            return result
        except Exception as exc:
            self._run_dir.record_followup(
                generation, status="failed", error=f"{type(exc).__name__}: {exc}"
            )
            raise
        finally:
            self._ask_pool.shutdown(wait=False, cancel_futures=True)


__all__ = ["DetachedDaemonExecutionHost"]
