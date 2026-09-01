"""Notification policy — channel allowlist, validation, dismiss authority,
producer envelope, and sync-primitive helpers.

Persistence (fingerprint, snapshot, publish, clear, atomic ack update,
atomic channel mutation) is delegated to the ``NotificationStorePort``
injected on ``BaseAgent``.  This module owns the Core policy layer:
channel syntax / allowlist, guarded / protected dismiss rules,
producer-owned stale decisions, wake / live-holder order, and
model-visible representation.

External producers (LICC inbox, direct ``mcp.*`` drops) use the
POSIX adapter directly and remain compatible with the filesystem
protocol.

Naming convention:

* Kernel intrinsics write ``<intrinsic_name>.json`` (e.g. ``email.json``,
  ``soul.json``, ``system.json``).
* MCP-loaded servers write ``mcp.<server_name>.json`` (e.g.
  ``mcp.imap.json``, ``mcp.telegram.json``).

The basename is the *tool* whose namespace owns the notification.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from .notification_store import NotificationStorePort

_CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Notification channels are intentionally allowlisted.  Unknown files in
# `.notification/` are ignored by readers and cannot be published/cleared
# through kernel helpers.  MCP bridge channels are allowlisted as a family
# because server names are dynamic but still owned by the MCP inbox contract.
_NOTIFICATION_CHANNEL_ALLOWLIST: set[str] = {
    "bash",
    "btw",
    "cron",
    "daemon",
    "delay-alarm",
    "email",
    "goal",
    "molt",
    "nudge",
    "post-molt",
    "soul",
    "system",
    "tool_loop_guard",
}
_NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST: tuple[str, ...] = ("mcp.",)

# External-hook registered channels, mirrored from each agent's
# `.notification/hooks.json` manifest registry. Keyed by the agent's working
# directory (the string form of ``agent._working_dir``). The mirror is seeded
# lazily per workdir by ``sync_hook_registry`` (called from the notification
# sync path) and mutated atomically by the add/edit/drop tool handlers, so the
# module-global allow predicate can consult it without threading per-agent
# state through call sites.
_REGISTERED_HOOK_CHANNELS: dict[str | None, set[str]] = {}

# Serializes all four mirror books (channels, stat cache, seeded set,
# blocked-channel warned set) so heartbeat-thread seeding and
# tool-call-thread mutation cannot interleave and leave the mirror
# reflecting an older committed registry.
_HOOK_REGISTRY_LOCK = threading.Lock()

# Workdirs already seeded from disk this process (avoids re-reading hooks.json
# on every sync tick).
_HOOK_REGISTRY_SEEDED: set[str | None] = set()

# Cheap staleness fingerprint per workdir: ``(st_mtime_ns, st_size)`` of the
# registry file at last seed, so an out-of-band write from another process
# (sibling CLI, Telegram server, hook installer) re-seeds the mirror without
# re-reading on every tick.
_HOOK_REGISTRY_STAT: dict[str | None, tuple[int, int] | None] = {}

# Blocked-attempt warnings already emitted per workdir+channel, so a
# repeatedly-present unregistered channel does not spam system events. Cleared
# when the channel becomes registered (then a later re-block can warn again).
_BLOCKED_CHANNEL_WARNED: dict[str | None, set[str]] = {}

# Channels that are valid notification surfaces but must not be cleared via
# generic system.dismiss because they are source-of-truth files.
_PROTECTED_GENERIC_DISMISS: dict[str, str] = {
    "goal": (
        "Goal state lives in .notification/goal.json. Do not dismiss it. "
        "To cancel the goal, delete .notification/goal.json; to complete it, "
        "mark its status done/superseded or replace/delete the file. See the "
        "goal manual under system-manual for details."
    ),
}

# Channels whose generic dismissal would leak producer-owned state.
# Producers with durable unread/state mirrors register themselves here at
# import time so notification(action="dismiss_channel", channel=...) can refuse
# unsafe generic clears and point the agent at the producer-specific verb.
_GENERIC_DISMISS_GUARDED: dict[str, str] = {}

# Agent-facing note included in the dismiss result to explain preferred path.
_LARGE_RESULT_DISMISS_NOTE = (
    "large_tool_result reminder acknowledged and removed. "
    "Summarization via context(action='summarize') remains the preferred way to "
    "discharge a large result — it records an agent-authored compact replacement "
    "in runtime history and auto-clears the reminder. Dismissal only clears the "
    "reminder surface; the original large result remains in chat history and "
    "events.jsonl."
)


def _is_large_result_event(ev: object) -> bool:
    """Return True iff *ev* is a large_tool_result system event."""
    return isinstance(ev, dict) and ev.get("source") == "large_tool_result"


# ---------------------------------------------------------------------------
# Allow predicate for the store — built from Core policy constants.
# The store receives this predicate so it never imports channel policy.
# ---------------------------------------------------------------------------


def _build_allow_predicate(workdir: str | None = None) -> callable:
    """Return a closure that answers ``is_channel_allowed`` for the store.

    ``workdir`` scopes registered hook channels to the owning agent; when
    ``None`` (no agent context) hook channels are NOT allowed — only the
    static set and the ``mcp.*`` prefix family pass.
    """

    def _allow(channel: str) -> bool:
        try:
            validate_channel_name(channel)
        except ValueError:
            return False
        if channel in _NOTIFICATION_CHANNEL_ALLOWLIST:
            return True
        if any(
            channel.startswith(prefix)
            for prefix in _NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST
        ):
            return True
        if workdir is None:
            return False
        # Registered external-hook channels are allowlisted through the
        # module-level mirror for THIS agent's workdir only.
        return channel in _REGISTERED_HOOK_CHANNELS.get(workdir, ())

    return _allow


# Cached allow predicate per workdir — rebuilt if the allowlist changes.
_allow_predicates: dict[str | None, callable] = {}


def _get_allow_predicate(workdir: str | None = None) -> callable:
    if workdir not in _allow_predicates:
        _allow_predicates[workdir] = _build_allow_predicate(workdir)
    return _allow_predicates[workdir]


def _invalidate_allow_predicates() -> None:
    _allow_predicates.clear()


# ---------------------------------------------------------------------------
# Consumer notification delay — private durable state, timer, and alarm mirror
# ---------------------------------------------------------------------------
#
# Delay is intentionally a *consumer* policy.  It never mutates the target
# producer's .json file: a short-lived private state record causes the coherent
# consumer read below to omit one target channel.  Expiry writes a separate
# delay-alarm mirror and immediately stops filtering the target.  The state and
# alarm use the same native lock as NotificationStore mutations, so independently
# composed processes cannot race a timer/recovery into two alarm publications.
#
# The `daemon` channel is the one target delay suppresses *attention* for
# instead of hiding: it is an aggregate of independent per-run mini-channels
# that already owns an attention token (the alarm-threshold mask below), and
# hiding it would also hide daemon truth — the payload the model reads, the
# bounded `agent_state.daemon` summary, and the delivered byte version a
# non-forced daemon dismissal compares against.  A live daemon delay therefore
# collapses the daemon entry to one constant attention token (readable, but it
# cannot move the wake fingerprint) while every other channel — including
# registered hook channels — keeps byte-exact change detection and wakes the
# parent normally.  Expiry lifts the mask and publishes the same delay-alarm
# mirror, so a delayed daemon channel can never strand an ASLEEP parent.

DELAY_ALARM_CHANNEL = "delay-alarm"
_DELAY_STATE_FILENAME = ".delay_state.json"
NOTIFICATION_DELAY_MAX_SECONDS_ENV = "LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS"
DEFAULT_NOTIFICATION_DELAY_MAX_SECONDS = 600
_DELAY_LOCKS_GUARD = threading.Lock()
_DELAY_LOCKS: dict[str, threading.RLock] = {}
_DELAY_TIMERS_GUARD = threading.Lock()
_DELAY_TIMERS: dict[str, tuple[str, float, threading.Timer]] = {}


def notification_delay_max_seconds(agent=None) -> int:
    """Return the live finite delay cap, logging invalid configured fallbacks.

    The environment is intentionally read for every delay action, so an operator
    can tune the bounded consumer silence window without a restart. Invalid,
    blank, zero, and negative values fail safe to the ten-minute default; no
    configuration can turn delay into an unbounded suppression.
    """
    raw = os.environ.get(NOTIFICATION_DELAY_MAX_SECONDS_ENV)
    if raw is None:
        return DEFAULT_NOTIFICATION_DELAY_MAX_SECONDS
    try:
        value = int(raw.strip())
    except (AttributeError, TypeError, ValueError):
        value = 0
    if value > 0:
        return value
    try:
        agent._log(
            "notification_delay_max_seconds_invalid",
            env=NOTIFICATION_DELAY_MAX_SECONDS_ENV,
            raw_value=str(raw)[:100],
            fallback_seconds=DEFAULT_NOTIFICATION_DELAY_MAX_SECONDS,
        )
    except Exception:
        pass
    return DEFAULT_NOTIFICATION_DELAY_MAX_SECONDS


def _delay_workdir_key(workdir: str | Path | None) -> str | None:
    if workdir is None:
        return None
    try:
        return str(Path(workdir))
    except (TypeError, ValueError):
        return None


def _delay_paths(workdir: str | Path) -> tuple[Path, Path, Path]:
    notification_dir = Path(workdir) / ".notification"
    return (
        notification_dir,
        notification_dir / _DELAY_STATE_FILENAME,
        notification_dir / f"{DELAY_ALARM_CHANNEL}.json",
    )


def _delay_thread_lock(workdir: str) -> threading.RLock:
    with _DELAY_LOCKS_GUARD:
        lock = _DELAY_LOCKS.get(workdir)
        if lock is None:
            lock = threading.RLock()
            _DELAY_LOCKS[workdir] = lock
        return lock


@contextmanager
def _delay_transaction(workdir: str, store: NotificationStorePort):
    """Serialize only delay state and its alarm mirror, never all notifications.

    This deliberately performs the tiny delay/alarm transaction directly rather
    than adding a ninth Store Port family.  Callers must capture Store read facts
    before entering: no snapshot/fingerprint may run under these native scopes.
    """
    from .notification_store._mutation_lock import (
        channel_mutation_scope,
        exclusive_notification_mutation,
        resource_mutation_scope,
    )

    notification_dir, _, _ = _delay_paths(workdir)
    with _delay_thread_lock(workdir):
        with exclusive_notification_mutation(
            store.mutation_lock,
            notification_dir,
            [resource_mutation_scope("delay-state"), channel_mutation_scope(DELAY_ALARM_CHANNEL)],
        ):
            yield


def _read_delay_state_locked(state_path: Path) -> dict[str, Any] | None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return state if isinstance(state, dict) else None


def _write_delay_state_locked(state_path: Path, state: dict[str, Any]) -> None:
    from ._fsutil import atomic_write_json

    atomic_write_json(
        state_path, state, ensure_ascii=False, indent=None, sort_keys=True, fsync=True
    )


def _delay_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _active_delay_state(state: object, *, now: float | None = None) -> dict[str, Any] | None:
    """Return a validated live delay, or ``None`` (fail visible).

    A malformed/unreadable state never suppresses consumer delivery.  This is
    deliberately stricter than ordinary state parsing: silence is not a safe
    fallback for a consumer-only policy.
    """
    if not isinstance(state, dict) or state.get("status") != "active":
        return None
    target = state.get("target")
    request_id = state.get("request_id")
    seconds = state.get("requested_seconds")
    started = state.get("started_epoch")
    deadline = state.get("deadline_epoch")
    if (
        not isinstance(target, str)
        or target == DELAY_ALARM_CHANNEL
        or not isinstance(request_id, str)
        or not request_id
        or isinstance(seconds, bool)
        or not isinstance(seconds, int)
        or seconds < 1
        or isinstance(started, bool)
        or not isinstance(started, (int, float))
        or isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or deadline < started
    ):
        return None
    if now is not None and deadline <= now:
        return None
    return state


def _read_target_stats(
    workdir: str, channel: str, store: NotificationStorePort
) -> dict[str, Any]:
    """Read conservative target facts through the injected Store Port.

    This intentionally runs before a delay transaction. The later transaction
    revalidates delay identity/state; a racing producer only makes `changed`
    fail visible and never puts a Store read below delay native scopes.
    """
    try:
        allow = lambda name: name == channel
        payload = store.snapshot(allow).get(channel)
        entries = tuple(store.fingerprint(allow))
    except OSError as exc:
        return {"present": None, "read_error": type(exc).__name__}
    except Exception as exc:
        return {"present": None, "read_error": type(exc).__name__}

    expected_name = f"{channel}.json"
    entry = next((item for item in entries if item and item[0] == expected_name), None)
    if entry is None and payload is None:
        return {"present": False}
    if entry is None:
        # A non-filesystem Store may expose a payload without a byte version.
        # Keep the delay fail-visible rather than inventing a CAS token.
        return {"present": None, "read_error": "missing_fingerprint"}

    stats: dict[str, Any] = {
        "present": True,
        "byte_size": entry[1],
        "sha256": entry[2],
    }
    if not isinstance(payload, dict):
        return stats
    data = payload.get("data")
    if not isinstance(data, dict):
        return stats
    reported_count = data.get("count")
    if isinstance(reported_count, int) and not isinstance(reported_count, bool) and reported_count >= 0:
        stats["producer_reported_count"] = reported_count
    events = data.get("events")
    if isinstance(events, list):
        stats["retained_event_count"] = len(events)
        stats["retained_event_count_scope"] = "current mirror entries; not asserted total"
    return stats


def _delay_stats_changed(initial: object, current: object) -> bool:
    if not isinstance(initial, dict) or not isinstance(current, dict):
        return True
    if initial.get("present") != current.get("present"):
        return True
    if initial.get("present") is not True:
        return False
    return initial.get("sha256") != current.get("sha256")


def _delay_alarm_payload(completion: dict[str, Any]) -> dict[str, Any]:
    target = str(completion["target"])
    return {
        "header": f"Notification delay ended: {target}",
        "icon": "⏰",
        "priority": "high",
        "published_at": completion["expired_at"],
        "instructions": (
            "This is a consumer-only delay alarm. The target producer state was "
            "not changed; handle the re-exposed target, then dismiss delay-alarm "
            "when this reminder is no longer needed."
        ),
        "data": {
            "delay_alarm": {
                "request_id": completion["request_id"],
                "target": target,
                "requested_seconds": completion["requested_seconds"],
                "actual_seconds": completion["actual_seconds"],
                "changed": completion["changed"],
                "initial": completion["initial"],
                "current": completion["current"],
                "statistics_scope": (
                    "Byte fingerprints compare the current mirror with delay start. "
                    "Counts are producer-reported or retained current entries only; "
                    "they are never asserted as an exact total for overwrite/capped payloads."
                ),
            }
        },
    }


def reconcile_notification_delay(
    workdir: str | Path | None,
    store: NotificationStorePort,
    *,
    expected_request_id: str | None = None,
) -> bool:
    """Recover or complete an overdue delay, publishing one stable alarm mirror.

    The state first records an immutable ``expiring`` completion snapshot, then
    writes the alarm and marks it ``published`` under the same cross-process
    lock.  A crash after the alarm replace but before the final state write only
    repeats that exact same latest-only mirror (same request id, timestamp, and
    payload) on recovery; it cannot append a second alarm.  A corrupt state or
    any I/O failure returns without filtering so the caller fails toward visible
    delivery; later heartbeat/timer sync retries publication.
    """
    key = _delay_workdir_key(workdir)
    if key is None:
        return False
    # The heartbeat calls this path every tick. Read the private state first and
    # acquire *zero* native locks when there is no due transaction to repair.
    _, observed_state_path, _ = _delay_paths(key)
    observed = _read_delay_state_locked(observed_state_path)
    if not isinstance(observed, dict):
        return False
    observed_status = observed.get("status")
    if observed_status in {"published", "cancelled"}:
        return False
    observed_request_id = observed.get("request_id")
    if expected_request_id is not None and observed_request_id != expected_request_id:
        return False
    observed_current: dict[str, Any] | None = None
    if observed_status == "active":
        observed_target = observed.get("target")
        observed_deadline = observed.get("deadline_epoch")
        if (
            not isinstance(observed_target, str)
            or observed_target == DELAY_ALARM_CHANNEL
            or isinstance(observed_deadline, bool)
            or not isinstance(observed_deadline, (int, float))
            or observed_deadline > time.time()
        ):
            return False
        observed_current = _read_target_stats(key, observed_target, store)
    elif observed_status != "expiring":
        return False
    try:
        with _delay_transaction(key, store):
            _, state_path, alarm_path = _delay_paths(key)
            state = _read_delay_state_locked(state_path)
            if not isinstance(state, dict):
                return False
            status = state.get("status")
            if status == "published" or status == "cancelled":
                return False
            request_id = state.get("request_id")
            if expected_request_id is not None and request_id != expected_request_id:
                return False
            completion = state.get("completion")
            if status == "active":
                # Validate even in the expiry path; invalid state is visible.
                target = state.get("target")
                seconds = state.get("requested_seconds")
                started = state.get("started_epoch")
                deadline = state.get("deadline_epoch")
                now = time.time()
                if (
                    not isinstance(target, str)
                    or target == DELAY_ALARM_CHANNEL
                    or isinstance(seconds, bool)
                    or not isinstance(seconds, int)
                    or seconds < 1
                    or isinstance(started, bool)
                    or not isinstance(started, (int, float))
                    or isinstance(deadline, bool)
                    or not isinstance(deadline, (int, float))
                    or deadline > now
                ):
                    return False
                # Reuse a pre-lock stat only for the exact state/request that
                # produced it. A replacement delay may keep the same expiry
                # window while changing target or request id; never describe
                # that other delay's channel as this completion's current data.
                current = (
                    observed_current
                    if target == observed_target and request_id == observed_request_id
                    else {"present": None}
                )
                initial = state.get("initial")
                completion = {
                    "request_id": request_id,
                    "target": target,
                    "requested_seconds": seconds,
                    "actual_seconds": round(max(0.0, now - float(started)), 3),
                    "expired_at": _delay_iso(now),
                    "changed": _delay_stats_changed(initial, current),
                    "initial": initial if isinstance(initial, dict) else {"present": None},
                    "current": current,
                }
                state["status"] = "expiring"
                state["completion"] = completion
                _write_delay_state_locked(state_path, state)
            if state.get("status") != "expiring" or not isinstance(completion, dict):
                return False
            required = {
                "request_id", "target", "requested_seconds", "actual_seconds",
                "expired_at", "changed", "initial", "current",
            }
            if not required.issubset(completion):
                return False
            from ._fsutil import atomic_write_json

            # Latest-only alarm channel makes the recovery write idempotent.
            atomic_write_json(
                alarm_path, _delay_alarm_payload(completion), ensure_ascii=False,
                indent=None, sort_keys=True, fsync=True,
            )
            state["status"] = "published"
            _write_delay_state_locked(state_path, state)
            return True
    except Exception:
        return False


def _read_active_notification_delay(workdir: str | Path | None) -> dict[str, Any] | None:
    key = _delay_workdir_key(workdir)
    if key is None:
        return None
    try:
        _, state_path, _ = _delay_paths(key)
        active = _active_delay_state(_read_delay_state_locked(state_path), now=time.time())
        if active is None or not is_channel_allowed(active["target"], workdir=key):
            return None
        return active
    except Exception:
        return None


def delayed_notification_target(
    workdir: str | Path | None, store: NotificationStorePort
) -> str | None:
    """Return the one currently delayed target, failing open on bad state.

    Expiry recovery runs first so a due target becomes visible and its alarm is
    created before this consumer snapshot begins. Consumers decide how the
    target is suppressed: ordinary channels are omitted from the coherent read,
    while ``daemon`` keeps its payload and is masked to a constant attention
    token (see ``coherent_attention_read``).
    """
    reconcile_notification_delay(workdir, store)
    active = _read_active_notification_delay(workdir)
    return active.get("target") if active else None


def _cancel_notification_delay_timer(workdir: str) -> None:
    with _DELAY_TIMERS_GUARD:
        existing = _DELAY_TIMERS.pop(workdir, None)
    if existing is not None:
        existing[2].cancel()


def _delay_timer_fired(agent, workdir: str, request_id: str) -> None:
    try:
        reconcile_notification_delay(
            workdir, agent._notification_store, expected_request_id=request_id
        )
        sync = getattr(agent, "_sync_notifications", None)
        if callable(sync):
            sync()
    except Exception:
        # Heartbeat/sync recovery remains the durable backstop.
        pass


def arm_notification_delay_timer(agent) -> None:
    """Arm/re-arm the per-agent process timer from persisted live state.

    Called after a delay action and on every notification sync; refresh/restart
    therefore recreates a missed timer from disk.  The request id guards a stale
    callback after an explicit replacement/cancellation.
    """
    workdir = _delay_workdir_key(getattr(agent, "_working_dir", None))
    if workdir is None:
        return
    reconcile_notification_delay(workdir, agent._notification_store)
    active = _read_active_notification_delay(workdir)
    if active is None:
        _cancel_notification_delay_timer(workdir)
        return
    request_id = active["request_id"]
    deadline = float(active["deadline_epoch"])
    with _DELAY_TIMERS_GUARD:
        existing = _DELAY_TIMERS.get(workdir)
        if existing is not None and existing[0] == request_id and existing[1] == deadline:
            return
        if existing is not None:
            existing[2].cancel()
        timer = threading.Timer(
            max(0.001, deadline - time.time()),
            _delay_timer_fired,
            args=(agent, workdir, request_id),
        )
        timer.daemon = True
        _DELAY_TIMERS[workdir] = (request_id, deadline, timer)
        timer.start()


def delay_notification_channel(agent, channel: str, seconds: int) -> dict[str, Any]:
    """Start, replace, or cancel the one consumer delay for ``agent``.

    Only delay state changes here; the target producer file is neither cleared
    nor rewritten.  On cancellation the next coherent read immediately restores
    the target's ordinary consumer behaviour: an ordinary channel becomes
    visible again, and the ``daemon`` channel — which stays readable throughout
    the delay — regains its ability to move the attention fingerprint.
    """
    workdir = _delay_workdir_key(getattr(agent, "_working_dir", None))
    store = agent._notification_store
    if workdir is None:
        return {
            "status": "error",
            "reason": "delay_requires_workdir",
            "message": "notification delay requires an agent working directory",
        }
    try:
        sync_hook_registry(agent)
        validate_allowed_channel(channel, workdir=_workdir_key(agent))
        if channel == DELAY_ALARM_CHANNEL:
            raise ValueError("delay-alarm is an alarm mirror and cannot be delayed")
        if isinstance(seconds, bool) or not isinstance(seconds, int):
            raise ValueError("seconds must be an integer")
        delay_cap = notification_delay_max_seconds(agent)
        if not 0 <= seconds <= delay_cap:
            raise ValueError(f"seconds must be between 0 and {delay_cap}")
    except ValueError as exc:
        return {
            "status": "error",
            "reason": "invalid_delay",
            "channel": channel,
            "message": str(exc),
        }

    # If a timer was missed, publish its owed alarm before replacing/cancelling
    # the record. Recheck *under the same native lock* below: expiry can race the
    # first recovery call, and overwriting an overdue active record would lose
    # the alarm it owes.
    reconcile_notification_delay(workdir, store)
    # Capture target facts before taking delay scopes. The transaction below
    # validates the delay state again; this observation is informational only.
    initial_target_stats = _read_target_stats(workdir, channel, store) if seconds else None
    for _ in range(2):
        overdue_request_id = None
        try:
            with _delay_transaction(workdir, store):
                _, state_path, _ = _delay_paths(workdir)
                current = _read_delay_state_locked(state_path)
                now = time.time()
                candidate = _active_delay_state(current)
                if (
                    candidate is not None
                    and float(candidate["deadline_epoch"]) <= now
                ):
                    overdue_request_id = candidate["request_id"]
                else:
                    active = _active_delay_state(current, now=now)
                    if seconds == 0:
                        if active is not None and active.get("target") != channel:
                            return {
                                "status": "error",
                                "reason": "delay_target_mismatch",
                                "channel": channel,
                                "active_channel": active.get("target"),
                                "message": "seconds=0 must name the currently delayed channel",
                            }
                        if active is None:
                            result = {
                                "status": "ok",
                                "action": "cancelled",
                                "channel": channel,
                                "cancelled": False,
                            }
                        else:
                            next_state = dict(active)
                            next_state["status"] = "cancelled"
                            next_state["cancelled_at"] = _delay_iso(now)
                            _write_delay_state_locked(state_path, next_state)
                            result = {
                                "status": "ok",
                                "action": "cancelled",
                                "channel": channel,
                                "cancelled": True,
                            }
                    else:
                        request_id = uuid.uuid4().hex
                        state = {
                            "version": 1,
                            "status": "active",
                            "request_id": request_id,
                            "target": channel,
                            "requested_seconds": seconds,
                            "started_epoch": now,
                            "started_at": _delay_iso(now),
                            "deadline_epoch": now + seconds,
                            "deadline_at": _delay_iso(now + seconds),
                            "initial": initial_target_stats or {"present": None},
                        }
                        _write_delay_state_locked(state_path, state)
                        result = {
                            "status": "ok",
                            "action": "delayed",
                            "channel": channel,
                            "seconds": seconds,
                            "request_id": request_id,
                            "deadline_at": state["deadline_at"],
                        }
                        if active is not None:
                            result["replaced_channel"] = active.get("target")
        except OSError as exc:
            return {
                "status": "error",
                "reason": "delay_state_write_failed",
                "channel": channel,
                "message": f"could not persist notification delay: {type(exc).__name__}",
            }
        if overdue_request_id is None:
            break
        if not reconcile_notification_delay(
            workdir, store, expected_request_id=overdue_request_id
        ):
            return {
                "status": "error",
                "reason": "delay_expiry_recovery_pending",
                "channel": channel,
                "message": "an overdue delay must publish its alarm before replacement",
            }
    else:  # pragma: no cover - bounded defense if a racing writer never settles
        return {
            "status": "error",
            "reason": "delay_expiry_recovery_pending",
            "channel": channel,
            "message": "an overdue delay must publish its alarm before replacement",
        }

    if seconds == 0:
        _cancel_notification_delay_timer(workdir)
    else:
        arm_notification_delay_timer(agent)
    try:
        agent._log("notification_delay", channel=channel, seconds=seconds)
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Channel validation
# ---------------------------------------------------------------------------


def validate_channel_name(channel: str) -> None:
    """Validate the syntax of a `.notification/<channel>.json` channel name.

    The notification filesystem treats the channel as a filename stem.
    Generic dismiss accepts agent-supplied channel names, so it validates
    them before constructing a path. Producer-side publish/clear additionally
    validate allowlist membership before touching the filesystem.
    """
    if not isinstance(channel, str) or not channel:
        raise ValueError("channel must be a non-empty string")
    if ".." in channel:
        raise ValueError("channel must not contain '..'")
    if _CHANNEL_RE.fullmatch(channel) is None:
        raise ValueError(
            "channel must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$"
        )


def is_channel_allowed(channel: str, *, workdir: str | None = None) -> bool:
    """Return whether ``channel`` is on the notification allowlist.

    Hook channels are scoped to the owning agent: pass the agent's working
    directory as ``workdir`` to consult its registered-hook mirror. Without a
    workdir (no agent context) hook channels are NOT allowed.
    """
    try:
        validate_channel_name(channel)
    except ValueError:
        return False
    if channel in _NOTIFICATION_CHANNEL_ALLOWLIST:
        return True
    if any(channel.startswith(prefix) for prefix in _NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST):
        return True
    if workdir is None:
        return False
    return channel in _REGISTERED_HOOK_CHANNELS.get(workdir, ())


def validate_allowed_channel(channel: str, *, workdir: str | None = None) -> None:
    """Validate syntax and allowlist membership for a notification channel."""
    validate_channel_name(channel)
    if not is_channel_allowed(channel, workdir=workdir):
        allowed = sorted(_NOTIFICATION_CHANNEL_ALLOWLIST)
        prefixes = list(_NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST)
        raise ValueError(
            "notification channel is not allowlisted: "
            f"{channel!r}; allowed={allowed}; allowed_prefixes={prefixes}"
        )


def register_notification_channel(channel: str) -> None:
    """Allow an in-process producer to register an exact notification channel."""
    validate_channel_name(channel)
    _NOTIFICATION_CHANNEL_ALLOWLIST.add(channel)
    _invalidate_allow_predicates()


# ---------------------------------------------------------------------------
# Daemon channel — batch counting + alarm-threshold attention policy
# ---------------------------------------------------------------------------
#
# All daemon-originated parent notices — cooperative daemon_common checkpoints
# plus terminal/follow-up outcomes — land in independent `.notification/daemon/<daemon-id>.json` mini-channels (not
# `system`), so one batch policy governs every daemon wake. The channel payload
# carries durable batch state under `data.daemon`:
#
#   {"count": <events appended since the last clear>, "alarm_fired": <bool>}
#
# `count` is the batch odometer; `alarm_fired` records that the strict
# `count > alarm_threshold` crossing already produced its single wake edge.
# Clearing the channel drops the file, so the next append starts a new batch and
# can alarm again.
#
# Wake policy is expressed by masking the *attention* fingerprint rather than by
# hiding state: snapshot/check always show the true payload, but sub-threshold
# writes collapse to a constant token so the compared fingerprint does not move
# and no wake/injection fires. Absent a valid configured threshold the raw
# content hash passes through unchanged — usual per-terminal wake behaviour,
# now carried by the daemon channel.

DAEMON_CHANNEL = "daemon"

# `<agent working dir>/notification.json` → channels.daemon.alarm_threshold.
NOTIFICATION_CONFIG_FILENAME = "notification.json"


def daemon_alarm_threshold(workdir: str | None) -> int | None:
    """Return the configured daemon alarm threshold, or ``None`` when unset.

    The threshold is read at this Core policy boundary (never in the Store or
    in the daemon tool) from ``<workdir>/notification.json``:

        {"channels": {"daemon": {"alarm_threshold": 5}}}

    ``None`` means "no threshold configured" and preserves the usual
    per-terminal wake behaviour. A malformed file, a non-integer, a bool, or a
    negative value are all treated as unset — this gate may only ever suppress
    wakes when an operator deliberately asked for it.
    """
    if not workdir:
        return None
    import os

    path = os.path.join(str(workdir), NOTIFICATION_CONFIG_FILENAME)
    try:
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(config, dict):
        return None
    channels = config.get("channels")
    if not isinstance(channels, dict):
        return None
    daemon_config = channels.get(DAEMON_CHANNEL)
    if not isinstance(daemon_config, dict):
        return None
    threshold = daemon_config.get("alarm_threshold")
    if isinstance(threshold, bool) or not isinstance(threshold, int):
        return None
    if threshold < 0:
        return None
    return threshold


def daemon_batch_state(payload: object) -> tuple[int, bool]:
    """Return ``(count, alarm_fired)`` from a daemon channel payload.

    Missing / malformed state reads as a fresh batch, so a hand-written or
    legacy payload degrades to "not yet alarmed" rather than silently
    suppressing a wake.
    """
    if not isinstance(payload, dict):
        return 0, False
    data = payload.get("data")
    if not isinstance(data, dict):
        return 0, False
    state = data.get(DAEMON_CHANNEL)
    if not isinstance(state, dict):
        return 0, False
    count = state.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        count = 0
    return count, state.get("alarm_fired") is True


def next_daemon_batch_state(
    current_payload: object, threshold: int | None
) -> dict:
    """Return the ``data.daemon`` state after appending one event.

    ``count`` is the odometer for this batch (events since the last clear).
    ``alarm_fired`` latches on the strict ``count > threshold`` crossing so the
    edge produces exactly one wake no matter how many further events arrive;
    it stays False while no threshold is configured, where every event wakes
    through the unmasked fingerprint anyway.
    """
    count, alarm_fired = daemon_batch_state(current_payload)
    count += 1
    if threshold is not None and count > threshold:
        alarm_fired = True
    return {"count": count, "alarm_fired": bool(alarm_fired)}


DAEMON_DELAYED_ATTENTION_TOKEN = "daemon:delayed=1"


def daemon_attention_token(
    payload: object, threshold: int | None, *, delayed: bool = False
) -> str | None:
    """Return the attention token for a daemon payload, or ``None`` to pass through.

    ``None`` (no configured threshold, no live delay) keeps the raw content
    hash, so every terminal notice wakes exactly as it did before this channel
    existed.

    ``delayed`` is the live consumer delay whose target is the daemon channel.
    It wins over the threshold and collapses the entry to one constant token
    for the whole bounded delay window: appends, crossings, and clears stay
    readable but cannot move the wake fingerprint. The durable ``alarm_fired``
    latch lives in the payload, so a crossing that happens while delayed still
    alarms once the delay expires — silence is deferred, never dropped.

    With a threshold, the token depends only on whether the strict
    ``count > threshold`` alarm edge has been crossed — never on the event
    bodies. Sub-threshold appends therefore leave the attention fingerprint
    identical (readable, but no wake); the crossing flips the token exactly
    once, and a clear drops the file so the next batch starts over.
    """
    if delayed:
        return DAEMON_DELAYED_ATTENTION_TOKEN
    if threshold is None:
        return None
    count, alarm_fired = daemon_batch_state(payload)
    alarmed = alarm_fired or count > threshold
    return f"daemon:alarm={'1' if alarmed else '0'}"


def apply_daemon_attention_mask(
    fingerprint: tuple,
    snapshot_payload: object,
    threshold: int | None,
    *,
    delayed: bool = False,
) -> tuple:
    """Replace the daemon entry's content hash with its attention token.

    ``fingerprint`` is the Store's ``(name, size, sha256)`` tuple sequence. Only
    the ``daemon.json`` entry is rewritten, and only when a threshold is
    configured or a live consumer delay targets the daemon channel; every other
    channel keeps byte-exact change detection.
    """
    token = daemon_attention_token(snapshot_payload, threshold, delayed=delayed)
    if token is None:
        return tuple(fingerprint)
    name = f"{DAEMON_CHANNEL}.json"
    masked = [
        (entry[0], 0, token) if entry and entry[0] == name else entry
        for entry in fingerprint
    ]
    # Keep the quiet daemon token present even before the channel file exists.
    # Otherwise creating the first sub-threshold daemon.json would change the
    # tuple's shape and spuriously wake the agent.
    if not any(entry and entry[0] == name for entry in masked):
        masked.append((name, 0, token))
    return tuple(sorted(masked, key=lambda entry: entry[0]))


def attention_fingerprint(store, allow_channel, workdir: str | None) -> tuple:
    """Compute and mask the wake-deciding fingerprint for ``store``."""
    return coherent_attention_read(store, allow_channel, workdir).masked_fp


# How many times a coherent read re-tries before accepting the last observation.
# A producer that keeps rewriting the directory faster than two fingerprint
# passes cannot be waited out; giving up after a bounded number of attempts and
# returning the last (fingerprint, payload) pair keeps the tick O(1) and never
# blocks the heartbeat. The accepted pair is always internally consistent for
# the wake decision — only its freshness, never its coherence, is sacrificed.
_COHERENT_READ_ATTEMPTS = 3


class CoherentAttentionRead(NamedTuple):
    """One internally consistent observation of the notification directory.

    ``raw_fp``, ``masked_fp`` and ``payloads`` all describe the *same* instant:
    ``raw_fp`` is the byte-exact Store fingerprint of the bytes in ``payloads``,
    and ``masked_fp`` is the daemon-attention mask applied to that same
    observation. Holding the three together is what makes them usable as a
    delivered-version token: a caller may deliver ``payloads`` to the model and
    later compare-and-swap against ``raw_fp``, knowing the version describes the
    bytes the model actually saw.

    ``stable`` records whether the observation was confirmed unchanged by a
    verifying re-read. ``False`` means a producer was writing throughout the
    read and the pair is the last attempt rather than a confirmed-quiet one;
    callers that must not lose an alarm edge treat an unstable read as "state is
    moving", never as evidence of quiet.
    """

    raw_fp: tuple
    masked_fp: tuple
    payloads: dict
    stable: bool


def coherent_attention_read(
    store, allow_channel, workdir: str | None
) -> CoherentAttentionRead:
    """Read fingerprint and payloads as one coherent observation.

    The Store Port deliberately exposes no atomic snapshot-plus-version
    primitive: ``fingerprint()`` and ``snapshot()`` are independent unlocked
    reads (see ``notification_store/CONTRACT.md``). Composing them naively tears
    under a concurrent producer, and every consumer of this module needs the
    pair to agree:

    * **Delivery / CAS.** ``meta_block`` hands ``payloads`` to the model and
      commits ``raw_fp`` as the delivered version. If the version came from a
      *later* independent read, a publish landing in between would let a
      non-forced ``dismiss_channel`` clear bytes the agent never saw.
    * **Daemon attention masking.** The mask needs the daemon payload that
      matches the fingerprint it is rewriting. Reading the payload separately
      lets an alarmed write be observed by the fingerprint pass and a clear by
      the payload pass, rewriting the alarmed entry to the quiet token — the
      alarm edge would vanish with no file left to replay it. A live daemon
      delay uses that same one seam, so daemon quiet has exactly one
      implementation whatever asked for it.

    Verify-and-retry supplies the missing atomicity without a Port change and
    without a lock on the read path: fingerprint, snapshot, then fingerprint
    again. Matching bookend fingerprints prove nothing was rewritten in between,
    so the observation is coherent. A mismatch means a producer wrote during the
    read; retry on the fresher fingerprint. After ``_COHERENT_READ_ATTEMPTS``
    the last observation is returned with ``stable=False`` rather than looping.

    Store errors degrade the way the rest of this module does — toward waking,
    never toward silence: a failed snapshot yields the raw fingerprint unmasked
    (so a below-threshold daemon entry reads as a change and wakes) and an empty
    payload map marked unstable, so no caller mistakes the failure for "the
    channels are quiet".
    """
    # First reconcile any elapsed persisted delay.  If the timer was missed by
    # refresh/restart, reconciliation publishes the alarm and turns filtering
    # off before this single consumer observation is built, so target visibility
    # and the high-priority alarm arrive in the same sync/wake cycle.
    delayed_target = delayed_notification_target(workdir, store)
    # A delayed `daemon` target is masked, not hidden: it keeps its payload and
    # its byte-exact raw entry (truth, the bounded summary, and the delivered
    # version a non-forced dismissal compares against) and only loses its
    # ability to move the wake-deciding fingerprint. Every other target keeps
    # the established hide-the-channel delay semantics.
    daemon_delayed = delayed_target == DAEMON_CHANNEL
    hidden_target = None if daemon_delayed else delayed_target

    def _consumer_allow(channel: str) -> bool:
        return allow_channel(channel) and channel != hidden_target

    threshold = daemon_alarm_threshold(workdir)
    raw_fp: tuple = ()
    payloads: dict = {}
    stable = False

    for _ in range(_COHERENT_READ_ATTEMPTS):
        try:
            before = tuple(store.fingerprint(_consumer_allow))
            payloads = store.snapshot(_consumer_allow)
            after = tuple(store.fingerprint(_consumer_allow))
        except Exception:
            # Read failure: report the last known fingerprint with no payloads
            # and unstable, so masking is skipped and quiet is never inferred.
            return CoherentAttentionRead(raw_fp, raw_fp, {}, False)
        raw_fp = after
        if before == after:
            stable = True
            break

    # When `stable` is False the directory was still moving; the pair below is
    # the last attempt rather than a confirmed one, and callers are told so.
    masked_fp = (
        tuple(raw_fp)
        if threshold is None and not daemon_delayed
        else apply_daemon_attention_mask(
            raw_fp, payloads.get(DAEMON_CHANNEL), threshold, delayed=daemon_delayed
        )
    )
    return CoherentAttentionRead(tuple(raw_fp), masked_fp, payloads, stable)


def masked_empty_attention_fp(workdir: str | None) -> tuple:
    """Return the masked fingerprint of an *empty* notification directory.

    With a daemon alarm threshold configured, ``apply_daemon_attention_mask``
    keeps a virtual quiet ``daemon.json`` token present whether or not the file
    exists, so the masked fingerprint of "no channels at all" is not ``()`` but
    that single quiet entry. An agent that has never synced starts at ``()``,
    which would read as a change the first time a sub-threshold ``daemon.json``
    appears — injecting and waking for exactly the arrival the threshold exists
    to keep quiet.

    Seeding the baseline with this value makes first-sight-of-a-quiet-channel a
    no-op while leaving every alarmed arrival (threshold ``0``, or a payload
    already past ``count > N``) a genuine change, because its token is
    ``daemon:alarm=1``. Absent a configured threshold there is no virtual entry
    and the empty baseline stays ``()``.

    A live daemon delay resolves the same way through the same mask, so a
    refresh/restart during the bounded delay window does not wake the agent for
    the daemon state it just asked to stay quiet about. The delay record is
    read directly (never reconciled here): the caller's coherent read already
    published any owed alarm, and an expiry racing this baseline only fails
    toward waking.
    """
    threshold = daemon_alarm_threshold(workdir)
    active = _read_active_notification_delay(workdir)
    delayed = bool(active) and active.get("target") == DAEMON_CHANNEL
    if threshold is None and not delayed:
        return ()
    return apply_daemon_attention_mask((), None, threshold, delayed=delayed)


def agent_attention_fingerprint(agent) -> tuple:
    """``attention_fingerprint`` bound to an agent's store / allowlist scope."""
    workdir = _workdir_key(agent)
    return attention_fingerprint(
        agent._notification_store,
        lambda ch: is_channel_allowed(ch, workdir=workdir),
        workdir,
    )


