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

import re
from datetime import datetime, timezone

_CHANNEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# Notification channels are intentionally allowlisted.  Unknown files in
# `.notification/` are ignored by readers and cannot be published/cleared
# through kernel helpers.  MCP bridge channels are allowlisted as a family
# because server names are dynamic but still owned by the MCP inbox contract.
_NOTIFICATION_CHANNEL_ALLOWLIST: set[str] = {
    "bash",
    "btw",
    "cron",
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
    "Summarization via system(action='summarize') remains the preferred way to "
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


def _build_allow_predicate() -> callable:
    """Return a closure that answers ``is_channel_allowed`` for the store."""

    def _allow(channel: str) -> bool:
        try:
            validate_channel_name(channel)
        except ValueError:
            return False
        if channel in _NOTIFICATION_CHANNEL_ALLOWLIST:
            return True
        return any(
            channel.startswith(prefix)
            for prefix in _NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST
        )

    return _allow


# Cached allow predicate — rebuilt if the allowlist changes at runtime.
_allow_predicate: callable | None = None


def _get_allow_predicate() -> callable:
    global _allow_predicate
    if _allow_predicate is None:
        _allow_predicate = _build_allow_predicate()
    return _allow_predicate


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


def is_channel_allowed(channel: str) -> bool:
    """Return whether ``channel`` is on the notification allowlist."""
    try:
        validate_channel_name(channel)
    except ValueError:
        return False
    if channel in _NOTIFICATION_CHANNEL_ALLOWLIST:
        return True
    return any(channel.startswith(prefix) for prefix in _NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST)


def validate_allowed_channel(channel: str) -> None:
    """Validate syntax and allowlist membership for a notification channel."""
    validate_channel_name(channel)
    if not is_channel_allowed(channel):
        allowed = sorted(_NOTIFICATION_CHANNEL_ALLOWLIST)
        prefixes = list(_NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST)
        raise ValueError(
            "notification channel is not allowlisted: "
            f"{channel!r}; allowed={allowed}; allowed_prefixes={prefixes}"
        )


def register_notification_channel(channel: str) -> None:
    """Allow an in-process producer to register an exact notification channel."""
    global _allow_predicate
    validate_channel_name(channel)
    _NOTIFICATION_CHANNEL_ALLOWLIST.add(channel)
    _allow_predicate = None  # invalidate cache


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
    validate_allowed_channel(tool_name)

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
    validate_allowed_channel(tool_name)
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
    validate_allowed_channel(channel)
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
        validate_allowed_channel(channel)
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

    if (event_id or ref_id) and channel != "system":
        return {
            "status": "error",
            "reason": "atomic_dismiss_requires_system_channel",
            "channel": channel,
            "event_id": event_id,
            "ref_id": ref_id,
            "message": "event_id/ref_id dismiss is only supported for channel='system'.",
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
    delivered = _channel_fingerprint_entry(
        getattr(agent, "_notification_fp", ()), channel
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
            return None, True, (large_ref_ids, goal_removed)

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

        large_ref_ids, goal_reminder_cleared_by_whole_system_dismiss = (
            update.value if isinstance(update.value, tuple) and len(update.value) == 2
            else ((), False)
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
        existed = update.cleared
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
        if ack_reason:
            result["reason"] = ack_reason
        if large_ref_ids:
            result["acked_large_result_refs"] = list(large_ref_ids)
            result["note"] = _LARGE_RESULT_DISMISS_NOTE
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
            value = (len(removed_events), len(kept), large_ref_ids, goal_removed)
            if not removed_events:
                return current_payload, False, value
            return _system_payload_with_events(
                current_payload, kept, published_at
            ), True, value

        try:
            update = store.compare_update_channel("system", expected, _mutator)
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
        removed, remaining, large_ref_ids, goal_removed = (
            update.value if isinstance(update.value, tuple) and len(update.value) == 4
            else (0, 0, (), False)
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
        return result

    # Store compare-update owns system-channel serialization.
    if channel == "system":
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
