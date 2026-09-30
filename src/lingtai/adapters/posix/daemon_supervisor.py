"""POSIX detached-process adapter for the daemon-run supervisor.

``PosixDaemonSupervisorAdapter`` implements the Core-owned
``lingtai.kernel.daemon_supervisor.DaemonSupervisorPort`` by encoding a
``DaemonSupervisorRequest`` to its compact deterministic JSON wire form and
launching a new interpreter subprocess against the owned entrypoint module
``lingtai.adapters.posix.daemon_supervisor_entrypoint``, detached into its own
POSIX session so it survives the caller's exit. It is the only production
adapter; Core never constructs it. Mirrors
``lingtai.adapters.posix.refresh_watcher.PosixRefreshWatcherAdapter`` — same
detachment mechanics, different owned entrypoint and request shape.

The concrete interpreter path, the ``-m`` module invocation, stdio
detachment to ``DEVNULL``, and ``start_new_session=True`` (POSIX
process-group detachment, not available on Windows) all live here — they are
the concrete process mechanism, not part of the technology-neutral Port.
"""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
from pathlib import Path

from lingtai.adapters.posix.daemon_capsule import (
    CAPSULE_FD_ENV,
    MAX_CAPSULE_BYTES,
    close_fd,
    encode_capsule,
    send_capsule,
)


# A capsule is intentionally bounded before Popen.  It is a one-shot transport,
# not an unbounded stream or a durable configuration channel.
_MAX_CAPSULE_BYTES = MAX_CAPSULE_BYTES
_SECRET_ENV_NAME_RE = re.compile(
    r"(?:api[_-]?key|token|password|secret|authorization|cookie|private[_-]?key|credential|passphrase)",
    re.IGNORECASE,
)

# Claude-family runners are deliberately absent: an inherited Claude credential
# must never be restored into their child.  Their only credential is the
# setup-token the daemon resolves under the shared claude-code auth policy
# (``DaemonManager._claude_cli_credential_env`` / ``lingtai.llm.claude_code.auth``),
# added to the capsule's ``credential_env`` explicitly.
_CLI_CREDENTIAL_ENV_NAMES = {
    "codex": {"OPENAI_API_KEY", "CODEX_API_KEY"},
    "opencode": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_API_KEY"},
    "mimocode": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MIMO_API_KEY"},
    "oh-my-pi": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"},
    "qwen-code": {"DASHSCOPE_API_KEY", "QWEN_API_KEY", "OPENAI_API_KEY"},
    "kimicode": {"KIMI_API_KEY", "KIMICODE_API_KEY", "MOONSHOT_API_KEY"},
    "deepseek": {"DEEPSEEK_API_KEY"},
    "cursor": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CURSOR_API_KEY"},
}


def selected_credential_environment(backend: str) -> dict[str, str]:
    """Select only known credentials needed by the final CLI child."""
    names = _CLI_CREDENTIAL_ENV_NAMES.get(backend, set())
    return {name: os.environ[name] for name in names if os.environ.get(name)}

from lingtai.kernel.daemon_supervisor import DaemonSupervisorPort, DaemonSupervisorRequest, encode_request