# ---------------------------------------------------------------------------
# Hook registry — external-hook whitelist (family 8 of the Notification Store)
# ---------------------------------------------------------------------------


def _workdir_key(agent) -> str | None:
    """Return the module-registry key for an agent (its working directory).

    ``None`` when the agent has no working directory, so hook channels are
    never allowlisted for agents without a real workdir.
    """
    workdir = getattr(agent, "_working_dir", None)
    if workdir is None:
        return None
    return str(workdir)


def sync_hook_registry(agent) -> None:
    """Seed the module-level hook-channel mirror from ``.notification/hooks.json``.

    Re-seeds whenever the registry file's ``(mtime_ns, size)`` changes, so an
    out-of-band write from another process (sibling CLI, Telegram server, hook
    installer) is picked up without re-reading the file on every tick. The
    seeded marker is only set after a successful load; a transient store
    failure is logged and retried on the next sync.
    """
    workdir = _workdir_key(agent)
    if workdir is None:
        # Workdir-less agents can never consult the mirror (both
        # _build_allow_predicate and is_channel_allowed short-circuit on a
        # None workdir), so seeding it would only write dead entries keyed by
        # None. Skip seeding entirely.
        return
    store = getattr(agent, "_notification_store", None)
    try:
        current_stat = store.stat_hook_registry()
    except Exception:
        current_stat = None
    with _HOOK_REGISTRY_LOCK:
        if _HOOK_REGISTRY_STAT.get(workdir) == current_stat and workdir in _HOOK_REGISTRY_SEEDED:
            return
        try:
            manifests = store.load_hook_manifests()
        except Exception as exc:
            _log_hook_registry_failure(agent, "load", exc)
            return
        channels = {
            m.get("channel")
            for m in manifests
            if isinstance(m, dict) and isinstance(m.get("channel"), str)
        }
        channels = {c for c in channels if c}
        _REGISTERED_HOOK_CHANNELS[workdir] = channels
        _HOOK_REGISTRY_STAT[workdir] = current_stat
        _HOOK_REGISTRY_SEEDED.add(workdir)
        _invalidate_allow_predicates()


