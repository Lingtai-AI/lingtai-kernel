"""Goal reminder nudge.

A protected ``.notification/goal.json`` file is the goal source of truth.
When an active goal remains while the agent has been IDLE for the configured
reminder delay, this check publishes one short ``goal.reminder`` event into
``.notification/system.json``.  The reminder only says to inspect the goal;
the actual objective and instructions stay in ``goal.json``.
"""
from __future__ import annotations

import time
from typing import Any

from ..notifications import _get_allow_predicate
from ..notification_store import UNCONDITIONAL
from ..state import AgentState


_DEFAULT_DELAY_SECONDS = 120.0
_REMINDER_BODY = "Goal reminder: read .notification/goal.json and follow its instructions; see the goal manual under system-manual."


def check(agent) -> None:
    """Publish a short system reminder when an active goal survives IDLE."""
    # Cheap gate before touching disk: goal reminders are IDLE-only.
    if getattr(agent, "_state", None) != AgentState.IDLE:
        return

    store = agent._notification_store
    allow = _get_allow_predicate()
    notifications = store.snapshot(allow)
    goal = notifications.get("goal")
    if not isinstance(goal, dict) or not _is_active(goal):
        _clear_goal_reminders(agent, keep_ref_id=None)
        try:
            agent._goal_reminder_last_goal_ref = None
            agent._goal_reminder_last_published_ref = None
        except Exception:
            pass
        return

    now = time.time()
    idle_since = float(getattr(agent, "_state_changed_at", now) or now)
    delay = _reminder_delay(agent, goal)
    if now - idle_since < delay:
        return

    goal_id = _goal_id(goal)
    ref_id = f"goal:{goal_id}"
    _clear_goal_reminders(agent, keep_ref_id=ref_id)

    last_dismissed = float(getattr(agent, "_goal_reminder_last_dismissed_at", 0.0) or 0.0)
    if last_dismissed and now - last_dismissed < delay:
        return

    event_id = agent._enqueue_system_notification(
        source="goal.reminder",
        ref_id=ref_id,
        body=_REMINDER_BODY,
        skip_if_ref_id_exists=True,
    )
    if not event_id:
        return
    agent._goal_reminder_last_goal_ref = ref_id
    agent._goal_reminder_last_published_ref = ref_id
    try:
        agent._log(
            "goal_reminder_published",
            ref_id=ref_id,
            delay_seconds=delay,
            idle_seconds=round(now - idle_since, 3),
        )
    except Exception:
        pass


def _is_active(goal: dict[str, Any]) -> bool:
    data = goal.get("data") if isinstance(goal.get("data"), dict) else {}
    status = goal.get("status", data.get("status", "active"))
    return str(status or "active").lower() not in {"done", "complete", "completed", "superseded", "cancelled", "canceled", "inactive"}


def _goal_id(goal: dict[str, Any]) -> str:
    data = goal.get("data") if isinstance(goal.get("data"), dict) else {}
    raw = goal.get("id") or data.get("id") or goal.get("title") or data.get("title") or "current"
    text = str(raw).strip() or "current"
    safe = "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in text)
    return safe[:80] or "current"


def _reminder_delay(agent, goal: dict[str, Any]) -> float:
    data = goal.get("data") if isinstance(goal.get("data"), dict) else {}
    raw = goal.get("reminder_delay_seconds", data.get("reminder_delay_seconds"))
    if raw is None:
        raw = getattr(agent, "_soul_delay", _DEFAULT_DELAY_SECONDS)
    try:
        delay = float(raw)
    except (TypeError, ValueError):
        delay = _DEFAULT_DELAY_SECONDS
    if delay <= 0:
        delay = _DEFAULT_DELAY_SECONDS
    return max(1.0, delay)


def _system_events(system: Any) -> list:
    if not isinstance(system, dict):
        return []
    data = system.get("data")
    events = data.get("events", []) if isinstance(data, dict) else []
    return events if isinstance(events, list) else []


def _is_goal_reminder_event(event: object) -> bool:
    return isinstance(event, dict) and event.get("source") == "goal.reminder"


def _clear_goal_reminders(agent, *, keep_ref_id: str | None) -> None:
    """Remove stale goal.reminder system events.

    Uses the store's serialized compare_update_channel — no external lock needed.
    """
    store = agent._notification_store
    published_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _mutator(current_payload: dict) -> tuple[dict | None, bool, int]:
        system = current_payload if isinstance(current_payload, dict) else {}
        events = _system_events(system)
        if not events:
            return current_payload, False, 0

        def _keep(event: object) -> bool:
            if not _is_goal_reminder_event(event):
                return True
            if keep_ref_id is not None and isinstance(event, dict) and event.get("ref_id") == keep_ref_id:
                return True
            return False

        kept = [event for event in events if _keep(event)]
        removed_count = len(events) - len(kept)
        if not removed_count:
            return current_payload, False, 0

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
            return new_payload, True, removed_count
        else:
            return None, True, removed_count  # clear system channel

    try:
        result = store.compare_update_channel("system", UNCONDITIONAL, _mutator)
        removed_count = result.value if isinstance(result.value, int) else 0
        if result.applied and result.changed and removed_count:
            try:
                agent._log(
                    "goal_reminder_cleared",
                    keep_ref_id=keep_ref_id,
                    removed=removed_count,
                )
            except Exception:
                pass
    except Exception as e:
        try:
            agent._log("goal_reminder_clear_error", error=str(e)[:200])
        except Exception:
            pass
