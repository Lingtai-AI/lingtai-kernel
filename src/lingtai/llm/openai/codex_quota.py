"""Read the Codex CLI's own OAuth quota via its app-server stdio protocol.

Wire protocol (verified against ``codex-cli 0.144.3``): spawn ``codex
app-server`` with ``$CODEX_HOME`` pointed at a throwaway auth dir; bare
newline-delimited JSON (not LSP framing) — send ``initialize``, read its
response, send the ``initialized`` notification, send
``account/rateLimits/read``, read its response, then terminate the process.
"""

from __future__ import annotations

import json
import math
import os
import queue
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

_CODEX_BIN = "codex"
_APP_SERVER_ARGS = ("app-server",)
_TIMEOUT_SECONDS = 10.0


class _Unavailable(Exception):
    """Internal fail-soft signal; never escapes :func:`read_remaining_percent`."""


def _prepare_temp_codex_home(auth_path: Path) -> tuple[Path, tempfile.TemporaryDirectory]:
    """Build a process-owned, mode-0700 temp ``$CODEX_HOME`` with a 0600 auth copy.

    Never mutates the real auth file. Raises :class:`_Unavailable` if the
    source auth file cannot be read/copied.
    """
    tmpdir = tempfile.TemporaryDirectory(prefix="lingtai-codex-quota-")
    try:
        home = Path(tmpdir.name)
        os.chmod(home, stat.S_IRWXU)
        dest = home / "auth.json"
        shutil.copyfile(str(auth_path), str(dest))
        os.chmod(dest, stat.S_IRUSR | stat.S_IWUSR)
        return home, tmpdir
    except OSError as exc:
        tmpdir.cleanup()
        raise _Unavailable(f"auth_copy_failed:{type(exc).__name__}") from exc


def _stdout_reader_thread(proc: subprocess.Popen, line_queue: "queue.Queue[str | None]") -> None:
    """Push each stdout line onto ``line_queue`` from a daemon thread; ``None`` marks EOF.

    Keeps the blocking ``readline()`` off the caller's bounded read loop, so a
    hung/silent child can never block past the read deadline.
    """
    def _run() -> None:
        try:
            if proc.stdout is not None:
                for line in iter(proc.stdout.readline, ""):
                    line_queue.put(line)
        except (ValueError, OSError):
            pass
        finally:
            line_queue.put(None)

    threading.Thread(target=_run, daemon=True).start()


def _read_line_until(
    line_queue: "queue.Queue[str | None]", predicate, deadline: float
) -> dict[str, Any] | None:
    """Read queued stdout lines until ``predicate(obj)`` is true or ``deadline`` passes."""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            line = line_queue.get(timeout=remaining)
        except queue.Empty:
            return None
        if line is None:
            return None
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and predicate(obj):
            return obj


def _terminate_process(proc: subprocess.Popen) -> None:
    """Best-effort bounded termination. Never raises."""
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
    except Exception:  # noqa: BLE001 - process cleanup must never raise
        pass
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass


def _run_app_server_read(codex_home: Path, timeout_seconds: float) -> dict[str, Any]:
    """Drive the initialize -> initialized -> account/rateLimits/read handshake.

    Returns the raw ``result`` dict. Raises :class:`_Unavailable` for every
    failure mode; never logs stderr content or request/response payloads.
    """
    if shutil.which(_CODEX_BIN) is None:
        raise _Unavailable("codex_binary_not_found")

    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    env["CODEX_DISABLE_ANALYTICS"] = "1"

    try:
        proc = subprocess.Popen(
            [_CODEX_BIN, *_APP_SERVER_ARGS],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        raise _Unavailable(f"spawn_failed:{type(exc).__name__}") from exc

    deadline = time.monotonic() + timeout_seconds
    line_queue: "queue.Queue[str | None]" = queue.Queue()
    _stdout_reader_thread(proc, line_queue)
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "lingtai-kernel", "version": "1.0"}},
        }) + "\n")
        proc.stdin.flush()

        init_response = _read_line_until(line_queue, lambda o: o.get("id") == 1, deadline)
        if init_response is None:
            raise _Unavailable("initialize_timeout_or_eof")
        if "error" in init_response:
            raise _Unavailable("initialize_error")

        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        proc.stdin.flush()

        proc.stdin.write(json.dumps({"id": 2, "method": "account/rateLimits/read", "params": None}) + "\n")
        proc.stdin.flush()

        read_response = _read_line_until(line_queue, lambda o: o.get("id") == 2, deadline)
        if read_response is None:
            raise _Unavailable("read_timeout_or_eof")
        if "error" in read_response:
            raise _Unavailable("read_error")
        result = read_response.get("result")
        if not isinstance(result, dict):
            raise _Unavailable("malformed_result")
        return result
    except BrokenPipeError as exc:
        raise _Unavailable(f"broken_pipe:{type(exc).__name__}") from exc
    finally:
        _terminate_process(proc)


def _extract_remaining_percent(result: dict[str, Any]) -> float | None:
    """Pull the main/primary window's remaining percent out of a raw ``result``.

    Returns ``None`` for any missing/malformed/non-finite field.
    """
    rate_limits = result.get("rateLimits")
    if not isinstance(rate_limits, dict):
        return None
    primary_window = rate_limits.get("primary")
    if not isinstance(primary_window, dict):
        return None
    used = primary_window.get("usedPercent")
    if isinstance(used, bool) or not isinstance(used, (int, float)):
        return None
    used = float(used)
    if not math.isfinite(used) or not 0.0 <= used <= 100.0:
        return None
    return 100.0 - used


def read_remaining_percent(auth_path: str | Path) -> float | None:
    """Return the main Codex rate-limit window's remaining percent for ``auth_path``.

    Spawns a throwaway ``codex app-server`` process with ``$CODEX_HOME``
    pointed at a process-owned mode-0700 temp dir holding a mode-0600 copy of
    ``auth_path`` (the real auth file is never written to). Returns ``None``
    for a query failure or a malformed/non-finite/missing field — never
    raises, never logs token/auth contents or raw paths.
    """
    tmpdir_handle = None
    try:
        auth_path = Path(auth_path).expanduser()
        if not auth_path.is_file():
            return None
        codex_home, tmpdir_handle = _prepare_temp_codex_home(auth_path)
        result = _run_app_server_read(codex_home, _TIMEOUT_SECONDS)
        return _extract_remaining_percent(result)
    except _Unavailable:
        return None
    except Exception:  # noqa: BLE001 - fail-soft contract; never raise
        return None
    finally:
        if tmpdir_handle is not None:
            try:
                tmpdir_handle.cleanup()
            except Exception:  # noqa: BLE001
                pass