def _log_hook_registry_failure(agent, phase: str, exc: Exception) -> None:
    try:
        agent._log(
            "notification_hook_registry_error",
            phase=phase,
            error=str(exc)[:300],
        )
    except Exception:
        pass


def _update_hook_registry(agent, mutator) -> object:
    """Run a pure manifest-list mutation under the store lock and re-mirror.

    Returns the mutator's policy value. Any exception from the store propagates
    to the caller; the mirror is refreshed only on success so a failed write
    never leaves in-memory state ahead of disk.
    """
    store = agent._notification_store
    result = store.update_hook_manifests(mutator)
    workdir = _workdir_key(agent)
    with _HOOK_REGISTRY_LOCK:
        _HOOK_REGISTRY_SEEDED.discard(workdir)
        _HOOK_REGISTRY_STAT.pop(workdir, None)
    sync_hook_registry(agent)
    return result.value


def _manifest_channel(manifest: dict) -> str | None:
    channel = manifest.get("channel")
    return channel if isinstance(channel, str) and channel else None


_REQUIRED_HOOK_FIELDS = (
    "name",
    "channel",
    "source",
    "description",
    "how_to_modify",
    "how_to_cancel",
)


def _validate_hook_manifest(manifest: dict) -> None:
    """Validate a hook manifest's required fields and channel syntax."""
    if not isinstance(manifest, dict):
        raise ValueError("hook manifest must be a JSON object")
    for field in _REQUIRED_HOOK_FIELDS:
        value = manifest.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"hook manifest requires a non-empty string '{field}'")
    channel = _manifest_channel(manifest)
    validate_channel_name(channel)
    if channel in _NOTIFICATION_CHANNEL_ALLOWLIST:
        raise ValueError(
            f"channel '{channel}' is a built-in notification channel; "
            "pick a hook-owned name"
        )
    from .notification_store import STORE_RESERVED_NON_CHANNEL_STEMS

    if channel in STORE_RESERVED_NON_CHANNEL_STEMS:
        raise ValueError(
            f"channel '{channel}' is reserved by the notification store"
        )