def _supervisor_environment(request: DaemonSupervisorRequest) -> dict[str, str]:
    """Build a diagnostic-capable environment without inheriting credentials.

    A detached supervisor must not expose the launching agent's environment via
    ``ps``/``/proc``.  Runtime credentials and task-MCP values cross the process
    boundary only in the one-shot capsule.  Keep ordinary interpreter/runtime
    variables (PATH, HOME, PYTHONPATH, test switches), but remove credential-
    shaped names and the exact ``api_key_env`` references recorded in the public
    manifest.  The latter matters for user-chosen names that do not contain a
    conventional secret word.
    """
    env = {
        key: value for key, value in os.environ.items()
        if not _SECRET_ENV_NAME_RE.search(key)
    }
    try:
        manifest = json.loads(Path(request.manifest_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        manifest = {}
    for block_name in ("llm", "preset_llm"):
        block = manifest.get(block_name) if isinstance(manifest, dict) else None
        if isinstance(block, dict):
            ref = block.get("api_key_env")
            if isinstance(ref, str):
                env.pop(ref, None)
    source_root = Path(__file__).resolve().parents[3]
    parts = [str(source_root)]
    parts.extend(p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env

ENTRYPOINT_MODULE = "lingtai.adapters.posix.daemon_supervisor_entrypoint"
EXECUTION_CHILD_MODULE = "lingtai.adapters.posix.daemon_execution_child_entrypoint"
RESUME_OWNER_MODULE = "lingtai.adapters.posix.daemon_resume_owner_entrypoint"


class PosixDaemonSupervisorAdapter(DaemonSupervisorPort):
    """Encode the request and spawn the supervisor as a detached POSIX subprocess.

    The launched process runs ``request.python_executable`` against the owned
    entrypoint module (``<python_executable> -m
    lingtai.adapters.posix.daemon_supervisor_entrypoint <encoded-request>``),
    with all three standard streams sent to ``DEVNULL`` and
    ``start_new_session=True`` so it is not a child of the caller's process
    group. The call returns immediately after ``Popen`` starts the process;
    it does not wait for or track the child.
    """

    def spawn_detached(
        self,
        request: DaemonSupervisorRequest,
        *,
        capsule: dict | None = None,
        adopted_fd: int | None = None,
    ) -> None:
        """Adopt a descriptor and launch one detached supervisor owner."""
        try:
            self._spawn_detached_owned(
                request,
                capsule=capsule,
                adopted_fd=adopted_fd,
            )
        finally:
            close_fd(adopted_fd)

    def _spawn_detached_owned(
        self,
        request: DaemonSupervisorRequest,
        *,
        capsule: dict | None,
        adopted_fd: int | None,
    ) -> None:
        """Launch one owner and optionally hand it one-shot runtime values.

        The capsule is encoded only in memory.  A socketpair is created before
        ``Popen`` but written after the child exists, so a large registration or
        option payload cannot block the launching manager before the supervisor
        has a reader.  ``pass_fds`` carries only the capsule socket.  An optional
        adopted descriptor crosses after spawn through ``SCM_RIGHTS``; this
        class's public spawn boundary closes the caller's descriptor on every
        return path.
        """
        payload = encode_request(request)
        run_dir = Path(request.manifest_path).resolve().parent
        stdout_path = run_dir / "supervisor.stdout.log"
        stderr_path = run_dir / "supervisor.stderr.log"
        capsule_bytes = None
        parent_socket = child_socket = None
        stdout = stderr = None
        try:
            if capsule or adopted_fd is not None:
                capsule_bytes = encode_capsule(capsule)
                parent_socket, child_socket = socket.socketpair()
            stdout = open(stdout_path, "ab", buffering=0)
            stderr = open(stderr_path, "ab", buffering=0)
            for path in (stdout_path, stderr_path):
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            env = _supervisor_environment(request)
            pass_fds = ()
            if child_socket is not None:
                env[CAPSULE_FD_ENV] = str(child_socket.fileno())
                pass_fds = (child_socket.fileno(),)
            subprocess.Popen(
                [request.python_executable, "-m", ENTRYPOINT_MODULE, payload],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=env,
                cwd=str(run_dir.parent.parent),
                start_new_session=True,
                close_fds=True,
                pass_fds=pass_fds,
            )
            if child_socket is not None:
                child_socket.close()
                child_socket = None
                send_capsule(
                    parent_socket,
                    capsule_bytes or b"{}",
                    adopted_fd=adopted_fd,
                )
        finally:
            for capsule_socket in (parent_socket, child_socket):
                if capsule_socket is not None:
                    capsule_socket.close()
            if stdout is not None:
                stdout.close()
            if stderr is not None:
                stderr.close()

    @staticmethod
    def _spawn_capsule_process(
        module: str, python_executable: str, args: list[str],
        *, run_dir: Path, capsule: dict | None = None,
        adopted_fd: int | None = None,
    ) -> subprocess.Popen:
        """Spawn an exact owner/child and adopt any live descriptor."""
        try:
            return PosixDaemonSupervisorAdapter._spawn_capsule_process_owned(
                module,
                python_executable,
                args,
                run_dir=run_dir,
                capsule=capsule,
                adopted_fd=adopted_fd,
            )
        finally:
            close_fd(adopted_fd)

    @staticmethod
    def _spawn_capsule_process_owned(
        module: str, python_executable: str, args: list[str],
        *, run_dir: Path, capsule: dict | None,
        adopted_fd: int | None,
    ) -> subprocess.Popen:
        capsule_bytes = None
        parent_socket = child_socket = None
        env = {
            key: value for key, value in os.environ.items()
            if not _SECRET_ENV_NAME_RE.search(key)
        }
        source_root = Path(__file__).resolve().parents[3]
        parts = [str(source_root)]
        parts.extend(p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p)
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
        stdout_path = run_dir / ("execution.stdout.log" if "execution_child" in module else "resume-owner.stdout.log")
        stderr_path = run_dir / ("execution.stderr.log" if "execution_child" in module else "resume-owner.stderr.log")
        stdout = stderr = None
        try:
            if capsule or adopted_fd is not None:
                capsule_bytes = encode_capsule(capsule)
                parent_socket, child_socket = socket.socketpair()
                env[CAPSULE_FD_ENV] = str(child_socket.fileno())
            stdout = open(stdout_path, "ab", buffering=0)
            stderr = open(stderr_path, "ab", buffering=0)
            for path in (stdout_path, stderr_path):
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            proc = subprocess.Popen(
                [python_executable, "-m", module, *args],
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                env=env, cwd=str(run_dir.parent.parent),
                start_new_session=True, close_fds=True,
                pass_fds=(child_socket.fileno(),) if child_socket is not None else (),
            )
            if child_socket is not None:
                child_socket.close()
                child_socket = None
                send_capsule(
                    parent_socket,
                    capsule_bytes or b"{}",
                    adopted_fd=adopted_fd,
                )
            return proc
        finally:
            for capsule_socket in (parent_socket, child_socket):
                if capsule_socket is not None:
                    capsule_socket.close()
            if stdout is not None:
                stdout.close()
            if stderr is not None:
                stderr.close()

    def spawn_execution_child(self, *, python_executable: str,
                               manifest_path: str, run_id: str,
                               run_dir: Path, capsule: dict | None = None,
                               adopted_fd: int | None = None,
                               mode: str = "emanation",
                               generation: str | None = None) -> subprocess.Popen:
        try:
            args = [str(manifest_path), run_id, mode]
            if generation:
                args.append(generation)
            return self._spawn_capsule_process_owned(
                EXECUTION_CHILD_MODULE, python_executable, args,
                run_dir=Path(run_dir), capsule=capsule, adopted_fd=adopted_fd,
            )
        finally:
            close_fd(adopted_fd)

    def spawn_resume_owner(self, *, python_executable: str,
                           manifest_path: str, run_id: str,
                           run_dir: Path, generation: str,
                           capsule: dict | None = None) -> subprocess.Popen:
        return self._spawn_capsule_process(
            RESUME_OWNER_MODULE, python_executable,
            [str(manifest_path), run_id, generation],
            run_dir=Path(run_dir), capsule=capsule,
        )


__all__ = [
    "PosixDaemonSupervisorAdapter", "ENTRYPOINT_MODULE", "EXECUTION_CHILD_MODULE",
    "RESUME_OWNER_MODULE", "selected_credential_environment",
]
