"""Agent Record — the one atomic, versioned, redacted live personal record.

Every LingTai Agent (including avatars, which are ordinary ``Agent`` instances
bound to their own working directory) owns and publishes exactly one live
record describing itself: identity, model/provider, verified consumer-facing
handles, visible MCP integration labels, session/liveness, usage/context, a
bounded aggregate of its own daemons' self-records, and one versioned recent
daemon+Shell async-work presentation snapshot.

This module is the sole writer/reader of that shape. Consumer surfaces (TUI,
``/kanban``, ``/details``, Telegram, Portal) read and curate this record; they
must not independently re-collect normal live state or scan daemon files to
reconstruct it (see the confirmed alignment: Telegram 14238/14242/14248).

Ownership split, mirroring the existing ``_build_manifest`` override pattern:

- Core (``BaseAgent``) builds the identity/session/health/usage/daemon blocks
  from data it already owns (state, lifecycle clock, token usage, heartbeat).
- The outer ``lingtai.Agent`` composition root overrides
  ``_build_agent_record_extra`` to add ``handles``/``integrations`` — Core
  knows nothing about MCP registries or specific integrations (Telegram,
  etc.), so it never imports them.

The owning agent aggregates only daemon states selected by its append-only
dispatch ledger, newest ``LINGTAI_SESSION_STATS_DAEMON_LIMIT`` first. The ledger
determines membership and append order; each selected ``daemon.json`` remains
lifecycle and usage truth. Shell state comes only from the existing atomic job
records. One small single-flight refresher performs bounded daemon selection and
background Shell lifetime-directory enumeration outside the heartbeat thread.

Never serialize: API keys/tokens/passwords, environment or config values, raw
prompts/messages/tool payloads, working-directory/host/user paths, shell
commands, PIDs, task/card free text, or raw logs. A field earns a place here
only with a documented consumer value, a bounded size/domain, and a clear
owner/clock.
"""
from __future__ import annotations

import copy
import json
import math
import os
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .._fsutil import atomic_write_json
from ..config import HEARTBEAT_LIVENESS_SECONDS

# ---------------------------------------------------------------------------
# Schema / filenames
# ---------------------------------------------------------------------------

#: The main Agent record — one per agent working directory, derived/safe to
#: delete, regenerated on the next throttled refresh (mirrors
#: ``system/manifest.resolved.json``).
AGENT_RECORD_SCHEMA = "lingtai.agent_record/v1"
AGENT_RECORD_VERSION = 1
AGENT_RECORD_RELATIVE_PATH = "system/agent_record.json"

# ---------------------------------------------------------------------------
# Environment configuration — validated with safe fallback, documented in
# ENVIRONMENT_VARIABLES.md (LINGTAI_SESSION_STATS_REFRESH_SECONDS,
# LINGTAI_SESSION_STATS_DAEMON_LIMIT).
# ---------------------------------------------------------------------------

REFRESH_SECONDS_ENV = "LINGTAI_SESSION_STATS_REFRESH_SECONDS"
DEFAULT_REFRESH_SECONDS = 5.0

DAEMON_LIMIT_ENV = "LINGTAI_SESSION_STATS_DAEMON_LIMIT"
DEFAULT_DAEMON_LIMIT = 1000

# Provider-neutral recent asynchronous-work projection.  This is deliberately
# fixed policy, not another runtime/config surface: Telegram's established
# terminal window becomes the shared Agent Record contract.
ASYNC_WORK_SCHEMA = "lingtai.async_work/v1"
ASYNC_WORK_VERSION = 1
ASYNC_WORK_WINDOW_SECONDS = 600
ASYNC_WORK_STATUS_KEYS = ("running", "queued", "done", "failed")