def _find_manifest(manifests: list[dict], name: str) -> int | None:
    for idx, manifest in enumerate(manifests):
        if isinstance(manifest, dict) and manifest.get("name") == name:
            return idx
    return None


def add_hook(agent, manifest: dict) -> dict:
    """Register a new external-hook manifest (notification add).

    Validates the manifest, checks the channel is not already used by another
    hook, appends it to the disk registry, and refreshes the module mirror.
    Returns a result dict with status / reason / value.
    """
    _validate_hook_manifest(manifest)
    name = manifest["name"]
    channel = _manifest_channel(manifest)

    def _mutator(current: list[dict]) -> tuple[list[dict], bool, object]:
        if _find_manifest(current, name) is not None:
            return current, False, {"reason": "duplicate_name", "name": name}
        for existing in current:
            if _manifest_channel(existing) == channel:
                return current, False, {
                    "reason": "channel_in_use",
                    "channel": channel,
                    "name": existing.get("name"),
                }
        return current + [dict(manifest)], True, {"reason": "added", "name": name}

    try:
        value = _update_hook_registry(agent, _mutator)
    except (json.JSONDecodeError, OSError) as exc:
        # A corrupt or unreadable registry must not be relabelled as an input
        # error (JSONDecodeError is a ValueError, which the tool layer's
        # validation catch would swallow) nor escape as a raw OSError —
        # surface the same structured result ``list_hooks`` returns.
        return _hook_registry_load_failed(exc)
    if value.get("reason") == "added":
        clear_blocked_channel_warning(agent, channel)
        return {"status": "ok", **value}
    return {"status": "error", **value}


