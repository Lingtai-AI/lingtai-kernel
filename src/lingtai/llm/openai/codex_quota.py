"""Codex OAuth quota/rate-limit read via the Codex CLI app-server (stdio).

Surfaces the machine-readable quota/rate-limit snapshot the ``codex`` CLI's
app-server exposes over its JSON-RPC-over-stdio protocol
(``account/rateLimits/read``), which the ``codex`` / ``codex-pool`` LLM
adapters otherwise never call. ``codex doctor --json`` does NOT carry this
data — it is a separate, narrower operation.

Wire protocol (verified against the installed ``codex-cli 0.144.3`` binary,
its ``codex app-server generate-ts`` / ``generate-json-schema`` bundles, and a
live stdio handshake — see the module docstring for
:func:`read_codex_quota_snapshot`):

  1. Spawn ``codex app-server`` with ``$CODEX_HOME`` pointed at the auth
     directory to use.
  2. Send ``{"id": 1, "method": "initialize", "params": {"clientInfo": {...}}}``
     as one newline-terminated JSON object (NOT LSP ``Content-Length`` framing
     — the app-server speaks bare newline-delimited JSON on stdio).
  3. Read the matching ``{"id": 1, "result": {...}}`` response.
  4. Send the notification ``{"method": "initialized"}`` (no ``id``, no
     ``params`` key).
  5. Send ``{"id": 2, "method": "account/rateLimits/read", "params": null}``.
  6. Read lines until the response with ``id == 2`` arrives (other lines may be
     unrelated server notifications, e.g. ``remoteControl/status/changed``,
     and are skipped).
  7. Terminate the process; never send any further requests.

This module owns exactly that read — it does not run a long-lived watcher for
the rolling ``account/rateLimits/updated`` notification (out of scope for the
minimal read-only surface).
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingtai.kernel.logging import get_logger

logger = get_logger()

# The app-server subcommand. Kept as data so the invoked argv is auditable.
_CODEX_BIN = "codex"
_APP_SERVER_ARGS = ("app-server",)

# Bounded process lifecycle. The whole read (spawn + initialize + read +
# terminate) must finish inside this wall-clock budget or the caller gets a
# fail-soft "timeout" result instead of hanging the caller's request path.
_DEFAULT_TIMEOUT_SECONDS = 10.0

_CLIENT_NAME = "lingtai-kernel"

# Non-secret client version string; kept independent of the LingTai package
# version so a resolution failure there can never break the quota read.
_CLIENT_VERSION = "1.0"


class CodexQuotaUnavailable(Exception):
    """Raised internally for a fail-soft condition; never escapes the public API.

    Every public function in this module catches this (and any other
    exception) and returns a normalized ``available=False`` result instead of
    raising, per the fail-soft contract required by callers that must never
    let a quota probe break an LLM request path.
    """


@dataclass(frozen=True)
class RateLimitWindowSnapshot:
    """One usage window (``primary`` or ``secondary``) of a rate limit bucket."""

    used_percent: float
    remaining_percent: float
    window_duration_mins: int | None
    resets_at: int | None


@dataclass(frozen=True)
class RateLimitBucketSnapshot:
    """One metered rate-limit bucket (``rateLimits`` or a ``rateLimitsByLimitId`` entry).

    ``limit_id`` is kept only when the protocol itself names it (the Codex
    app-server's own ``limitId`` field, e.g. ``"codex"``) — it is a public,
    stable identifier the protocol documents, not a private/opaque token, so
    surfacing it verbatim is safe.
    """

    limit_id: str | None
    limit_name: str | None
    primary: RateLimitWindowSnapshot | None
    secondary: RateLimitWindowSnapshot | None
    plan_type: str | None
    has_credits: bool | None
    credits_unlimited: bool | None
    credits_balance: str | None
    rate_limit_reached_type: str | None


@dataclass(frozen=True)
class CodexQuotaSnapshot:
    """Normalized, safe-to-log result of a Codex quota read.

    ``available=False`` covers every fail-soft path (no binary, timeout,
    malformed handshake, protocol error, missing/unreadable auth). ``error``
    is a short machine-safe category string in that case — never raw
    stderr/stdout content, never a stack trace, never auth material.
    """

    available: bool
    primary: RateLimitBucketSnapshot | None = None
    by_limit_id: dict[str, RateLimitBucketSnapshot] = field(default_factory=dict)
    error: str | None = None


def _safe_percent(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _normalize_window(raw: Any) -> RateLimitWindowSnapshot | None:
    if not isinstance(raw, dict):
        return None
    used = _safe_percent(raw.get("usedPercent"))
    if used is None:
        return None
    remaining = max(0.0, 100.0 - used)
    duration = raw.get("windowDurationMins")
    resets_at = raw.get("resetsAt")
    return RateLimitWindowSnapshot(
        used_percent=used,
        remaining_percent=remaining,
        window_duration_mins=(
            int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) else None
        ),
        resets_at=(
            int(resets_at) if isinstance(resets_at, (int, float)) and not isinstance(resets_at, bool) else None
        ),
    )


def _normalize_bucket(raw: Any) -> RateLimitBucketSnapshot | None:
    if not isinstance(raw, dict):
        return None
    credits = raw.get("credits")
    has_credits = credits_unlimited = credits_balance = None
    if isinstance(credits, dict):
        has_credits = credits.get("hasCredits") if isinstance(credits.get("hasCredits"), bool) else None
        credits_unlimited = credits.get("unlimited") if isinstance(credits.get("unlimited"), bool) else None
        balance = credits.get("balance")
        credits_balance = balance if isinstance(balance, str) else None

    limit_id = raw.get("limitId")
    limit_name = raw.get("limitName")
    plan_type = raw.get("planType")
    reached_type = raw.get("rateLimitReachedType")
    return RateLimitBucketSnapshot(
        limit_id=limit_id if isinstance(limit_id, str) else None,
        limit_name=limit_name if isinstance(limit_name, str) else None,
        primary=_normalize_window(raw.get("primary")),
        secondary=_normalize_window(raw.get("secondary")),
        plan_type=plan_type if isinstance(plan_type, str) else None,
        has_credits=has_credits,
        credits_unlimited=credits_unlimited,
        credits_balance=credits_balance,
        rate_limit_reached_type=reached_type if isinstance(reached_type, str) else None,
    )


def _normalize_response(result: dict[str, Any]) -> CodexQuotaSnapshot:
    """Turn a raw ``account/rateLimits/read`` ``result`` payload into a snapshot.

    Only the explicit, documented fields listed in the module docstring are
    read; everything else in the raw payload is ignored and never retained.
    """
    primary = _normalize_bucket(result.get("rateLimits"))
    by_id_raw = result.get("rateLimitsByLimitId")
    by_id: dict[str, RateLimitBucketSnapshot] = {}
    if isinstance(by_id_raw, dict):
        for key, value in by_id_raw.items():
            if not isinstance(key, str):
                continue
            bucket = _normalize_bucket(value)
            if bucket is not None:
                by_id[key] = bucket
    return CodexQuotaSnapshot(available=True, primary=primary, by_limit_id=by_id)


def _unavailable(error: str) -> CodexQuotaSnapshot:
    return CodexQuotaSnapshot(available=False, error=error)


def _prepare_temp_codex_home(auth_path: Path) -> tuple[Path, Any]:
    """Build a process-owned, mode-0700 temp ``$CODEX_HOME`` with a 0600 auth copy.

    Never mutates the real auth/config directory. Returns ``(codex_home,
    tmpdir_handle)`` — the caller must keep ``tmpdir_handle`` alive (it is a
    ``TemporaryDirectory`` context manager instance) until the subprocess has
    exited, then let it clean up via its own bounded lifecycle. Raises
    ``CodexQuotaUnavailable`` if the source auth file cannot be read/copied;
    never logs the auth path or contents.
    """
    tmpdir = tempfile.TemporaryDirectory(prefix="lingtai-codex-quota-")
    try:
        home = Path(tmpdir.name)
        os.chmod(home, stat.S_IRWXU)  # 0700, owner-only
        dest = home / "auth.json"
        shutil.copyfile(str(auth_path), str(dest))
        os.chmod(dest, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return home, tmpdir
    except OSError as exc:
        tmpdir.cleanup()
        raise CodexQuotaUnavailable(f"auth_copy_failed:{type(exc).__name__}") from exc


def _stdout_reader_thread(proc: subprocess.Popen, line_queue: "queue.Queue[str | None]") -> threading.Thread:
    """Start a daemon thread that pushes each stdout line onto ``line_queue``.

    Cross-platform bounded read: ``Queue.get(timeout=...)`` works identically
    on POSIX and Windows, unlike ``select.select()`` on a pipe object (POSIX
    ``select``/``selectors`` support waiting on pipes; Windows ``select`` only
    supports sockets). The blocking ``readline()`` call lives only in this
    background thread, so the caller's bounded ``queue.get`` loop can never be
    blocked by a hung/silent child. Pushes ``None`` once as an EOF sentinel
    when the child's stdout closes; the thread then exits. Marked ``daemon``
    so it never blocks interpreter shutdown if a caller abandons the read
    before EOF (the caller still explicitly terminates the process).
    """
    def _run() -> None:
        try:
            if proc.stdout is not None:
                for line in iter(proc.stdout.readline, ""):
                    line_queue.put(line)
        except (ValueError, OSError):
            # Stream closed out from under us (process torn down concurrently).
            pass
        finally:
            line_queue.put(None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _read_line_until(
    line_queue: "queue.Queue[str | None]",
    predicate,
    deadline: float,
) -> dict[str, Any] | None:
    """Read queued stdout lines until ``predicate(obj)`` is true or ``deadline`` passes.

    Returns the matching parsed JSON object, or ``None`` on timeout/EOF. Reads
    from a queue fed by :func:`_stdout_reader_thread` — never blocks on the
    subprocess pipe directly, so a hung/silent child can never keep this past
    ``deadline``. A ``None`` sentinel on the queue means the child's stdout
    closed (EOF); non-JSON or non-matching lines (server notifications) are
    skipped.
    """
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


def _run_app_server_read(codex_home: Path, timeout_seconds: float) -> dict[str, Any]:
    """Drive the initialize -> initialized -> account/rateLimits/read handshake.

    Returns the raw ``result`` dict of the rate-limits read. Raises
    ``CodexQuotaUnavailable`` for every failure mode (missing binary, process
    launch failure, timeout, JSON-RPC error response, malformed/missing
    result) — callers convert that into a fail-soft snapshot. Never logs
    stderr content or any request/response payload.
    """
    if shutil.which(_CODEX_BIN) is None:
        raise CodexQuotaUnavailable("codex_binary_not_found")

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
        raise CodexQuotaUnavailable(f"spawn_failed:{type(exc).__name__}") from exc

    deadline = time.monotonic() + timeout_seconds
    line_queue: "queue.Queue[str | None]" = queue.Queue()
    _stdout_reader_thread(proc, line_queue)
    try:
        assert proc.stdin is not None
        init_request = {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": _CLIENT_NAME, "version": _CLIENT_VERSION}
            },
        }
        proc.stdin.write(json.dumps(init_request) + "\n")
        proc.stdin.flush()

        init_response = _read_line_until(line_queue, lambda o: o.get("id") == 1, deadline)
        if init_response is None:
            raise CodexQuotaUnavailable("initialize_timeout_or_eof")
        if "error" in init_response:
            raise CodexQuotaUnavailable("initialize_error")

        proc.stdin.write(json.dumps({"method": "initialized"}) + "\n")
        proc.stdin.flush()

        read_request = {"id": 2, "method": "account/rateLimits/read", "params": None}
        proc.stdin.write(json.dumps(read_request) + "\n")
        proc.stdin.flush()

        read_response = _read_line_until(line_queue, lambda o: o.get("id") == 2, deadline)
        if read_response is None:
            raise CodexQuotaUnavailable("read_timeout_or_eof")
        if "error" in read_response:
            raise CodexQuotaUnavailable("read_error")
        result = read_response.get("result")
        if not isinstance(result, dict):
            raise CodexQuotaUnavailable("malformed_result")
        return result
    except BrokenPipeError as exc:
        raise CodexQuotaUnavailable(f"broken_pipe:{type(exc).__name__}") from exc
    finally:
        _terminate_process(proc)


def _terminate_process(proc: subprocess.Popen) -> None:
    """Best-effort bounded termination. Never raises; never logs stderr content."""
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


def read_codex_quota_snapshot(
    auth_path: str | Path,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> CodexQuotaSnapshot:
    """Read the Codex OAuth quota/rate-limit snapshot for one auth file.

    Spawns a throwaway ``codex app-server`` process with ``$CODEX_HOME``
    pointed at a process-owned mode-0700 temporary directory holding a
    mode-0600 copy of ``auth_path`` (the real auth file/directory is never
    written to). Runs the bounded initialize/read handshake documented in
    this module's docstring, then terminates the process. Analytics are
    disabled for the probe process (``CODEX_DISABLE_ANALYTICS=1``).

    Always returns a :class:`CodexQuotaSnapshot`; never raises. On any
    failure (binary missing, bad/missing auth file, handshake timeout,
    malformed response, protocol error) returns ``available=False`` with a
    short machine-safe ``error`` category — never token/auth contents, raw
    auth paths, raw account ids, command stderr, or the raw response payload.
    """
    auth_path = Path(auth_path).expanduser()
    tmpdir_handle = None
    try:
        if not auth_path.is_file():
            return _unavailable("auth_file_missing")
        codex_home, tmpdir_handle = _prepare_temp_codex_home(auth_path)
        result = _run_app_server_read(codex_home, timeout_seconds)
        return _normalize_response(result)
    except CodexQuotaUnavailable as exc:
        return _unavailable(str(exc))
    except Exception as exc:  # noqa: BLE001 - fail-soft contract; never raise
        logger.warning("Codex quota read failed: %s", type(exc).__name__)
        return _unavailable(f"unexpected:{type(exc).__name__}")
    finally:
        if tmpdir_handle is not None:
            try:
                tmpdir_handle.cleanup()
            except Exception:  # noqa: BLE001
                pass


def quota_snapshot_to_dict(snapshot: CodexQuotaSnapshot) -> dict[str, Any]:
    """Serialize a :class:`CodexQuotaSnapshot` into a plain JSON-safe dict.

    This is the stable, documented return shape for adapter-level consumers::

        {
          "available": bool,
          "error": str | None,
          "primary": <bucket dict> | None,
          "by_limit_id": {<limit_id>: <bucket dict>, ...},
        }

    where a bucket dict is::

        {
          "limit_id": str | None,
          "limit_name": str | None,
          "plan_type": str | None,
          "rate_limit_reached_type": str | None,
          "has_credits": bool | None,
          "credits_unlimited": bool | None,
          "credits_balance": str | None,
          "primary": <window dict> | None,
          "secondary": <window dict> | None,
        }

    and a window dict is::

        {
          "used_percent": float,
          "remaining_percent": float,
          "window_duration_mins": int | None,
          "resets_at": int | None,
        }
    """

    def _window_dict(w: RateLimitWindowSnapshot | None) -> dict[str, Any] | None:
        if w is None:
            return None
        return {
            "used_percent": w.used_percent,
            "remaining_percent": w.remaining_percent,
            "window_duration_mins": w.window_duration_mins,
            "resets_at": w.resets_at,
        }

    def _bucket_dict(b: RateLimitBucketSnapshot | None) -> dict[str, Any] | None:
        if b is None:
            return None
        return {
            "limit_id": b.limit_id,
            "limit_name": b.limit_name,
            "plan_type": b.plan_type,
            "rate_limit_reached_type": b.rate_limit_reached_type,
            "has_credits": b.has_credits,
            "credits_unlimited": b.credits_unlimited,
            "credits_balance": b.credits_balance,
            "primary": _window_dict(b.primary),
            "secondary": _window_dict(b.secondary),
        }

    return {
        "available": snapshot.available,
        "error": snapshot.error,
        "primary": _bucket_dict(snapshot.primary),
        "by_limit_id": {k: _bucket_dict(v) for k, v in snapshot.by_limit_id.items()},
    }
