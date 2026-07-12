"""Shared explicitly Store-backed helpers for notification-cluster tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lingtai.kernel.notification_store import NotificationStorePort
from tests._notification_store_helpers import (
    fingerprint_notifications,
    notification_store_for,
    publish_test_payload,
)


@dataclass
class StubAgent:
    """Minimal agent stub with an explicitly composed production Store."""

    _working_dir: Path
    _logs: list[tuple[str, dict]] = field(default_factory=list)
    _notification_fp: tuple = ()
    _notification_store: NotificationStorePort = field(init=False)

    def __post_init__(self) -> None:
        self._notification_store = notification_store_for(self._working_dir)

    def _log(self, event_type: str, **fields: Any) -> None:
        self._logs.append((event_type, fields))


def events(agent: StubAgent, name: str) -> list[dict]:
    """Return the field dicts of every ``_log`` call of type *name*."""
    return [fields for event, fields in agent._logs if event == name]


def mark_delivered(agent: StubAgent) -> None:
    """Stamp the agent's notification fingerprint as current (delivered)."""
    agent._notification_fp = fingerprint_notifications(agent)


def publish_large_result_reminder(
    tmp_path: Path,
    *,
    tool_call_id: str = "toolu_big",
    extra_events: list[dict] | None = None,
) -> None:
    """Publish a ``system.json`` containing one large-result reminder."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    events_payload = [
        {
            "event_id": "evt_lr",
            "source": "large_tool_result",
            "ref_id": f"large_tool_result:{tool_call_id}",
            "body": "summarize me",
        }
    ]
    if extra_events:
        events_payload = list(extra_events) + events_payload
    publish_test_payload(
        tmp_path,
        "system",
        {
            "header": f"{len(events_payload)} system notifications",
            "icon": "🔔",
            "priority": "normal",
            "published_at": "2026-06-20T00:00:00Z",
            "data": {"events": events_payload},
        },
    )