def edit_hook(agent, name: str, fields: dict) -> dict:
    """Update a registered hook's fields (notification edit).

    ``fields`` must be a subset of the editable manifest fields. Changing the
    channel re-validates uniqueness. Returns result with status / reason / name.
    """
    if not name:
        raise ValueError("edit_hook requires a hook name")
    editable = {
        "version",
        "source",
        "description",
        "channel",
        "how_to_modify",
        "how_to_cancel",
        "instructions",
    }
    unknown = set(fields) - editable
    if unknown:
        raise ValueError(f"not editable hook fields: {sorted(unknown)}")
    provided = {k: v for k, v in fields.items() if v is not None}
    if not provided:
        return {"status": "ok", "reason": "no_change", "name": name}

    def _mutator(current: list[dict]) -> tuple[list[dict], bool, object]:
        idx = _find_manifest(current, name)
        if idx is None:
            return current, False, {"reason": "not_found", "name": name}
        updated = dict(current[idx])
        updated.update(provided)
        # Validate the merged manifest (catches bad channel edits).
        try:
            _validate_hook_manifest(updated)
        except ValueError as exc:
            # Same reason add_hook uses for the identical refusal.
            return current, False, {"reason": "invalid_manifest", "message": str(exc)}
        new_channel = _manifest_channel(updated)
        for idx2, existing in enumerate(current):
            if idx2 == idx:
                continue
            if _manifest_channel(existing) == new_channel:
                return current, False, {
                    "reason": "channel_in_use",
                    "channel": new_channel,
                    "name": existing.get("name"),
                }
        next_manifests = list(current)
        next_manifests[idx] = updated
        return next_manifests, True, {"reason": "edited", "name": name}

    try:
        value = _update_hook_registry(agent, _mutator)
    except (json.JSONDecodeError, OSError) as exc:
        # Same structured result as ``list_hooks`` — never an input-validation
        # error and never a raw raise (see add_hook).
        return _hook_registry_load_failed(exc)
    if value.get("reason") == "edited":
        new_channel = provided.get("channel")
        if new_channel is not None:
            clear_blocked_channel_warning(agent, new_channel)
        return {"status": "ok", **value}
    return {"status": "error", **value}


