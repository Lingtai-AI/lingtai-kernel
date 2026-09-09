"""Focused contract tests for the persisted ``system.sleep(delay=...)`` alarm."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import sys
import threading
from unittest.mock import Mock, patch

import pytest

from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.base_agent import lifecycle as lifecycle_mod
from lingtai.kernel.state import AgentState
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS
from lingtai.tools.system.schema import ACTION_ENUM_DESCRIPTION, INPUT_SCHEMAS
from tests._agent_presence_helpers import make_test_presence_store
from tests._lifecycle_clock_helpers import FakeLifecycleClock
from tests._notification_store_helpers import FakeNotificationStore
from tests._service_helpers import make_tool_result_mock_service
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._workdir_lease_helpers import make_test_lease


def _make_agent(tmp_path, *, clock=None, store=None) -> BaseAgent:
    workdir = tmp_path / "alarm_agent"
    return BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_tool_result_mock_service(),
        agent_name="alarm-agent",
        working_dir=workdir,
        workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(),
        agent_presence=make_test_presence_store(),
        lifecycle_clock=clock or FakeLifecycleClock(wall=1_000.0, monotonic=0.0),
        source_revision_port=make_test_source_revision_port(),
        notification_store=store or FakeNotificationStore(),
    )


def _sleep(agent, **input_values) -> dict:
    return agent._intrinsics["system"]({"action": "sleep", "input": input_values})


def _arm(agent, delay: object) -> str:
    with lifecycle_mod._sleep_alarm_lock(agent):
        return lifecycle_mod._arm_sleep_alarm(
            agent, lifecycle_mod._sleep_alarm_delay_decimal(delay)
        )


def _events(store: FakeNotificationStore) -> list[dict]:
    return store.snapshot(lambda _channel: True).get("system", {}).get("data", {}).get("events", [])


def test_sleep_delay_schema_is_required_nullable_positive_and_has_no_maximum() -> None:
    schema = INPUT_SCHEMAS["sleep"]
    delay = schema["properties"]["delay"]
    assert "delay" in schema["required"]
    assert delay["type"] == ["number", "null"]
    assert delay["exclusiveMinimum"] == 0
    assert "maximum" not in delay
    assert "last-resort" in delay["description"]
    assert "normal waiting is IDLE" in ACTION_ENUM_DESCRIPTION
    assert "no reliable completion notification" in ACTION_ENUM_DESCRIPTION


@pytest.mark.parametrize("delay", [True, False, 0, -1, float("nan"), float("inf"), -float("inf"), "1"])
def test_sleep_rejects_nonfinite_nonpositive_and_non_number_delay(tmp_path, delay) -> None:
    agent = _make_agent(tmp_path)
    result = _sleep(agent, reason=None, force=None, delay=delay)
    assert result == {
        "status": "error",
        "message": "delay must be a finite positive number of seconds",
    }
    assert not agent._asleep.is_set()
    assert not (agent._working_dir / ".alarm").exists()


def test_null_or_omitted_delay_preserves_sleep_and_existing_alarm(tmp_path) -> None:
    agent = _make_agent(tmp_path)
    alarm = agent._working_dir / ".alarm"
    alarm.parent.mkdir(parents=True, exist_ok=True)
    alarm.write_text("12345", encoding="utf-8")

    # The handler also remains compatible with pre-delay direct callers that
    # omit delay altogether rather than sending the strict-schema null.
    result = _sleep(agent, reason="ordinary sleep", force=None)
    assert result["status"] == "ok"
    assert agent._asleep.is_set()
    assert alarm.read_text(encoding="utf-8") == "12345"

    agent._asleep.clear()
    result = _sleep(agent, reason=None, force=None, delay=None)
    assert result["status"] == "ok"
    assert alarm.read_text(encoding="utf-8") == "12345"


def test_successful_sleep_persists_and_overwrites_alarm_before_asleep(tmp_path, monkeypatch) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    agent = _make_agent(tmp_path, clock=clock)
    alarm = agent._working_dir / ".alarm"
    original_set_state = agent._set_state

    def assert_alarm_before_asleep(state, *, reason):
        assert state == AgentState.ASLEEP
        assert alarm.is_file()
        return original_set_state(state, reason=reason)

    monkeypatch.setattr(agent, "_set_state", assert_alarm_before_asleep)
    assert _sleep(agent, reason=None, force=None, delay=5)["status"] == "ok"
    assert Decimal(alarm.read_text(encoding="utf-8")) == Decimal("1005.0")

    clock.set_wall(1_001.0)
    assert _sleep(agent, reason=None, force=None, delay=9)["status"] == "ok"
    assert Decimal(alarm.read_text(encoding="utf-8")) == Decimal("1010.0")


def test_alarm_arm_failure_leaves_agent_awake_and_existing_alarm_intact(tmp_path) -> None:
    agent = _make_agent(tmp_path)
    alarm = agent._working_dir / ".alarm"
    alarm.write_text("2000", encoding="utf-8")

    with patch(
        "lingtai.kernel._fsutil.atomic_write_text", side_effect=OSError("disk full")
    ):
        result = _sleep(agent, reason=None, force=None, delay=5)
    assert result == {"status": "error", "message": "Could not arm sleep alarm; staying awake"}
    assert not agent._asleep.is_set()
    assert alarm.read_text(encoding="utf-8") == "2000"


def test_refused_sleep_does_not_arm_or_replace_alarm(tmp_path) -> None:
    store = FakeNotificationStore()
    store.publish("system", {"data": {"events": [{"body": "already pending"}]}})
    agent = _make_agent(tmp_path, store=store)
    alarm = agent._working_dir / ".alarm"
    alarm.parent.mkdir(parents=True, exist_ok=True)
    alarm.write_text("2222", encoding="utf-8")

    result = _sleep(agent, reason=None, force=None, delay=5)
    assert result["status"] == "ok"  # existing sleep-refusal receipt remains stable
    assert not agent._asleep.is_set()
    assert alarm.read_text(encoding="utf-8") == "2222"


def test_early_wake_does_not_cancel_persisted_alarm(tmp_path) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    agent = _make_agent(tmp_path, clock=clock)
    assert _sleep(agent, reason=None, force=None, delay=50)["status"] == "ok"
    persisted = (agent._working_dir / ".alarm").read_text(encoding="utf-8")

    # A real notification sync uses this same state transition; neither it nor
    # an ordinary early wake owns the alarm file.
    agent._set_state(AgentState.IDLE, reason="notification arrival")
    agent._asleep.clear()
    assert (agent._working_dir / ".alarm").read_text(encoding="utf-8") == persisted


def test_alarm_before_deadline_is_noop(tmp_path) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    store = FakeNotificationStore()
    agent = _make_agent(tmp_path, clock=clock, store=store)
    deadline = _arm(agent, 5)

    lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert (agent._working_dir / ".alarm").read_text(encoding="utf-8") == deadline
    assert _events(store) == []


def test_due_alarm_publishes_once_and_existing_sync_wakes_asleep_agent(tmp_path) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    store = FakeNotificationStore()
    agent = _make_agent(tmp_path, clock=clock, store=store)
    _arm(agent, 5)
    agent._set_state(AgentState.ASLEEP, reason="test")
    agent._asleep.set()
    clock.set_wall(1_005.0)

    lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert not (agent._working_dir / ".alarm").exists()
    events = _events(store)
    assert len(events) == 1
    assert events[0]["source"] == "system.sleep_alarm"
    assert events[0]["ref_id"].startswith("sleep_alarm:")

    # The alarm itself never wakes a state machine; the existing system-event
    # sync path sees this ordinary event and performs ASLEEP -> IDLE.
    agent._sync_notifications()
    assert agent._state == AgentState.IDLE


def test_failed_publication_keeps_alarm_for_retry(tmp_path) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    store = FakeNotificationStore()
    agent = _make_agent(tmp_path, clock=clock, store=store)
    _arm(agent, 5)
    clock.set_wall(1_005.0)

    with patch(
        "lingtai.kernel.base_agent.messaging._enqueue_system_notification",
        side_effect=OSError("store unavailable"),
    ):
        lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert (agent._working_dir / ".alarm").exists()
    assert _events(store) == []

    lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert not (agent._working_dir / ".alarm").exists()
    assert len(_events(store)) == 1


def test_crash_window_retry_is_idempotent_after_publish_before_consume(tmp_path) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    store = FakeNotificationStore()
    agent = _make_agent(tmp_path, clock=clock, store=store)
    _arm(agent, 5)
    clock.set_wall(1_005.0)

    with patch.object(Path, "unlink", side_effect=OSError("disk busy")):
        lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert (agent._working_dir / ".alarm").exists()
    assert len(_events(store)) == 1

    lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert not (agent._working_dir / ".alarm").exists()
    assert len(_events(store)) == 1


def test_overwrite_cannot_be_consumed_by_an_expiry_waiting_on_same_lock(tmp_path) -> None:
    clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    store = FakeNotificationStore()
    agent = _make_agent(tmp_path, clock=clock, store=store)
    _arm(agent, 1)
    clock.set_wall(1_001.0)
    started = threading.Event()

    def expire() -> None:
        started.set()
        lifecycle_mod._fire_sleep_alarm_if_due(agent)

    with lifecycle_mod._sleep_alarm_lock(agent):
        worker = threading.Thread(target=expire)
        worker.start()
        assert started.wait(timeout=1.0)
        new_deadline = lifecycle_mod._arm_sleep_alarm(
            agent, lifecycle_mod._sleep_alarm_delay_decimal(50)
        )
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert (agent._working_dir / ".alarm").read_text(encoding="utf-8") == new_deadline
    assert _events(store) == []


def test_existing_alarm_fires_after_restart(tmp_path) -> None:
    first_clock = FakeLifecycleClock(wall=1_000.0, monotonic=0.0)
    first = _make_agent(tmp_path, clock=first_clock)
    persisted = _arm(first, 5)

    restarted_store = FakeNotificationStore()
    restarted = _make_agent(
        tmp_path,
        clock=FakeLifecycleClock(wall=1_005.0, monotonic=0.0),
        store=restarted_store,
    )
    assert (restarted._working_dir / ".alarm").read_text(encoding="utf-8") == persisted
    lifecycle_mod._fire_sleep_alarm_if_due(restarted)
    assert not (restarted._working_dir / ".alarm").exists()
    assert len(_events(restarted_store)) == 1


def test_huge_finite_delay_has_no_runtime_maximum(tmp_path) -> None:
    agent = _make_agent(tmp_path)
    huge = sys.float_info.max
    result = _sleep(agent, reason=None, force=None, delay=huge)
    assert result["status"] == "ok"
    assert Decimal((agent._working_dir / ".alarm").read_text(encoding="utf-8")).is_finite()


def test_malformed_alarm_is_visible_once_per_unchanged_file(tmp_path) -> None:
    agent = _make_agent(tmp_path)
    alarm = agent._working_dir / ".alarm"
    alarm.parent.mkdir(parents=True, exist_ok=True)
    alarm.write_text("not an absolute time", encoding="utf-8")
    log = Mock()
    agent._log = log

    lifecycle_mod._fire_sleep_alarm_if_due(agent)
    lifecycle_mod._fire_sleep_alarm_if_due(agent)
    assert alarm.exists()
    assert log.call_count == 1
    assert log.call_args.args[0] == "sleep_alarm_malformed"
