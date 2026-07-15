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
import subprocess
from pathlib import Path


# A capsule is intentionally bounded before Popen.  It is a one-shot transport,
# not an unbounded stream or a durable configuration channel.
_MAX_CAPSULE_BYTES = 4 * 1024 * 1024
_SECRET_ENV_NAME_RE = re.compile(
    r"(?:api[_-]?key|token|password|secret|authorization|cookie|private[_-]?key|credential|passphrase)",
    re.IGNORECASE,
)

# Claude-family runners are deliberately absent: their product contract strips
# ANTHROPIC_* and CLAUDE_CODE_OAUTH_TOKEN so the CLI uses its first-party stored
# subscription/OAuth state.  Detachment must not turn restored parent env into an
# override of that established runner policy.
_CLI_CREDENTIAL_ENV_NAMES = {
    "codex": {"OPENAI_API_KEY", "CODEX_API_KEY"},
    "opencode": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_API_KEY"},
    "mimocode": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MIMO_API_KEY"},
    "oh-my-pi": {"OPENAI_API_KEY", "ANTHROPIC_API_KEY"},
    "qwen-code": {"DASHSCOPE_API_KEY", "QWEN_API_KEY", "OPENAI_API_KEY"},
    "kimicode": {"KIMI_API_KEY", "KIMICODE_API_KEY", "MOONSHOT_API_KEY"},
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
        self, request: DaemonSupervisorRequest, *, capsule: dict | None = None
    ) -> None:
        """Launch one owner and optionally hand it one-shot runtime values.

        The capsule is encoded only in memory.  A pipe is created before
        ``Popen`` but written after the child exists, so a large registration or
        option payload cannot block the launching manager before the supervisor
        has a reader.  ``pass_fds`` is deliberate: the numeric descriptor is
        process metadata, while raw credentials cross only this inherited
        descriptor and are consumed once by the supervisor entrypoint.
        """
        payload = encode_request(request)
        run_dir = Path(request.manifest_path).resolve().parent
        stdout_path = run_dir / "supervisor.stdout.log"
        stderr_path = run_dir / "supervisor.stderr.log"
        stdout = open(stdout_path, "ab", buffering=0)
        stderr = open(stderr_path, "ab", buffering=0)
        read_fd = write_fd = None
        capsule_bytes = None
        if capsule:
            capsule_bytes = json.dumps(
                capsule, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(capsule_bytes) > _MAX_CAPSULE_BYTES:
                raise ValueError(
                    f"daemon runtime capsule exceeds {_MAX_CAPSULE_BYTES} bytes"
                )
            read_fd, write_fd = os.pipe()
        try:
            for path in (stdout_path, stderr_path):
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            env = _supervisor_environment(request)
            pass_fds = ()
            if read_fd is not None:
                env["LINGTAI_DAEMON_CAPSULE_FD"] = str(read_fd)
                pass_fds = (read_fd,)
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
            if read_fd is not None:
                os.close(read_fd)
                read_fd = None
                view = memoryview(capsule_bytes or b"")
                while view:
                    written = os.write(write_fd, view)
                    view = view[written:]
                os.close(write_fd)
                write_fd = None
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            stdout.close()
            stderr.close()

    @staticmethod
    def _spawn_capsule_process(
        module: str, python_executable: str, args: list[str],
        *, run_dir: Path, capsule: dict | None = None,
    ) -> subprocess.Popen:
        """Spawn an exact owner/child with the same bounded one-shot wire."""
        capsule_bytes = None
        read_fd = write_fd = None
        if capsule:
            capsule_bytes = json.dumps(
                capsule, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            if len(capsule_bytes) > _MAX_CAPSULE_BYTES:
                raise ValueError(
                    f"daemon runtime capsule exceeds {_MAX_CAPSULE_BYTES} bytes"
                )
            read_fd, write_fd = os.pipe()
        env = {
            key: value for key, value in os.environ.items()
            if not _SECRET_ENV_NAME_RE.search(key)
        }
        source_root = Path(__file__).resolve().parents[3]
        parts = [str(source_root)]
        parts.extend(p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p)
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
        if read_fd is not None:
            env["LINGTAI_DAEMON_CAPSULE_FD"] = str(read_fd)
        stdout_path = run_dir / ("execution.stdout.log" if "execution_child" in module else "resume-owner.stdout.log")
        stderr_path = run_dir / ("execution.stderr.log" if "execution_child" in module else "resume-owner.stderr.log")
        stdout = open(stdout_path, "ab", buffering=0)
        stderr = open(stderr_path, "ab", buffering=0)
        try:
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
                pass_fds=(read_fd,) if read_fd is not None else (),
            )
            if read_fd is not None:
                os.close(read_fd)
                read_fd = None
                view = memoryview(capsule_bytes or b"")
                while view:
                    written = os.write(write_fd, view)
                    view = view[written:]
                os.close(write_fd)
                write_fd = None
            return proc
        finally:
            for fd in (read_fd, write_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            stdout.close()
            stderr.close()

    def spawn_execution_child(self, *, python_executable: str,
                               manifest_path: str, run_id: str,
                               run_dir: Path, capsule: dict | None = None,
                               mode: str = "emanation",
                               generation: str | None = None) -> subprocess.Popen:
        args = [str(manifest_path), run_id, mode]
        if generation:
            args.append(generation)
        return self._spawn_capsule_process(
            EXECUTION_CHILD_MODULE, python_executable, args,
            run_dir=Path(run_dir), capsule=capsule,
        )

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