def drop_hook(agent, name: str) -> dict:
    """Remove a registered hook and revoke its channel (notification drop).

    Only removes the registration evidence; the hook process itself is the
    owner's job (documented in ``how_to_cancel``).
    """
    if not name:
        raise ValueError("drop_hook requires a hook name")

    def _mutator(current: list[dict]) -> tuple[list[dict], bool, object]:
        idx = _find_manifest(current, name)
        if idx is None:
            return current, False, {"reason": "not_found", "name": name}
        next_manifests = list(current)
        next_manifests.pop(idx)
        return next_manifests, True, {"reason": "dropped", "name": name}

    try:
        value = _update_hook_registry(agent, _mutator)
    except (json.JSONDecodeError, OSError) as exc:
        # Same structured result as ``list_hooks`` — never an input-validation
        # error and never a raw raise (see add_hook).
        return _hook_registry_load_failed(exc)
    if value.get("reason") == "dropped":
        return {"status": "ok", **value}
    return {"status": "error", **value}


def _hook_registry_load_failed(exc: Exception) -> dict[str, object]:
    """Structured error shared by list/add/drop/edit for a broken registry.

    A corrupt (invalid-JSON) or unreadable ``hooks.json`` must never be
    reported as "nothing registered" nor mislabelled as an input-validation
    error (``json.JSONDecodeError`` is a ``ValueError``), nor escape as a raw
    ``OSError``.
    """
    return {
        "status": "error",
        "reason": "hook_registry_load_failed",
        "message": f"Could not load hooks.json: {str(exc)[:300]}",
    }


def list_hooks(agent) -> list[dict] | dict[str, object]:
    """Return the registered hook manifests for this agent (notification list).

    A store load failure is surfaced as a structured ``status: error`` result
    (instead of an empty list) so a corrupt ``hooks.json`` is distinguishable
    from "nothing registered" while the agent debugs it.
    """
    try:
        return agent._notification_store.load_hook_manifests()
    except Exception as exc:
        return _hook_registry_load_failed(exc)


def flag_unregistered_channel(agent, channel: str) -> None:
    """Emit a warn-and-flag system event for a blocked unregistered channel.

    D2 behavior: the channel's notification does NOT pass through, but the
    attempt becomes observable so the agent can investigate and add the hook
    if legitimate. Deduped per workdir+channel until the channel registers.
    """
    workdir = _workdir_key(agent)
    if is_channel_allowed(channel, workdir=workdir):
        return
    with _HOOK_REGISTRY_LOCK:
        warned = _BLOCKED_CHANNEL_WARNED.setdefault(workdir, set())
        if channel in warned:
            return
        warned.add(channel)
    try:
        agent._enqueue_system_notification(
            source="notification_hook",
            ref_id=f"blocked_channel:{channel}",
            body=(
                f"Channel '{channel}' tried to notify you but is not registered. "
                "Notifications from unregistered channels do not pass through. "
                "Run notification(action='list') to inspect hooks, or "
                "notification(action='add', ...) to register this hook if it "
                "is legitimate."
            ),
            skip_if_ref_id_exists=True,
        )
    except Exception:
        pass


def is_present_channel_flagable(name: str) -> bool:
    """Return whether a present ``.notification`` filename should be D2-flagged.

    Skips kernel-private dotfiles (``.nudge_state.json``), non-``.json``
    entries, and stems that fail channel-name validation, so the D2 scan never
    emits an unresolvable "register this hook" event for files that cannot
    become channels.
    """
    if not name.endswith(".json"):
        return False
    stem = name[: -len(".json")]
    if stem.startswith("."):
        return False
    try:
        validate_channel_name(stem)
    except ValueError:
        return False
    return True


def clear_blocked_channel_warning(agent, channel: str) -> None:
    """Drop the warn-and-flag dedupe marker when a channel registers."""
    with _HOOK_REGISTRY_LOCK:
        warned = _BLOCKED_CHANNEL_WARNED.get(_workdir_key(agent))
        if warned:
            warned.discard(channel)


def reset_hook_registry_for_tests() -> None:
    """Clear module-level hook registry state (test isolation)."""
    with _HOOK_REGISTRY_LOCK:
        _REGISTERED_HOOK_CHANNELS.clear()
        _HOOK_REGISTRY_SEEDED.clear()
        _HOOK_REGISTRY_STAT.clear()
        _BLOCKED_CHANNEL_WARNED.clear()
        _invalidate_allow_predicates()


def register_generic_dismiss_guard(channel: str, suggested_verb: str) -> None:
    """Guard a channel against accidental generic dismissal.

    Category-A producers (notifications that mirror durable producer state)
    call this at import time. Duplicate registration is idempotent; the
    newest suggested verb wins so producers can refine guidance.
    """
    validate_channel_name(channel)
    _GENERIC_DISMISS_GUARDED[channel] = str(suggested_verb)


def is_generic_dismiss_guarded(channel: str) -> str | None:
    """Return the producer-specific suggested verb if guarded."""
    return _GENERIC_DISMISS_GUARDED.get(channel)


# ---------------------------------------------------------------------------
# Producer-facing submit — canonical "submit a notification" entry point
# ---------------------------------------------------------------------------


def submit(
    agent,
    tool_name: str,
    *,
    data: dict,
    header: str,
    icon: str = "🔔",
    priority: str = "normal",
    instructions: str | None = None,
) -> None:
    """Submit a notification with the standard envelope.

    This is the canonical entry point for in-process producers.  It
    wraps ``agent._notification_store.publish()`` with the envelope shape
    documented in the design (``notification-filesystem-redesign.md`` §2.1.3)
    and stamps ``published_at`` automatically.

    *agent* must have a ``_notification_store`` attribute.

    Args:
        agent: The agent instance.
        tool_name: The producer's namespace key — ``email``, ``soul``,
            ``system``, ``mcp.<server>``, …  This becomes both the file
            basename (``<tool_name>.json``) AND the dict key the agent
            sees when it reads ``notification(action="check")``.
        data: Structured payload the agent will read.  No restrictions
            on shape — producers decide.
        header: One-line glanceable summary used by frontends (TUI
            status bar, portal cards) for compact rendering.
        icon: Optional glyph for status indicators.  Defaults to 🔔;
            common conventions: 📧 (mail), 🌊 (soul), 💬 (chat), …
        priority: ``"low"``, ``"normal"``, or ``"high"``.  Frontends
            may surface high-priority notifications more prominently.
        instructions: Optional agent-facing directive describing how to
            dismiss or act on this notification.
    """
    validate_allowed_channel(tool_name, workdir=_workdir_key(agent))

    payload = {
        "header": header,
        "icon": icon,
        "priority": priority,
        "published_at": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "data": data,
    }
    if instructions is not None:
        payload["instructions"] = instructions

    store = agent._notification_store
    store.publish(tool_name, payload)


# ---------------------------------------------------------------------------
# Producer-facing clear
# ---------------------------------------------------------------------------


def clear(agent, tool_name: str) -> None:
    """Delete a producer's notification file.  Idempotent (best-effort).

    Producers call this when their state empties (e.g. mail's unread
    count drops to 0).  Deletion changes the directory fingerprint, so
    the kernel's next sync tick will strip the wire's notification block.

    Errors other than FileNotFoundError are silently suppressed — this
    is the existing best-effort contract for producers.
    """
    validate_allowed_channel(tool_name, workdir=_workdir_key(agent))
    store = agent._notification_store
    try:
        store.clear(tool_name)
    except OSError:
        pass


# Back-compat: clear_notification alias used by some external paths.
clear_notification = clear


def clear_with_result(agent, channel: str) -> bool:
    """Delete a notification file and report whether it existed.

    Unlike ``clear``, this helper is strict: only a missing file is an
    idempotent no-op. Other ``OSError`` subclasses propagate to the caller
    so agent-facing dismiss can surface honest failures.
    """
    validate_allowed_channel(channel, workdir=_workdir_key(agent))
    store = agent._notification_store
    return store.clear(channel)


# ---------------------------------------------------------------------------
# Large-result ack helpers (Core policy on top of store)
# ---------------------------------------------------------------------------