def session_stats_refresh_seconds() -> float:
    """Read ``LINGTAI_SESSION_STATS_REFRESH_SECONDS`` with a safe fallback.

    Missing, blank, non-numeric, non-finite, zero, or negative values fall
    back to :data:`DEFAULT_REFRESH_SECONDS`. Read live at each use point —
    no caching, no restart required.
    """
    raw = os.environ.get(REFRESH_SECONDS_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_REFRESH_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_REFRESH_SECONDS
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_REFRESH_SECONDS
    return value


def session_stats_daemon_limit() -> int:
    """Read ``LINGTAI_SESSION_STATS_DAEMON_LIMIT`` with a safe fallback.

    Missing, blank, non-integer, zero, or negative values fall back to
    :data:`DEFAULT_DAEMON_LIMIT`. Read live at each use point.
    """
    raw = os.environ.get(DAEMON_LIMIT_ENV)
    if raw is None or not raw.strip():
        return DEFAULT_DAEMON_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_DAEMON_LIMIT
    if value <= 0:
        return DEFAULT_DAEMON_LIMIT
    return value


def should_refresh_agent_record(
    last_written_wall: float | None, wall_now: float, refresh_seconds: float
) -> bool:
    """Return whether a throttled Agent record write should proceed.

    ``last_written_wall`` is ``None`` on the first write (always refreshes).
    A wall-clock jump backwards (rare, e.g. NTP correction) also refreshes
    rather than wedging the record stale for ``refresh_seconds``.
    """
    if last_written_wall is None:
        return True
    return (wall_now - last_written_wall) >= refresh_seconds or wall_now < last_written_wall


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _safe_round(value: Any, ndigits: int = 1) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return round(float(value), ndigits)


# ---------------------------------------------------------------------------
# Agent record — identity / session / health / usage / daemon aggregate
# ---------------------------------------------------------------------------


def agent_record_path(working_dir: Path | str) -> Path:
    return Path(working_dir) / AGENT_RECORD_RELATIVE_PATH


def build_agent_record(
    agent,
    *,
    sequence: int = 0,
    daemon_summary: dict | None = None,
    async_work_snapshot: dict | None = None,
) -> dict:
    """Build the redacted, versioned Agent record for *agent*.

    Reads only fields Core already owns (mirrors the allowlisted subset of
    ``BaseAgent.status()``/``_build_manifest`` — never the full working-dir
    path, never prompts/messages/tool payloads). ``extra`` blocks
    (``handles``/``integrations``) come from the outer composition root via
    ``agent._build_agent_record_extra()`` — Core never imports MCP/Telegram
    modules.
    """
    from ..config import HEARTBEAT_LIVENESS_SECONDS

    mail_addr = None
    if getattr(agent, "_mail_service", None) is not None and agent._mail_service.address:
        mail_addr = agent._mail_service.address

    wall_now = agent._lifecycle_clock.wall_seconds()
    started_at = getattr(agent, "_started_at", None)
    uptime_seconds = None
    if getattr(agent, "_uptime_anchor", None) is not None:
        uptime_seconds = _safe_round(
            agent._lifecycle_clock.monotonic_seconds() - agent._uptime_anchor
        )

    heartbeat = float(getattr(agent, "_heartbeat", 0.0) or 0.0)
    heartbeat_age_seconds = _safe_round(wall_now - heartbeat, 3) if heartbeat > 0 else None
    if heartbeat_age_seconds is None:
        liveness = "unknown"
    elif heartbeat_age_seconds < HEARTBEAT_LIVENESS_SECONDS:
        liveness = "fresh"
    else:
        liveness = "stale"

    # Last-activity anchors — the same fields Telegram's Task Card footer
    # already renders (an "active (Ns)" countdown) and the ACTIVE no-progress
    # watchdog uses. Carried through unchanged so that curated consumer can
    # stop reading .status.json without losing this fact.
    last_api_call_at = getattr(agent, "_last_api_call_at", None)
    last_progress_at = getattr(agent, "_last_progress_at", None)

    usage = agent.get_token_usage()
    context_limit = None
    context_usage_pct = None
    if getattr(agent, "_chat", None) is not None:
        try:
            context_limit = agent._config.context_limit or agent._chat.context_window()
            if context_limit:
                context_usage_pct = _safe_round(
                    usage["ctx_total_tokens"] / context_limit * 100
                )
        except Exception:
            context_limit = None

    from ..base_agent.identity import _safe_llm_from_service  # local import avoids a base_agent<->session_stats cycle at module load

    model = _safe_llm_from_service(agent)

    record: dict[str, Any] = {
        "schema": AGENT_RECORD_SCHEMA,
        "schema_version": AGENT_RECORD_VERSION,
        "generated_at": _utc_now_iso(),
        "sequence": sequence,
        "identity": {
            "agent_id": getattr(agent, "_agent_id", None),
            "agent_name": getattr(agent, "agent_name", None),
            "nickname": getattr(agent, "nickname", None),
            "mail_address": mail_addr,
        },
        "model": model or {},
        "handles": {},
        "integrations": [],
        "session": {
            "state": agent._state.value,
            "started_at": started_at,
            "uptime_seconds": uptime_seconds,
            "molt_count": _safe_int(getattr(agent, "_molt_count", 0)),
        },
        "health": {
            "heartbeat_at": heartbeat if heartbeat > 0 else None,
            "heartbeat_age_seconds": heartbeat_age_seconds,
            "liveness": liveness,
            "last_api_call_at": last_api_call_at,
            "last_progress_at": last_progress_at,
        },
        "usage": {
            "api_calls": _safe_int(usage.get("api_calls")),
            "input_tokens": _safe_int(usage.get("input_tokens")),
            "output_tokens": _safe_int(usage.get("output_tokens")),
            "thinking_tokens": _safe_int(usage.get("thinking_tokens")),
            "cached_tokens": _safe_int(usage.get("cached_tokens")),
            "context_used_tokens": _safe_int(usage.get("ctx_total_tokens")),
            "context_limit_tokens": context_limit,
            "context_usage_pct": context_usage_pct,
            # Meta-line decomposition — mirrors .status.json's tokens.context
            # (system/tools/history) so a curated consumer's existing
            # breakdown rendering (e.g. Telegram /kanban's "fixed/history"
            # line) needs no new source, only a new field path.
            "context_system_tokens": _safe_int(usage.get("ctx_system_tokens")),
            "context_tools_tokens": _safe_int(usage.get("ctx_tools_tokens")),
            "context_history_tokens": _safe_int(usage.get("ctx_history_tokens")),
        },
        # BaseAgent passes an already-built background snapshot so this
        # projector never blocks a heartbeat on daemon history I/O.  Keeping a
        # synchronous fallback makes this pure public builder useful to
        # explicit callers/tests without making that fallback a heartbeat path.
        "daemons": daemon_summary if isinstance(daemon_summary, dict) else aggregate_daemon_records(getattr(agent, "_working_dir", None)),
    }
    # Missing on the first publication while the background owner obtains its
    # first complete view.  Never invent zero counts merely to fill the field.
    if isinstance(async_work_snapshot, dict):
        record["async_work"] = copy.deepcopy(async_work_snapshot)

    extra_provider = getattr(agent, "_build_agent_record_extra", None)
    if callable(extra_provider):
        try:
            extra = extra_provider()
        except Exception:
            extra = None
        if isinstance(extra, dict):
            handles = extra.get("handles")
            if isinstance(handles, dict):
                record["handles"] = {
                    str(k): v for k, v in handles.items() if isinstance(v, (str, int, float, bool))
                }
            integrations = extra.get("integrations")
            if isinstance(integrations, list):
                record["integrations"] = [item for item in integrations if isinstance(item, dict)]

    return record


def write_agent_record(working_dir: Path | str, record: dict) -> Path:
    """Atomically publish *record* to ``system/agent_record.json``."""
    path = agent_record_path(working_dir)
    atomic_write_json(path, record, ensure_ascii=False, indent=2)
    return path


def read_agent_record(working_dir: Path | str) -> dict | None:
    """Read the current Agent record, or ``None`` if missing/corrupt.

    Callers that need to distinguish "missing" from "corrupt" for a stale
    presentation policy should catch the read themselves; this is the plain
    best-effort accessor used by same-process/local callers (e.g. wrapper
    extension points, tests).
    """
    path = agent_record_path(working_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def classify_published_agent_record(
    record: object,
    *,
    wall_now: float,
    liveness_seconds: float = HEARTBEAT_LIVENESS_SECONDS,
) -> str | None:
    """Classify a published Agent Record against an injected wall-clock value.

    ``health.liveness`` is a write-time snapshot, so consumers must not treat it
    as live evidence. Explicit ``stuck`` and ``suspended`` states remain
    authoritative. The running states require a finite heartbeat with an age
    strictly below ``liveness_seconds``; otherwise they are offline. Missing or
    unrecognized records return ``None`` for an unavailable presentation.
    """
    if not isinstance(record, dict):
        return None
    if (
        record.get("schema") != AGENT_RECORD_SCHEMA
        or record.get("schema_version") != AGENT_RECORD_VERSION
    ):
        return None

    session = record.get("session")
    if not isinstance(session, dict):
        return None
    state = session.get("state")
    if state not in {"active", "idle", "asleep", "stuck", "suspended"}:
        return None
    if state in {"stuck", "suspended"}:
        return state

    health = record.get("health")
    heartbeat_at = health.get("heartbeat_at") if isinstance(health, dict) else None
    if isinstance(heartbeat_at, bool) or not isinstance(heartbeat_at, (int, float)):
        return "offline"
    if not math.isfinite(heartbeat_at):
        return "offline"
    if isinstance(wall_now, bool) or not isinstance(wall_now, (int, float)):
        return "offline"
    if not math.isfinite(wall_now):
        return "offline"

    age = wall_now - heartbeat_at
    if not math.isfinite(age) or age >= liveness_seconds:
        return "offline"
    return state


def query_published_agent_liveness(
    record: object,
    *,
    wall_now: float,
) -> dict[str, str]:
    """Project the canonical published-record liveness decision as one dict.

    The result is always exactly ``{"liveness": <value>}``, where a malformed
    or unavailable record becomes ``"unavailable"``. This is the consumer
    contract: callers must not read legacy status sources or reconstruct the
    heartbeat policy from record fields.
    """
    lifecycle = classify_published_agent_record(record, wall_now=wall_now)
    return {"liveness": lifecycle if lifecycle is not None else "unavailable"}


# ---------------------------------------------------------------------------
# Recent async-work projection and asynchronous Agent Record snapshot owner
# ---------------------------------------------------------------------------


def _empty_async_lane() -> dict[str, int]:
    return {key: 0 for key in ASYNC_WORK_STATUS_KEYS}


def _nonnegative_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(number) or number < 0:
        return 0
    return int(number)


def _safe_machine_identifier(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit:
        return None
    safe_punctuation = frozenset("._:/\\\\-")
    if not all(
        char.isascii() and (char.isalnum() or char in safe_punctuation)
        for char in text
    ):
        return None
    return text


def _iso_timestamp_epoch(value: object) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        stamp = datetime.fromisoformat(text)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _terminal_in_window(finished_at: float | None, now_epoch: float) -> bool:
    if finished_at is None or not math.isfinite(finished_at):
        return False
    age = now_epoch - finished_at
    return math.isfinite(age) and 0 <= age <= ASYNC_WORK_WINDOW_SECONDS


def _daemon_async_category(state: object) -> str | None:
    if not isinstance(state, dict):
        return None
    raw = state.get("state")
    if not isinstance(raw, str):
        return None
    if raw in {"running", "active"}:
        return "running"
    if raw in {"queued", "pending"}:
        return "queued"
    if raw == "done":
        return "done"
    if raw in {"failed", "cancelled", "timeout"}:
        return "failed"
    return None


def _shell_async_category(state: object) -> str | None:
    """Classify only durable Shell truth; never probe or mutate a process."""
    if not isinstance(state, dict):
        return None
    raw = state.get("status")
    if raw == "launching":
        return "queued"
    if raw == "running":
        return "running"
    if raw == "unrecoverable":
        return "failed"
    if raw != "completed":
        return None
    if state.get("cancellation_outcome") == "group_cancelled":
        return "failed"
    if state.get("exit_status_known") is not True:
        return None
    exit_code = state.get("exit_code")
    if type(exit_code) is not int:
        return None
    return "done" if exit_code == 0 else "failed"


def _selected_daemon_usage(state: dict[str, Any]) -> dict[str, Any] | None:
    tokens = state.get("tokens")
    cli_tokens = state.get("cli_tokens")
    tokens = tokens if isinstance(tokens, dict) else None
    cli_tokens = cli_tokens if isinstance(cli_tokens, dict) else None
    backend = state.get("backend")
    if backend == "lingtai":
        return tokens
    if isinstance(backend, str) and backend.strip():
        return cli_tokens or tokens
    # Legacy records had no backend marker. Prefer a non-zero external CLI
    # ledger, then fall back to kernel tokens, matching Telegram's prior view.
    if cli_tokens is not None and any(
        _nonnegative_count(cli_tokens.get(key)) > 0
        for key in ("input", "output", "thinking", "cached", "calls")
    ):
        return cli_tokens
    return tokens or cli_tokens


def _recent_daemon_lane(
    working_dir: Path,
    *,
    now_epoch: float,
    limit: int,
) -> dict[str, Any] | None:
    from ..daemon_dispatch import read_recent_daemon_states

    _, rows, warnings = read_recent_daemon_states(working_dir, limit=limit)
    if any(
        not isinstance(warning, dict)
        or warning.get("code") != "dispatch_ledger_empty"
        for warning in warnings
    ):
        return None
    lane: dict[str, Any] = _empty_async_lane()
    backend_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cached_tokens": 0,
        "api_calls": 0,
    }
    for _, _, state in rows:
        category = _daemon_async_category(state)
        if category is None:
            continue
        if category in {"done", "failed"} and not _terminal_in_window(
            _iso_timestamp_epoch(state.get("finished_at")), now_epoch
        ):
            continue
        lane[category] += 1

        raw_backend = state.get("backend")
        backend = _safe_machine_identifier(raw_backend, limit=48) or "unknown"
        backend_counts[backend] = backend_counts.get(backend, 0) + 1
        if raw_backend == "lingtai":
            model = _safe_machine_identifier(state.get("model"), limit=128)
            if model is not None and model != "unknown":
                model_counts[model] = model_counts.get(model, 0) + 1

        selected = _selected_daemon_usage(state)
        if selected is not None:
            usage["input_tokens"] += _nonnegative_count(selected.get("input"))
            usage["output_tokens"] += _nonnegative_count(selected.get("output"))
            usage["thinking_tokens"] += _nonnegative_count(selected.get("thinking"))
            usage["cached_tokens"] += _nonnegative_count(selected.get("cached"))
            usage["api_calls"] += _nonnegative_count(selected.get("calls"))

    lane["backend_counts"] = backend_counts
    if model_counts:
        lane["model_counts"] = model_counts
    lane["usage"] = usage
    return lane


def _recent_shell_lane(working_dir: Path, *, now_epoch: float) -> dict[str, int] | None:
    jobs_dir = working_dir / "system" / "jobs"
    try:
        jobs_mode = jobs_dir.lstat().st_mode
    except FileNotFoundError:
        return _empty_async_lane()
    except OSError:
        return None
    if stat.S_ISLNK(jobs_mode) or not stat.S_ISDIR(jobs_mode):
        return None
    try:
        children = list(jobs_dir.iterdir())
    except OSError:
        return None

    lane = _empty_async_lane()
    for job_dir in children:
        try:
            job_mode = job_dir.lstat().st_mode
        except OSError:
            return None
        if stat.S_ISLNK(job_mode) or not stat.S_ISDIR(job_mode):
            continue
        try:
            raw_state = (job_dir / "state.json").read_text(encoding="utf-8")
        except FileNotFoundError:
            # A legacy directory or the pre-publication edge of an atomic state
            # write has no durable lifecycle truth yet, so it is expected absent.
            continue
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
        try:
            state = json.loads(raw_state)
            if not isinstance(state, dict):
                continue
            category = _shell_async_category(state)
            if category is None:
                continue
            if category in {"done", "failed"}:
                finished = state.get("finished_at")
                if (
                    isinstance(finished, bool)
                    or not isinstance(finished, (int, float))
                    or not _terminal_in_window(float(finished), now_epoch)
                ):
                    continue
            lane[category] += 1
        except (json.JSONDecodeError, TypeError, ValueError, OverflowError):
            continue
    return lane


def build_async_work_snapshot(
    working_dir: Path | str | None,
    *,
    now_epoch: float | None = None,
    daemon_limit: int | None = None,
) -> dict[str, Any] | None:
    """Build one provider-neutral daemon+Shell recent-work snapshot.

    Collection is read-only. Callers place this function behind
    :class:`RecentAsyncWorkSnapshot`; it is intentionally synchronous as a pure
    worker operation and must not run on the foreground heartbeat thread.
    """
    if not working_dir:
        return None
    now = time.time() if now_epoch is None else now_epoch
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        return None
    now = float(now)
    if not math.isfinite(now):
        return None
    limit = daemon_limit if daemon_limit is not None else session_stats_daemon_limit()
    try:
        daemon = _recent_daemon_lane(Path(working_dir), now_epoch=now, limit=limit)
        shell = _recent_shell_lane(Path(working_dir), now_epoch=now)
    except Exception:
        return None
    if daemon is None or shell is None:
        return None

    aggregate = {
        key: daemon[key] + shell[key]
        for key in ASYNC_WORK_STATUS_KEYS
    }
    return {
        "schema": ASYNC_WORK_SCHEMA,
        "schema_version": ASYNC_WORK_VERSION,
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat().replace(
            "+00:00", "Z"
        ),
        "window_seconds": ASYNC_WORK_WINDOW_SECONDS,
        **aggregate,
        "daemon": daemon,
        "shell": shell,
    }


def query_published_async_work(
    record: object,
    *,
    wall_now: float,
) -> dict[str, Any] | None:
    """Return a strict safe async-work view, or ``None`` when unavailable.

    The exact 600-second window is also the maximum snapshot age. Consumers do
    not reuse a stopped producer's terminal counts forever, and malformed/old
    Agent Records never become fabricated zero rows.
    """
    if not isinstance(record, dict):
        return None
    if (
        record.get("schema") != AGENT_RECORD_SCHEMA
        or type(record.get("schema_version")) is not int
        or record.get("schema_version") != AGENT_RECORD_VERSION
    ):
        return None
    snapshot = record.get("async_work")
    if not isinstance(snapshot, dict):
        return None
    if (
        snapshot.get("schema") != ASYNC_WORK_SCHEMA
        or type(snapshot.get("schema_version")) is not int
        or snapshot.get("schema_version") != ASYNC_WORK_VERSION
        or type(snapshot.get("window_seconds")) is not int
        or snapshot.get("window_seconds") != ASYNC_WORK_WINDOW_SECONDS
    ):
        return None
    if isinstance(wall_now, bool) or not isinstance(wall_now, (int, float)):
        return None
    wall_now = float(wall_now)
    generated_at = _iso_timestamp_epoch(snapshot.get("generated_at"))
    if not math.isfinite(wall_now) or not _terminal_in_window(generated_at, wall_now):
        return None

    lanes: dict[str, dict[str, Any]] = {}
    for lane_name in ("daemon", "shell"):
        source = snapshot.get(lane_name)
        if not isinstance(source, dict):
            return None
        lane: dict[str, Any] = {}
        for key in ASYNC_WORK_STATUS_KEYS:
            value = source.get(key)
            if type(value) is not int or value < 0:
                return None
            lane[key] = value
        lanes[lane_name] = lane

    daemon_source = snapshot["daemon"]
    for detail_key, limit in (("backend_counts", 48), ("model_counts", 128)):
        details = daemon_source.get(detail_key)
        if details is None:
            continue
        if not isinstance(details, dict):
            return None
        safe_details: dict[str, int] = {}
        for raw_name, raw_count in details.items():
            name = _safe_machine_identifier(raw_name, limit=limit)
            if name is None or type(raw_count) is not int or raw_count <= 0:
                return None
            safe_details[name] = raw_count
        lanes["daemon"][detail_key] = safe_details

    raw_usage = daemon_source.get("usage")
    if not isinstance(raw_usage, dict):
        return None
    safe_usage: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cached_tokens",
        "api_calls",
    ):
        value = raw_usage.get(key)
        if type(value) is not int or value < 0:
            return None
        safe_usage[key] = value
    lanes["daemon"]["usage"] = safe_usage

    aggregate: dict[str, int] = {}
    for key in ASYNC_WORK_STATUS_KEYS:
        value = snapshot.get(key)
        expected = lanes["daemon"][key] + lanes["shell"][key]
        if type(value) is not int or value < 0 or value != expected:
            return None
        aggregate[key] = value

    return {
        "schema": ASYNC_WORK_SCHEMA,
        "schema_version": ASYNC_WORK_VERSION,
        "generated_at": snapshot["generated_at"],
        "window_seconds": ASYNC_WORK_WINDOW_SECONDS,
        **aggregate,
        **lanes,
    }


# ---------------------------------------------------------------------------
# Ledger-selected daemon aggregation
# ---------------------------------------------------------------------------


def _empty_daemon_summary(limit: int) -> dict:
    return {
        "source": "dispatch_ledger",
        "present": 0,
        "scanned": 0,
        "limit": limit,
        "counts_by_state": {},
        "usage": _empty_daemon_usage_totals(),
        "checked": {
            "source": "tail",
            "requested_limit": limit,
            "records_read": 0,
            "sequence_from": None,
            "sequence_to": None,
        },
        "warnings": [],
    }


def aggregate_daemon_records(working_dir: Path | str | None, *, limit: int | None = None) -> dict:
    """Aggregate only daemon states selected by the ledger's newest tail.

    ``present`` is intentionally the number of ledger records in this checked
    window, not a claimed lifetime total.  Discovering a lifetime total would
    require the history scan this component is specifically forbidden to do.
    Missing/corrupt referenced state files remain visible as bounded warning
    diagnostics and are excluded from ``scanned`` and usage totals.
    """
    resolved_limit = limit if limit is not None else session_stats_daemon_limit()
    summary = _empty_daemon_summary(resolved_limit)
    if not working_dir:
        return summary

    from ..daemon_dispatch import read_recent_daemon_states

    read, rows, warnings = read_recent_daemon_states(working_dir, limit=resolved_limit)
    summary["present"] = len(read.records)
    summary["checked"] = dict(read.checked)
    # An absent ledger is normal for an agent without new-mechanism dispatches;
    # retain its diagnostic for an explicit caller but avoid making it a fake
    # daemon result or falling back to legacy directory enumeration.
    summary["warnings"] = [dict(item) for item in warnings]

    counts_by_state: dict[str, int] = {}
    totals = _empty_daemon_usage_totals()
    for _, _, state in rows:
        summary["scanned"] += 1
        daemon_state = state.get("state")
        if isinstance(daemon_state, str) and daemon_state:
            counts_by_state[daemon_state] = counts_by_state.get(daemon_state, 0) + 1
        tokens = state.get("tokens") if isinstance(state.get("tokens"), dict) else {}
        cli_tokens = state.get("cli_tokens") if isinstance(state.get("cli_tokens"), dict) else {}
        totals["input_tokens"] += _safe_int(tokens.get("input")) + _safe_int(cli_tokens.get("input"))
        totals["output_tokens"] += _safe_int(tokens.get("output")) + _safe_int(cli_tokens.get("output"))
        totals["thinking_tokens"] += _safe_int(tokens.get("thinking")) + _safe_int(cli_tokens.get("thinking"))
        totals["cached_tokens"] += _safe_int(tokens.get("cached")) + _safe_int(cli_tokens.get("cached"))
        # LingTai's existing daemon token ledger records API calls in tokens;
        # external CLI records carry them in cli_tokens.  Combining both keeps
        # this display summary faithful to the established record shape.
        totals["api_calls"] += _safe_int(tokens.get("calls")) + _safe_int(cli_tokens.get("calls"))

    summary["counts_by_state"] = counts_by_state
    summary["usage"] = totals
    return summary


class RecentAsyncWorkSnapshot:
    """One per-agent single-flight owner for Agent Record background I/O.

    Scheduling is nonblocking and coalesces while a read is in flight. The
    heartbeat receives one detached pair containing the latest complete
    daemon-history summary and common recent daemon+Shell snapshot. Either value
    advances only after its own complete read. No executor, queue, scheduler, or
    generic task framework is introduced for this boundary.
    """

    def __init__(self, working_dir: Path | str, *, limit: int | None = None) -> None:
        self._working_dir = Path(working_dir)
        self._limit = limit
        resolved_limit = limit if limit is not None else session_stats_daemon_limit()
        daemon_summary = _empty_daemon_summary(resolved_limit)
        daemon_summary["refreshing"] = False
        self._snapshot: dict[str, Any] = {
            "daemons": daemon_summary,
            "async_work": None,
        }
        self._lock = threading.Lock()
        self._refreshing = False

    def schedule(self) -> bool:
        """Start one refresh if idle; return ``False`` when it was coalesced."""
        with self._lock:
            if self._refreshing:
                return False
            self._refreshing = True
            self._snapshot["daemons"]["refreshing"] = True
        try:
            thread = threading.Thread(
                target=self._refresh,
                name="lingtai-async-work-snapshot",
                daemon=True,
            )
            thread.start()
        except Exception:
            with self._lock:
                self._refreshing = False
                self._snapshot["daemons"]["refreshing"] = False
            return False
        return True

    def snapshot(self) -> dict[str, Any]:
        """Return a detached copy of the last completed/current compact pair."""
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _refresh(self) -> None:
        try:
            daemon_summary = aggregate_daemon_records(
                self._working_dir, limit=self._limit
            )
        except Exception:
            daemon_summary = None
        try:
            async_work = build_async_work_snapshot(
                self._working_dir, daemon_limit=self._limit
            )
        except Exception:
            async_work = None
        with self._lock:
            # Each result advances only when its own complete read succeeded.
            # In particular, a malformed Shell store must not freeze the
            # pre-existing daemon-history publication path.
            if isinstance(daemon_summary, dict):
                daemon_summary["refreshing"] = False
                self._snapshot["daemons"] = daemon_summary
            else:
                self._snapshot["daemons"]["refreshing"] = False
            if isinstance(async_work, dict):
                self._snapshot["async_work"] = async_work
            self._refreshing = False

def _empty_daemon_usage_totals() -> dict:
    return {
        "input_tokens": 0, "output_tokens": 0, "thinking_tokens": 0,
        "cached_tokens": 0, "api_calls": 0,
    }


__all__ = [
    "AGENT_RECORD_SCHEMA", "AGENT_RECORD_VERSION", "AGENT_RECORD_RELATIVE_PATH",
    "ASYNC_WORK_SCHEMA", "ASYNC_WORK_VERSION", "ASYNC_WORK_WINDOW_SECONDS",
    "ASYNC_WORK_STATUS_KEYS",
    "REFRESH_SECONDS_ENV", "DEFAULT_REFRESH_SECONDS",
    "DAEMON_LIMIT_ENV", "DEFAULT_DAEMON_LIMIT",
    "session_stats_refresh_seconds", "session_stats_daemon_limit",
    "should_refresh_agent_record",
    "agent_record_path", "build_agent_record", "write_agent_record", "read_agent_record",
    "classify_published_agent_record", "query_published_agent_liveness",
    "build_async_work_snapshot", "query_published_async_work",
    "aggregate_daemon_records", "RecentAsyncWorkSnapshot",
]
