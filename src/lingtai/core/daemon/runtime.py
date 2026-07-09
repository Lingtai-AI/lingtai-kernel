"""Daemon backend runtime primitives.

Small, behavior-preserving helpers extracted from ``daemon/__init__.py`` so
the backend runners (LingTai in-process + the CLI backends) stop re-declaring
the same subprocess-cleanup, stdout-deadline, cancellation/timeout, and stderr
draining logic inline.

Everything here is package-internal by location. ``daemon/__init__.py`` imports
these under their historical private names (``_kill_process_group`` etc.) so
existing tests and local monkeypatches that target those names keep working.

This module depends only on the standard library; ``DaemonRunDir`` is referenced
under ``TYPE_CHECKING`` to avoid an import cycle.
"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .run_dir import DaemonRunDir


def _supports_killpg() -> bool:
    """True when the platform exposes POSIX process-group signalling.

    ``os.killpg`` (and ``start_new_session``-backed process groups) only exist
    on POSIX. Native Windows has neither, so callers fall back to Windows
    process-tree teardown (``taskkill``). Kept as a tiny predicate so tests can
    simulate a Windows host without a real one.
    """
    return hasattr(os, "killpg")


def _new_process_group_kwargs() -> dict:
    """Platform-specific ``Popen`` kwargs that isolate the child's process group.

    POSIX: ``start_new_session=True`` puts the child in its own session/process
    group so ``kill_process_group`` can signal the whole group by pgid.

    Native Windows: there is no ``start_new_session``; use
    ``creationflags=CREATE_NEW_PROCESS_GROUP`` where the flag is available so
    the child is not tied to the daemon's console group (and ``taskkill /T``
    reaps the tree at cleanup). If the platform build lacks the flag, spawn
    with no isolation kwarg rather than crashing — cleanup still falls back to
    direct-child termination.
    """
    if _supports_killpg():
        return {"start_new_session": True}
    flag = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", None)
    if flag is None:
        return {}
    return {"creationflags": flag}


def spawn_cli_subprocess(
    cmd: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
) -> subprocess.Popen:
    """Spawn a headless CLI backend subprocess with platform-correct isolation.

    Wraps ``subprocess.Popen`` for the daemon's pipe/JSON CLI backends: stdout
    and stderr are captured as text pipes (the JSONL parsers depend on this),
    ``cwd`` is set, ``env`` is passed only when provided (``None`` inherits the
    parent environment), and the child is placed in its own process group via
    :func:`_new_process_group_kwargs` — ``start_new_session=True`` on POSIX,
    ``creationflags=CREATE_NEW_PROCESS_GROUP`` on native Windows.

    Callers keep their existing ``FileNotFoundError`` / ``OSError`` handling;
    this helper does not swallow spawn errors.
    """
    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
    )
    if env is not None:
        popen_kwargs["env"] = env
    popen_kwargs.update(_new_process_group_kwargs())
    return subprocess.Popen(cmd, **popen_kwargs)


def _terminate_direct_child(
    proc: subprocess.Popen,
    *,
    term_timeout: float,
    kill_timeout: float,
) -> None:
    """Fallback teardown: terminate the direct child only, then force-kill.

    Used on native Windows when ``taskkill`` is unavailable or fails. This is
    NOT process-tree teardown — grandchildren spawned by the CLI backend are
    not reaped — so it is strictly the last resort behind the ``taskkill`` path.
    """
    try:
        proc.terminate()
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=term_timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=kill_timeout)
        except subprocess.TimeoutExpired:
            pass


def _taskkill_process_tree(pid: int, *, timeout: float) -> bool:
    """Force-kill the whole process tree rooted at *pid* via ``taskkill``.

    ``taskkill /PID <pid> /T /F`` is the Windows equivalent of a process-group
    SIGKILL: ``/T`` reaps the tree (children/grandchildren) and ``/F`` forces
    termination. Returns True when taskkill was invoked and reported success,
    False when the ``taskkill`` executable is missing, timed out, or returned
    non-zero — the caller then falls back to direct-child termination.

    A non-zero return typically means the process already exited (taskkill
    reports "not found"), which the caller treats as "nothing left to reap".
    """
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def kill_process_group(
    proc: subprocess.Popen,
    *,
    term_timeout: float = 5.0,
    kill_timeout: float = 3.0,
) -> None:
    """Terminate *proc* and its whole process tree, force-killing survivors.

    On POSIX (``start_new_session=True`` ⇒ PGID == PID) this terminates the
    subprocess's whole process group: SIGTERM to the group, wait up to
    ``term_timeout`` seconds, then escalate to SIGKILL for any survivors.
    ``proc.pid`` is used directly as the PGID to avoid a ``getpgid`` round-trip
    that could race with PID recycling.

    On native Windows (no ``os.killpg``) this prefers ``taskkill /PID <pid>
    /T /F`` for real process-tree teardown — ``/T`` reaps grandchildren the
    plain ``proc.terminate()`` path would strand. If the ``taskkill``
    executable is missing or fails, it falls back to terminating only the
    direct child (``proc.terminate()`` → wait → ``proc.kill()``), which does
    NOT reap grandchildren. After a successful taskkill the child pid is
    reaped with a bounded ``proc.wait`` so it is not left as a zombie handle.

    Silently ignores ``ProcessLookupError`` (process already dead) and
    ``OSError`` (permission denied on already-dead group/process).
    """
    if not _supports_killpg():
        # Native Windows: no process groups. Prefer taskkill for real tree
        # teardown; fall back to direct-child terminate/kill if it is missing
        # or fails.
        if _taskkill_process_tree(proc.pid, timeout=term_timeout):
            try:
                proc.wait(timeout=kill_timeout)
            except subprocess.TimeoutExpired:
                pass
            return
        _terminate_direct_child(
            proc, term_timeout=term_timeout, kill_timeout=kill_timeout,
        )
        return

    # start_new_session=True guarantees pgid == pid
    pgid = proc.pid
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    try:
        proc.wait(timeout=term_timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=kill_timeout)
        except subprocess.TimeoutExpired:
            pass


# Sentinel placed on the stdout-reader queue when the background reader
# thread observes EOF on the subprocess pipe. The consumer treats this as
# "no more lines will ever arrive — stop draining."
_STDOUT_EOF = object()


def iter_stdout_with_deadline(
    proc: subprocess.Popen,
    deadline: float,
    thread_name: str,
):
    """Yield stdout lines from *proc* until EOF, deadline, or process exit.

    The fundamental problem this solves: ``for line in proc.stdout`` blocks
    the caller's thread until the subprocess writes a newline. If the
    resumed CLI hangs without producing output, the caller can never
    observe the deadline. We work around it by pushing the blocking
    read onto a small daemon thread that drops each line into a queue,
    while the caller pulls from the queue with ``timeout=remaining``.

    Yields raw lines (with trailing ``\\n`` preserved, matching the
    original iterator semantics). Stops iterating when:
      - the reader thread reports EOF (sentinel arrives), OR
      - ``time.monotonic() >= deadline`` (caller is expected to
        ``kill_process_group`` after handling timeout — we do NOT do
        it here so the worker can record timeout state first).

    The reader thread is a daemon thread (won't block process exit) and
    is left orphaned if the deadline fires — it will exit naturally once
    the subprocess is killed and its pipe closes.
    """
    q: "queue.Queue[object]" = queue.Queue(maxsize=1024)

    def _reader():
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                q.put(raw_line)
        except (ValueError, OSError):
            # Pipe closed mid-read (e.g. after kill_process_group). Treat
            # as EOF — the consumer either already noticed the timeout or
            # is about to.
            pass
        finally:
            q.put(_STDOUT_EOF)

    reader = threading.Thread(target=_reader, daemon=True, name=thread_name)
    reader.start()

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return  # caller handles timeout (kill + mark)
        try:
            item = q.get(timeout=min(remaining, 0.5))
        except queue.Empty:
            continue  # re-check deadline
        if item is _STDOUT_EOF:
            return
        yield item


def mark_cancelled_or_timeout(
    run_dir: "DaemonRunDir",
    timeout_event: threading.Event | None,
) -> str:
    """Mark *run_dir* terminal for a cancellation/timeout and return ``"[cancelled]"``.

    If *timeout_event* is set, the run was stopped by the watchdog, so mark
    it as a timeout; otherwise it was a manual reclaim/shutdown, so mark it
    cancelled. ``timeout_event`` may be ``None`` (direct-call tests), which
    defaults to cancelled semantics.

    Returns the literal ``"[cancelled]"`` sentinel string the backend runners
    hand back to the caller — model-visible, do not change.
    """
    if timeout_event is not None and timeout_event.is_set():
        run_dir.mark_timeout()
    else:
        run_dir.mark_cancelled()
    return "[cancelled]"


class StderrDrain:
    """Handle for a background stderr-draining thread.

    Holds the live ``lines`` list the drainer appends to (callers that need
    the full captured stderr read it directly), and exposes ``join`` to wait
    for the drainer in a ``finally`` block and ``tail`` to render the trailing
    diagnostic lines for failure messages.
    """

    def __init__(self, thread: threading.Thread, lines: list[str]) -> None:
        self._thread = thread
        self.lines = lines

    def join(self, timeout: float = 2.0) -> None:
        """Give the drainer a moment to finish reading before the pipe closes."""
        self._thread.join(timeout=timeout)

    def tail(self, n: int = 20) -> str:
        """Return the last *n* captured stderr lines joined by newlines (or "")."""
        return "\n".join(self.lines[-n:]) if self.lines else ""


def spawn_stderr_drainer(
    proc: subprocess.Popen,
    run_dir: "DaemonRunDir",
    *,
    thread_name: str,
) -> StderrDrain:
    """Start a daemon thread that drains *proc*'s stderr into a ``StderrDrain``.

    Preserves the existing per-backend behavior exactly: blank stripped lines
    are ignored, each non-blank stripped line is appended to the captured list
    and best-effort mirrored to ``run_dir.record_cli_output(..., stream="stderr")``
    with any recording exception swallowed. The thread is a daemon thread with
    the caller-supplied *thread_name* and is started immediately.
    """
    lines: list[str] = []

    def _drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stripped = line.rstrip("\n")
            if not stripped:
                continue
            lines.append(stripped)
            try:
                run_dir.record_cli_output(stripped, stream="stderr")
            except Exception:
                pass

    thread = threading.Thread(target=_drain, daemon=True, name=thread_name)
    thread.start()
    return StderrDrain(thread, lines)
