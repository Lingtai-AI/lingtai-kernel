"""Tests for protected goal notifications and idle reminders."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingtai.tools import notification as notif_intrinsic
from tests._notification_store_helpers import (
    fingerprint_notifications,
    notification_store_for,
    publish_test_payload,
    snapshot_notifications,
)
from lingtai.kernel.nudge.goal import check as check_goal
from lingtai.kernel.state import AgentState


@dataclass
class _GoalAgent:
    _working_dir: Path
    _state: AgentState = AgentState.IDLE
    _state_changed_at: float = field(default_factory=lambda: time.time() - 10)
    _soul_delay: float = 1.0
    _logs: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _notification_store: object = field(init=False)

    def __post_init__(self) -> None:
        self._notification_store = notification_store_for(self._working_dir)

    def _log(self, event_type: str, **fields: Any) -> None:
        self._logs.append((event_type, fields))

    def _wake_nap(self, reason: str) -> None:
        self._wake_reason = reason

    def _enqueue_system_notification(
        self,
        *,
        source: str,
        ref_id: str,
        body: str,
        skip_if_ref_id_exists: bool = False,
    ) -> str:
        from lingtai.kernel.base_agent.messaging import _enqueue_system_notification

        return _enqueue_system_notification(
            self,
            source=source,
            ref_id=ref_id,
            body=body,
            skip_if_ref_id_exists=skip_if_ref_id_exists,
        )


def test_goal_reminder_publishes_short_system_event_after_idle_delay(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)
    publish_test_payload(
        tmp_path,
        "goal",
        {
            "instructions": "Current active goal. Details live here; see the goal manual under system-manual.",
            "data": {"id": "demo", "status": "active", "reminder_delay_seconds": 1},
        },
    )

    check_goal(agent)

    system = snapshot_notifications(tmp_path)["system"]
    events = system["data"]["events"]
    assert len(events) == 1
    assert events[0]["source"] == "goal.reminder"
    assert events[0]["ref_id"] == "goal:demo"
    assert events[0]["body"] == (
        "Goal reminder: read .notification/goal.json and follow its instructions; "
        "see the goal manual under system-manual."
    )


def test_goal_reminder_does_not_duplicate_existing_event(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)
    publish_test_payload(tmp_path, "goal", {"data": {"id": "demo", "status": "active", "reminder_delay_seconds": 1}})
    check_goal(agent)
    check_goal(agent)
    assert len(snapshot_notifications(tmp_path)["system"]["data"]["events"]) == 1


def test_goal_reminder_skips_completed_goal(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)
    publish_test_payload(tmp_path, "goal", {"data": {"id": "demo", "status": "done", "reminder_delay_seconds": 1}})
    check_goal(agent)
    assert "system" not in snapshot_notifications(tmp_path)


def test_goal_reminder_requires_goal_json(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)

    check_goal(agent)

    assert "system" not in snapshot_notifications(tmp_path)
    assert not (tmp_path / ".notification" / "system.json").exists()


def test_goal_reminder_republishes_after_whole_system_dismiss_and_fresh_delay(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)
    publish_test_payload(tmp_path, "goal", {"data": {"id": "demo", "status": "active", "reminder_delay_seconds": 1}})
    check_goal(agent)
    assert "system" in snapshot_notifications(tmp_path)
    agent._notification_fp = fingerprint_notifications(tmp_path)

    result = notif_intrinsic.handle(agent, {"action": "dismiss_channel", "channel": "system"})

    assert result["status"] == "ok"
    assert "system" not in snapshot_notifications(tmp_path)
    assert getattr(agent, "_goal_reminder_last_dismissed_at", 0) > 0
    check_goal(agent)
    assert "system" not in snapshot_notifications(tmp_path)

    agent._goal_reminder_last_dismissed_at = time.time() - 2
    check_goal(agent)
    assert snapshot_notifications(tmp_path)["system"]["data"]["events"][0]["ref_id"] == "goal:demo"


def test_goal_reminder_clears_when_goal_becomes_done(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)
    publish_test_payload(tmp_path, "goal", {"data": {"id": "demo", "status": "active", "reminder_delay_seconds": 1}})
    check_goal(agent)
    assert "system" in snapshot_notifications(tmp_path)

    publish_test_payload(tmp_path, "goal", {"data": {"id": "demo", "status": "done", "reminder_delay_seconds": 1}})
    check_goal(agent)

    assert "system" not in snapshot_notifications(tmp_path)


def test_goal_reminder_clears_when_goal_json_is_deleted(tmp_path: Path) -> None:
    agent = _GoalAgent(tmp_path)
    publish_test_payload(tmp_path, "goal", {"data": {"id": "demo", "status": "active", "reminder_delay_seconds": 1}})
    check_goal(agent)
    assert "system" in snapshot_notifications(tmp_path)

    assert agent._notification_store.clear("goal") is True
    check_goal(agent)

    assert "system" not in snapshot_notifications(tmp_path)