def ack_large_result_refs(agent, ref_ids: set[str]) -> None:
    """Atomically union *ref_ids* into persistent acknowledgements."""
    def _union(current: set[str]) -> tuple[set[str], bool, None]:
        updated = current | ref_ids
        return updated, updated != current, None

    agent._notification_store.update_ack_refs(_union)


def purge_stale_large_result_acks(agent, current_ref_ids: set[str]) -> None:
    """Atomically retain acknowledgements still present in the live ref set."""
    def _purge(current: set[str]) -> tuple[set[str], bool, None]:
        updated = current & current_ref_ids
        return updated, updated != current, None

    agent._notification_store.update_ack_refs(_purge)


# ---------------------------------------------------------------------------
# Core RMW helpers — atomic channel mutation via compare_update_channel
# ---------------------------------------------------------------------------


def clear_large_result_reminders(agent, tool_call_ids) -> list[str]:
    """Remove large-result reminder events for *tool_call_ids* from system.json.

    Uses the store's serialized compare_update_channel so no external lock is
    needed.  The list of removed ref_ids is returned through the result's
    policy value (``result.value``) — no impure side channels.
    """
    from .notification_store import UNCONDITIONAL

    wanted_ref_ids = {
        f"large_tool_result:{tcid}" for tcid in tool_call_ids if tcid
    }
    if not wanted_ref_ids:
        return []

    store = agent._notification_store
    published_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _mutator(current_payload: dict) -> tuple[dict | None, bool, list[str]]:
        system = current_payload if isinstance(current_payload, dict) else {}
        data_obj = system.get("data")
        events = data_obj.get("events", []) if isinstance(data_obj, dict) else []
        if not isinstance(events, list):
            return current_payload, False, []

        def _is_target(ev: object) -> bool:
            return (
                isinstance(ev, dict)
                and ev.get("source") == "large_tool_result"
                and ev.get("ref_id") in wanted_ref_ids
            )

        removed = [r for r in (ev.get("ref_id") for ev in events if _is_target(ev)) if r]
        if not removed:
            return current_payload, False, []
        kept = [ev for ev in events if not _is_target(ev)]
        if kept:
            new_payload = dict(system)
            new_data = dict(new_payload.get("data", {}))
            new_data["events"] = kept
            new_payload["data"] = new_data
            new_payload["header"] = (
                f"{len(kept)} system notification"
                f"{'s' if len(kept) != 1 else ''}"
            )
            new_payload["published_at"] = published_at
            return new_payload, True, removed
        else:
            return None, True, removed  # clear channel

    result = store.compare_update_channel("system", UNCONDITIONAL, _mutator)
    removed: list[str] = result.value if result.applied and isinstance(result.value, list) else []

    if removed and result.applied:
        _safe_log(
            agent,
            "large_result_reminder_cleared_by_summarize",
            removed_ref_ids=removed,
        )
    return removed


def _safe_log(agent, event_type: str, **fields) -> None:
    """Best-effort agent log helper for dismissal housekeeping."""
    try:
        agent._log(event_type, **fields)
    except Exception:
        pass


def _system_events(payload: object) -> list:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    events = data.get("events", []) if isinstance(data, dict) else []
    return events if isinstance(events, list) else []


def _system_payload_with_events(current: dict, events: list, published_at: str):
    if not events:
        return None
    payload = dict(current)
    data = payload.get("data")
    data = dict(data) if isinstance(data, dict) else {}
    data["events"] = events
    payload["data"] = data
    payload["header"] = f"{len(events)} system notification{'s' if len(events) != 1 else ''}"
    payload["published_at"] = published_at
    return payload


def _resolve_worker_hang_refs(
    agent,
    removed_events: list,
    *,
    reason: str,
) -> list[str]:
    """Best-effort artifact resolution for removed worker-hang system events."""
    try:
        from .base_agent.worker_recovery import (
            is_worker_hang_ref,
            resolve_worker_hang_artifact,
        )
    except Exception:
        return []

    refs = sorted({
        str(ev.get("ref_id"))
        for ev in removed_events
        if isinstance(ev, dict) and is_worker_hang_ref(ev.get("ref_id"))
    })
    resolved: list[str] = []
    for ref in refs:
        try:
            if resolve_worker_hang_artifact(agent, ref, reason=reason or "dismissed"):
                resolved.append(ref)
        except Exception:
            pass
    return resolved


def _channel_fingerprint_entry(fp: tuple | None, channel: str) -> tuple | None:
    """Return one channel's fingerprint entry from a directory fingerprint."""
    filename = f"{channel}.json"
    for entry in fp or ():
        try:
            if entry[0] == filename:
                return tuple(entry)
        except (IndexError, TypeError):
            continue
    return None


def _safe_version(entry: tuple | None) -> list | None:
    """Return a JSON/log-safe fingerprint representation."""
    return list(entry) if entry is not None else None


def _stale_channel_refusal(
    agent,
    channel: str,
    *,
    invoked_by: str,
    delivered: tuple | None,
    current: list | tuple | None,
) -> dict:
    delivered_version = _safe_version(delivered)
    current_version = _safe_version(current)
    try:
        agent._log(
            "notification_dismiss_refused",
            reason="stale_channel_version",
            channel=channel,
            invoked_by=invoked_by,
            forced=False,
            delivered_version=delivered_version,
            current_version=current_version,
        )
        if invoked_by == "system":
            agent._log(
                "system_dismiss_refused",
                reason="stale_channel_version",
                channel=channel,
                forced=False,
                delivered_version=delivered_version,
                current_version=current_version,
            )
    except Exception:
        pass
    return {
        "status": "error",
        "reason": "stale_channel_version",
        "channel": channel,
        "forced": False,
        "delivered_version": delivered_version,
        "current_version": current_version,
        "message": (
            f"Channel '{channel}' changed after the delivered notification "
            "version. Read the current notification state before dismissing, "
            "or pass force=true to knowingly clear it."
        ),
    }


# Machine-readable ``cause`` values for ``cleared: false`` no-op dismiss
# results (#716). Each constant is stamped by the exact branch that decides
# the no-op, never recomputed elsewhere: ``already_empty`` keys on
# ``update.cleared`` from ``NotificationStorePort.compare_update_channel``
# (whole-channel clear of an already-empty channel), ``no_matching_event``
# keys on the event mutator's ``removed == 0`` count (event_id/ref_id
# matched nothing). Stale-version refusals are a separate ``status: error``
# contract (``_stale_channel_refusal``) and never carry ``cause``.
DISMISS_CAUSE_ALREADY_EMPTY = "already_empty"
DISMISS_CAUSE_NO_MATCHING_EVENT = "no_matching_event"


