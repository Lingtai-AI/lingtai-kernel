"""Windows detached-process adapter for the daemon-run supervisor.

``WindowsDaemonSupervisorAdapter`` implements the Core-owned
``lingtai.kernel.daemon_supervisor.DaemonSupervisorPort`` by encoding a
``DaemonSupervisorRequest`` to the same compact JSON wire form as the POSIX
adapter and launching a new interpreter against the Windows-owned entrypoint
module ``lingtai.adapters.windows.daemon_supervisor_entrypoint``, detached via
``CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`` so it survives the caller's
exit (Windows children survive parent death by default; the new process group
additionally isolates console-control events).

One-shot capsule transport, Windows wire: ``os.pipe()`` in the parent, the
read end's OS HANDLE made inheritable and allowed through
``STARTUPINFO.lpAttributeList["handle_list"]`` (so ``close_fds=True`` still
holds for everything else), and the child environment carrying ONLY the
numeric handle in ``LINGTAI_DAEMON_CAPSULE_HANDLE``. Raw capsule bytes never
touch disk, argv, or an environment value — they cross the inherited pipe
exactly once and are consumed by the entrypoint, which converts the handle
back to a CRT fd via ``msvcrt.open_osfhandle`` and reuses the shared POSIX
read loop. The capsule is written AFTER ``Popen`` for the same reason the
POSIX adapter writes after spawn: a payload larger than the pipe buffer must
not block the launching manager before the supervisor has a reader.

Secret-name environment stripping and the 4 MiB capsule bound are imported
from the POSIX adapter module (plain ``os``/``json`` code, import-safe on
every platform) so the two adapters cannot drift.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from lingtai.adapters.windows import _win32
from lingtai.adapters.posix.daemon_supervisor import (
    _MAX_CAPSULE_BYTES,
    _SECRET_ENV_NAME_RE,
    _supervisor_environment,
)
from lingtai.kernel.daemon_supervisor import (
    DaemonSupervisorPort,
    DaemonSupervisorRequest,
    encode_request,
)

_CAPSULE_HANDLE_ENV = "LINGTAI_DAEMON_CAPSULE_HANDLE"

ENTRYPOINT_MODULE = "lingtai.adapters.windows.daemon_supervisor_entrypoint"
EXECUTION_CHILD_MODULE = "lingtai.adapters.windows.daemon_execution_child_entrypoint"
RESUME_OWNER_MODULE = "lingtai.adapters.windows.daemon_resume_owner_entrypoint"


def _require_windows() -> None:
    """Fail loudly off-Windows before any handle mechanism is touched."""
    if os.name != "nt":
        raise OSError("Windows daemon supervisor adapter requires Windows")


def _encode_capsule(capsule: dict | None) -> bytes | None:
    """Encode the bounded one-shot capsule in memory only."""
    if not capsule:
        return None
    capsule_bytes = json.dumps(
        capsule, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    if len(capsule_bytes) > _MAX_CAPSULE_BYTES:
        raise ValueError(
            f"daemon runtime capsule exceeds {_MAX_CAPSULE_BYTES} bytes"
        )
    return capsule_bytes


def _stripped_process_environment() -> dict[str, str]:
    """Mirror the POSIX capsule-process environment policy exactly.

    Credential-shaped names are removed with the same shared regex; the
    repository source root is prepended to ``PYTHONPATH`` for editable runs.
    """
    env = {
        key: value for key, value in os.environ.items()
        if not _SECRET_ENV_NAME_RE.search(key)
    }
    source_root = Path(__file__).resolve().parents[3]
    parts = [str(source_root)]
    parts.extend(p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env


def adopt_capsule_handle_to_fd(*, strict: bool) -> None:
    """Convert the inherited capsule HANDLE into the shared fd wire, in-place.

    The Windows spawn wire publishes only a pipe HANDLE number under
    ``LINGTAI_DAEMON_CAPSULE_HANDLE``; the shared mechanism-free read loops in
    the POSIX entrypoint modules consume a CRT fd from
    ``LINGTAI_DAEMON_CAPSULE_FD``. This one-shot adoption bridges the two so
    those readers run unchanged: open the handle as a binary read fd and
    republish it under the fd name. ``strict=False`` mirrors the supervisor
    entrypoint's lenient read (a malformed handle means "no capsule");
    ``strict=True`` mirrors the execution-child/resume-owner fail-loud read.
    """
    raw = os.environ.pop(_CAPSULE_HANDLE_ENV, None)
    if raw is None:
        return
    try:
        import msvcrt

        fd = msvcrt.open_osfhandle(int(raw), os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except (ValueError, OSError):
        if strict:
            raise
        return
    os.environ["LINGTAI_DAEMON_CAPSULE_FD"] = str(fd)


class WindowsDaemonSupervisorAdapter(DaemonSupervisorPort):
    """Encode the request and spawn the supervisor as a detached Windows process.

    The launched process runs ``request.python_executable`` against the owned
    Windows entrypoint module with all three standard streams detached
    (stdin ``DEVNULL``, stdout/stderr appended to restrictive run-owned log
    files) and ``_win32.DETACHED_CREATIONFLAGS``. The call returns immediately
    after ``Popen``; it does not wait for or track the child.
    """

    def spawn_detached(
        self, request: DaemonSupervisorRequest, *, capsule: dict | None = None
    ) -> None:
        """Launch one owner and optionally hand it one-shot runtime values."""
        payload = encode_request(request)
        run_dir = Path(request.manifest_path).resolve().parent
        self._launch_with_capsule(
            [request.python_executable, "-m", ENTRYPOINT_MODULE, payload],
            env=_supervisor_environment(request),
            cwd=run_dir.parent.parent,
            stdout_path=run_dir / "supervisor.stdout.log",
            stderr_path=run_dir / "supervisor.stderr.log",
            capsule=capsule,
        )

    @staticmethod
    def _launch_with_capsule(
        argv: list[str], *, env: dict[str, str], cwd: Path,
        stdout_path: Path, stderr_path: Path, capsule: dict | None,
    ) -> subprocess.Popen:
        """Spawn one detached owner/child with the bounded one-shot handle wire.

        ``handle_list`` is deliberate: with ``close_fds=True`` only the listed
        capsule handle (plus the redirected std handles CPython appends) is
        inherited, so the numeric handle is process metadata while raw
        credentials cross only the inherited pipe and are consumed once.
        """
        _require_windows()
        import msvcrt

        capsule_bytes = _encode_capsule(capsule)
        read_fd = write_fd = None
        if capsule_bytes is not None:
            read_fd, write_fd = os.pipe()
        stdout = open(stdout_path, "ab", buffering=0)
        stderr = open(stderr_path, "ab", buffering=0)
        try:
            for path in (stdout_path, stderr_path):
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
            env = dict(env)
            startupinfo = None
            if read_fd is not None:
                handle = int(msvcrt.get_osfhandle(read_fd))
                os.set_handle_inheritable(handle, True)
                env[_CAPSULE_HANDLE_ENV] = str(handle)
                startupinfo = subprocess.STARTUPINFO(
                    lpAttributeList={"handle_list": [handle]}
                )
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                env=env,
                cwd=str(cwd),
                close_fds=True,
                creationflags=_win32.DETACHED_CREATIONFLAGS,
                startupinfo=startupinfo,
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
        run_dir = Path(run_dir)
        return self._launch_with_capsule(
            [python_executable, "-m", EXECUTION_CHILD_MODULE, *args],
            env=_stripped_process_environment(),
            cwd=run_dir.parent.parent,
            stdout_path=run_dir / "execution.stdout.log",
            stderr_path=run_dir / "execution.stderr.log",
            capsule=capsule,
        )

    def spawn_resume_owner(self, *, python_executable: str,
                           manifest_path: str, run_id: str,
                           run_dir: Path, generation: str,
                           capsule: dict | None = None) -> subprocess.Popen:
        run_dir = Path(run_dir)
        return self._launch_with_capsule(
            [python_executable, "-m", RESUME_OWNER_MODULE,
             str(manifest_path), run_id, generation],
            env=_stripped_process_environment(),
            cwd=run_dir.parent.parent,
            stdout_path=run_dir / "resume-owner.stdout.log",
            stderr_path=run_dir / "resume-owner.stderr.log",
            capsule=capsule,
        )


__all__ = [
    "WindowsDaemonSupervisorAdapter", "ENTRYPOINT_MODULE",
    "EXECUTION_CHILD_MODULE", "RESUME_OWNER_MODULE",
    "adopt_capsule_handle_to_fd",
]