def dismiss_channel(
    agent,
    channel: str,
    *,
    invoked_by: str,
    force: bool = False,
    reason: str | None = None,
    event_id: str | None = None,
    ref_id: str | None = None,
) -> dict:
    """Shared agent-facing notification dismissal helper.

    Used by the standalone ``notification`` tool's atomic dismiss verbs
    (``dismiss_channel``/``dismiss_event``/``dismiss_ref``, all with
    ``invoked_by="notification"``) and the ``soul(action="dismiss")``
    convenience alias.

    Generic dismiss clears only the notification surface; producer-owned state
    is untouched.
    """
    try:
        validate_allowed_channel(channel, workdir=_workdir_key(agent))
    except ValueError as e:
        try:
            agent._log(
                "notification_dismiss_invalid",
                channel=str(channel)[:100],
                invoked_by=invoked_by,
                error=str(e),
            )
        except Exception:
            pass
        return {
            "status": "error",
            "reason": "invalid_channel",
            "channel": channel,
            "message": str(e),
        }

    ack_reason = (reason or "").strip()
    if channel == "post-molt" and not ack_reason:
        try:
            agent._log(
                "notification_dismiss_missing_reason",
                channel=channel,
                invoked_by=invoked_by,
            )
        except Exception:
            pass
        return {
            "status": "error",
            "reason": "missing_ack_reason",
            "channel": channel,
            "message": (
                "post-molt continuation reminders require an acknowledgement "
                "reason. Use reason='<continue|defer|obsolete>: ...'."
            ),
        }

    protected_message = _PROTECTED_GENERIC_DISMISS.get(channel)
    if protected_message:
        try:
            agent._log(
                "notification_dismiss_protected",
                channel=channel,
                invoked_by=invoked_by,
                forced=bool(force),
            )
            if invoked_by == "system":
                agent._log(
                    "system_dismiss_protected",
                    channel=channel,
                    forced=bool(force),
                )
        except Exception:
            pass
        return {
            "status": "error",
            "reason": "protected_channel",
            "channel": channel,
            "message": protected_message,
        }

    if (event_id or ref_id) and channel not in {"system", DAEMON_CHANNEL}:
        return {
            "status": "error",
            "reason": "atomic_dismiss_requires_system_channel",
            "channel": channel,
            "event_id": event_id,
            "ref_id": ref_id,
            "message": "event_id/ref_id dismiss is only supported for channel='system' or 'daemon'.",
        }

    suggested = is_generic_dismiss_guarded(channel)
    if suggested and not force:
        try:
            if invoked_by == "system":
                agent._log(
                    "system_dismiss_guarded",
                    channel=channel,
                    suggested_verb=suggested,
                )
            agent._log(
                "notification_dismiss_guarded",
                channel=channel,
                invoked_by=invoked_by,
                suggested_verb=suggested,
            )
        except Exception:
            pass
        return {
            "status": "error",
            "reason": "guarded",
            "channel": channel,
            "suggested_verb": suggested,
            "message": (
                f"Channel '{channel}' mirrors producer-owned state; use {suggested} "
                "or pass force=true only when knowingly clearing a stale mirror."
            ),
        }

    from .notification_store import UNCONDITIONAL

    store = agent._notification_store
    # The optimistic-concurrency token must be the RAW delivered fingerprint,
    # never the daemon-attention-masked one: compare_update_channel always
    # compares against the real on-disk (name, size, sha256) triple, and the
    # masked entry (e.g. `("daemon.json", 0, "daemon:alarm=0")`) can never
    # equal that, which would make every non-forced dismiss of a
    # threshold-masked channel refuse as stale regardless of how current the
    # agent's read was. See notifications.py's daemon-channel docstring and
    # meta_block._commit_notification_fp.
    delivered = _channel_fingerprint_entry(
        getattr(agent, "_notification_raw_fp", ()), channel
    )
    expected = UNCONDITIONAL if force else delivered
    published_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _clear_current_channel() -> dict:
        def _mutator(current_payload: dict):
            events = _system_events(current_payload) if channel == "system" else []
            large_ref_ids = tuple(sorted({
                str(ev.get("ref_id")) for ev in events
                if _is_large_result_event(ev) and ev.get("ref_id")
            }))
            goal_removed = any(
                isinstance(ev, dict)
                and ev.get("source") == "goal.reminder"
                and str(ev.get("ref_id", "")).startswith("goal:")
                for ev in events
            )
            return None, True, (large_ref_ids, goal_removed, events)

        try:
            update = store.compare_update_channel(channel, expected, _mutator)
        except OSError as e:
            try:
                agent._log(
                    "notification_dismiss_error",
                    channel=channel,
                    invoked_by=invoked_by,
                    forced=bool(force),
                    error=str(e)[:200],
                )
            except Exception:
                pass
            return {
                "status": "error",
                "reason": "clear_failed",
                "channel": channel,
                "message": str(e),
            }
        if update.conflict:
            return _stale_channel_refusal(
                agent, channel, invoked_by=invoked_by, delivered=delivered,
                current=update.current_version,
            )

        large_ref_ids, goal_reminder_cleared_by_whole_system_dismiss, removed_events = (
            update.value if isinstance(update.value, tuple) and len(update.value) == 3
            else ((), False, [])
        )
        existed = update.cleared
        resolved_worker_refs = (
            _resolve_worker_hang_refs(agent, removed_events, reason=ack_reason)
            if channel == "system" and existed
            else []
        )
        if large_ref_ids:
            try:
                ack_large_result_refs(agent, set(large_ref_ids))
            except Exception:
                pass
            _safe_log(
                agent, "large_result_reminder_dismissed", channel=channel,
                invoked_by=invoked_by, forced=bool(force),
                acked_ref_ids=list(large_ref_ids), event_id=None, ref_id=None,
            )
        if existed and goal_reminder_cleared_by_whole_system_dismiss:
            try:
                import time as _time
                agent._goal_reminder_last_dismissed_at = _time.time()
            except Exception:
                pass

        try:
            agent._log(
                "notification_dismiss",
                channel=channel,
                invoked_by=invoked_by,
                existed=existed,
                forced=bool(force),
                reason=ack_reason or None,
            )
            if invoked_by == "system":
                agent._log(
                    "system_dismiss",
                    channel=channel,
                    existed=existed,
                    forced=bool(force),
                    reason=ack_reason or None,
                )
            elif invoked_by == "soul":
                agent._log("soul_dismiss")
        except Exception:
            pass

        result = {
            "status": "ok",
            "channel": channel,
            "cleared": existed,
            "forced": bool(force),
        }
        if not existed:
            result["cause"] = DISMISS_CAUSE_ALREADY_EMPTY
        if ack_reason:
            result["reason"] = ack_reason
        if large_ref_ids:
            result["acked_large_result_refs"] = list(large_ref_ids)
            result["note"] = _LARGE_RESULT_DISMISS_NOTE
        if resolved_worker_refs:
            result["resolved_worker_hang_refs"] = resolved_worker_refs
        return result

    def _dismiss_system_event() -> dict:
        if not (event_id or ref_id):
            return _clear_current_channel()

        def _match(ev: object) -> bool:
            if not isinstance(ev, dict):
                return False
            return bool(
                (event_id and ev.get("event_id") == event_id)
                or (ref_id and ev.get("ref_id") == ref_id)
            )

        def _mutator(current_payload: dict):
            events = _system_events(current_payload)
            removed_events = [ev for ev in events if _match(ev)]
            kept = [ev for ev in events if not _match(ev)]
            large_ref_ids = tuple(sorted({
                str(ev.get("ref_id")) for ev in removed_events
                if _is_large_result_event(ev) and ev.get("ref_id")
            }))
            goal_removed = any(
                isinstance(ev, dict)
                and ev.get("source") == "goal.reminder"
                and str(ev.get("ref_id", "")).startswith("goal:")
                for ev in removed_events
            )
            value = (
                len(removed_events),
                len(kept),
                large_ref_ids,
                goal_removed,
                removed_events,
            )
            if not removed_events:
                return current_payload, False, value
            return _system_payload_with_events(
                current_payload, kept, published_at
            ), True, value

        try:
            update = store.compare_update_channel(channel, expected, _mutator)
        except OSError as e:
            return {
                "status": "error", "reason": "clear_failed",
                "channel": channel, "message": str(e),
            }
        if update.conflict:
            return _stale_channel_refusal(
                agent, channel, invoked_by=invoked_by, delivered=delivered,
                current=update.current_version,
            )
        removed, remaining, large_ref_ids, goal_removed, removed_events = (
            update.value if isinstance(update.value, tuple) and len(update.value) == 5
            else (0, 0, (), False, [])
        )
        if large_ref_ids:
            try:
                ack_large_result_refs(agent, set(large_ref_ids))
            except Exception:
                pass
            _safe_log(
                agent, "large_result_reminder_dismissed", channel=channel,
                invoked_by=invoked_by, forced=bool(force),
                acked_ref_ids=list(large_ref_ids),
                event_id=event_id, ref_id=ref_id,
            )

        if removed == 0:
            try:
                agent._log(
                    "notification_event_dismiss",
                    channel=channel,
                    invoked_by=invoked_by,
                    event_id=event_id,
                    ref_id=ref_id,
                    removed=0,
                    forced=bool(force),
                    reason=ack_reason or None,
                )
                if invoked_by == "system":
                    agent._log(
                        "system_event_dismiss",
                        event_id=event_id,
                        ref_id=ref_id,
                        removed=0,
                        forced=bool(force),
                        reason=ack_reason or None,
                    )
            except Exception:
                pass
            result = {
                "status": "ok",
                "channel": channel,
                "cleared": False,
                "cause": DISMISS_CAUSE_NO_MATCHING_EVENT,
                "removed": 0,
                "remaining": remaining,
                "forced": bool(force),
            }
            if event_id:
                result["event_id"] = event_id
            if ref_id:
                result["ref_id"] = ref_id
            if ack_reason:
                result["reason"] = ack_reason
            return result

        if goal_removed:
            try:
                import time as _time
                agent._goal_reminder_last_dismissed_at = _time.time()
            except Exception:
                pass

        resolved_worker_refs = _resolve_worker_hang_refs(
            agent, removed_events, reason=ack_reason
        )

        try:
            agent._log(
                "notification_event_dismiss",
                channel=channel,
                invoked_by=invoked_by,
                event_id=event_id,
                ref_id=ref_id,
                removed=removed,
                forced=bool(force),
                reason=ack_reason or None,
            )
            if invoked_by == "system":
                agent._log(
                    "system_event_dismiss",
                    event_id=event_id,
                    ref_id=ref_id,
                    removed=removed,
                    forced=bool(force),
                    reason=ack_reason or None,
                )
        except Exception:
            pass

        result = {
            "status": "ok",
            "channel": channel,
            "cleared": bool(removed),
            "removed": removed,
            "remaining": remaining,
            "forced": bool(force),
        }
        if event_id:
            result["event_id"] = event_id
        if ref_id:
            result["ref_id"] = ref_id
        if ack_reason:
            result["reason"] = ack_reason
        if large_ref_ids:
            result["acked_large_result_refs"] = list(large_ref_ids)
            result["note"] = _LARGE_RESULT_DISMISS_NOTE
        if resolved_worker_refs:
            result["resolved_worker_hang_refs"] = resolved_worker_refs
        return result

    # Nudge owns dismissal semantics while Notification remains the transport.
    # Capture the current finding identities before the channel is cleared so
    # unresolved findings can reappear only after the shared repeat interval.
    if channel == "nudge" and not event_id and not ref_id:
        try:
            from .nudge import record_dismissal
            record_dismissal(agent)
        except Exception:
            pass

    # Store compare-update owns system/daemon aggregate serialization.
    if channel in {"system", DAEMON_CHANNEL}:
        result = _dismiss_system_event()
    else:
        result = _clear_current_channel()

    if _dismiss_changed_surface(result):
        _signal_notification_dismissed(agent, channel)
    return result


def _dismiss_changed_surface(result: dict) -> bool:
    """Return True iff a dismiss result reflects a real change to the surface."""
    if not isinstance(result, dict) or result.get("status") != "ok":
        return False
    return bool(
        result.get("cleared")
        or result.get("removed")
        or result.get("acked_large_result_refs")
    )


def _signal_notification_dismissed(agent, channel: str) -> None:
    """Signal a notification-surface dismiss to the chat session's adapter."""
    chat = getattr(agent, "_chat", None)
    if chat is None:
        return
    hook = getattr(chat, "on_notification_dismissed", None)
    if not callable(hook):
        return
    try:
        hook(channel)
    except Exception:  # pragma: no cover - defensive hook isolation
        try:
            agent._log(
                "notification_dismiss_hook_failed",
                channel=channel,
            )
        except Exception:
            pass
