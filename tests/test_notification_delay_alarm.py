"""Focused contract tests for consumer-side notification delay and its alarm."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from lingtai.kernel.notifications import (
    DELAY_ALARM_CHANNEL,
    coherent_attention_read,
    delay_notification_channel,
    is_channel_allowed,
    notification_delay_max_seconds,
    reconcile_notification_delay,
)
from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.meta_block import _collect_active_notifications
from lingtai.tools.notification import get_schema
from tests._tool_plugin_helpers import dispatch_declared_tool
from lingtai.tools.notification import DECLARATION as NOTIFICATION_DECLARATION
from tests._notification_store_helpers import notification_store_for, publish_test_payload, snapshot_notifications


@dataclass
class _DelayAgent:
    _working_dir: Path
    _logs: list[tuple[str, dict]] = field(default_factory=list)
    _notification_store: object = field(init=False)

    def __post_init__(self) -> None:
        self._notification_store = notification_store_for(self._working_dir)

    def _log(self, event: str, **fields) -> None:
        self._logs.append((event, fields))


def _call(agent: _DelayAgent, channel: str, seconds: int) -> dict:
    return dispatch_declared_tool(NOTIFICATION_DECLARATION,
        agent,
        {
            "action": "delay",
            "input": {"channel": channel, "seconds": seconds},
            "reasoning": "focused delay test",
        },
    )


def _observed(agent: _DelayAgent):
    return coherent_attention_read(
        agent._notification_store,
        lambda channel: is_channel_allowed(channel, workdir=str(agent._working_dir)),
        str(agent._working_dir),
    )


def _expire_state(agent: _DelayAgent) -> None:
    state_path = agent._working_dir / ".notification" / ".delay_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["deadline_epoch"] = state["started_epoch"] - 1
    state_path.write_text(json.dumps(state), encoding="utf-8")


def _expired_delay(tmp_path: Path, channel="email", payload=None) -> _DelayAgent:
    agent = _DelayAgent(tmp_path)
    if payload is not None:
        publish_test_payload(tmp_path, channel, payload)
    assert _call(agent, channel, 30)["status"] == "ok"
    _expire_state(agent)
    return agent


def test_delay_schema_and_allowlist_expose_alarm_but_forbid_target(tmp_path: Path, monkeypatch) -> None:
    agent = _DelayAgent(tmp_path)
    monkeypatch.delenv("LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS", raising=False)
    assert is_channel_allowed(DELAY_ALARM_CHANNEL)
    assert notification_delay_max_seconds(agent) == 600
    schema = get_schema()
    assert schema["properties"]["action"]["enum"][-3:] == [
        "delay",
        "settings",
        "manual",
    ]
    delay_branch = next(
        branch
        for branch in schema["properties"]["input"]["anyOf"]
        if branch["title"] == "delay input"
    )
    seconds_schema = delay_branch["properties"]["seconds"]
    assert seconds_schema["type"] == "integer"
    assert seconds_schema["minimum"] == 0
    assert "maximum" not in seconds_schema  # the cap is live, not schema-static

    bad_alarm = _call(agent, DELAY_ALARM_CHANNEL, 3)
    assert bad_alarm["status"] == "error"
    assert bad_alarm["reason"] == "invalid_delay"
    assert "cannot be delayed" in bad_alarm["message"]

    assert _call(agent, "email", 600)["status"] == "ok"
    too_long = _call(agent, "soul", 601)
    assert too_long["status"] == "error"
    assert too_long["reason"] == "invalid_delay"


def test_delay_cap_is_live_at_each_action_invocation(tmp_path: Path, monkeypatch) -> None:
    agent = _DelayAgent(tmp_path)
    monkeypatch.setenv("LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS", "2")
    assert _call(agent, "email", 3)["reason"] == "invalid_delay"

    monkeypatch.setenv("LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS", "4")
    allowed = _call(agent, "email", 4)
    assert allowed["status"] == "ok"
    assert allowed["seconds"] == 4


@pytest.mark.parametrize("raw", ["", "bad", "0", "-1"])
def test_invalid_delay_cap_falls_back_to_600_and_logs(tmp_path: Path, monkeypatch, raw: str) -> None:
    agent = _DelayAgent(tmp_path)
    monkeypatch.setenv("LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS", raw)

    assert notification_delay_max_seconds(agent) == 600
    refused = _call(agent, "email", 601)
    assert refused["reason"] == "invalid_delay"
    fallback_logs = [event for event in agent._logs if event[0] == "notification_delay_max_seconds_invalid"]
    assert fallback_logs
    assert fallback_logs[-1][1]["fallback_seconds"] == 600


def test_no_state_and_future_delay_heartbeat_take_zero_native_locks(tmp_path: Path, monkeypatch) -> None:
    import lingtai.kernel.notifications as notifications

    agent = _DelayAgent(tmp_path)
    calls = []

    def no_native_lock(_workdir, _store):
        calls.append(_workdir)
        raise AssertionError("heartbeat should not acquire native delay locks")

    monkeypatch.setattr(notifications, "_delay_transaction", no_native_lock)
    assert reconcile_notification_delay(tmp_path, agent._notification_store) is False
    assert calls == []

    monkeypatch.undo()
    assert _call(agent, "email", 30)["status"] == "ok"
    monkeypatch.setattr(notifications, "_delay_transaction", no_native_lock)
    assert reconcile_notification_delay(tmp_path, agent._notification_store) is False
    assert calls == []


def test_delay_uses_the_lock_port_composed_with_the_store(tmp_path: Path) -> None:
    acquired: list[str] = []

    class RecordingMutationLock:
        @contextmanager
        def exclusive(self, _notification_dir: Path, scope: str):
            acquired.append(scope)
            yield

    agent = _DelayAgent(tmp_path)
    agent._notification_store = PosixNotificationStoreAdapter(
        tmp_path, mutation_lock=RecordingMutationLock()
    )

    assert _call(agent, "email", 30)["status"] == "ok"
    assert acquired == ["channel:delay-alarm", "resource:delay-state"]


def test_delay_hides_only_target_from_coherent_and_voluntary_check_reads(tmp_path: Path) -> None:
    agent = _DelayAgent(tmp_path)
    publish_test_payload(tmp_path, "email", {"data": {"count": 2}})
    publish_test_payload(tmp_path, "soul", {"data": {"voices": ["remain"]}})
    original_email = (tmp_path / ".notification" / "email.json").read_bytes()

    delayed = _call(agent, "email", 30)
    assert delayed["status"] == "ok"
    assert delayed["action"] == "delayed"
    assert (tmp_path / ".notification" / ".delay_state.json").is_file()
    assert (tmp_path / ".notification" / "email.json").read_bytes() == original_email

    observed = _observed(agent)
    assert "email" not in observed.payloads
    assert observed.payloads["soul"]["data"]["voices"] == ["remain"]

    # The voluntary check collection uses the same coherent read/filter.
    payload, versions = _collect_active_notifications(agent)
    assert versions is not None
    assert "email" not in payload["notifications"]
    assert "soul" in payload["notifications"]


def test_zero_cancels_matching_delay_and_reexposes_target(tmp_path: Path) -> None:
    agent = _DelayAgent(tmp_path)
    publish_test_payload(tmp_path, "email", {"data": {"count": 1}})
    assert _call(agent, "email", 30)["status"] == "ok"
    assert "email" not in _observed(agent).payloads

    cancelled = _call(agent, "email", 0)
    assert cancelled == {
        "status": "ok", "action": "cancelled", "channel": "email", "cancelled": True
    }
    assert "email" in _observed(agent).payloads
    assert DELAY_ALARM_CHANNEL not in snapshot_notifications(tmp_path)


def test_nonzero_replaces_the_one_live_delay(tmp_path: Path) -> None:
    agent = _DelayAgent(tmp_path)
    publish_test_payload(tmp_path, "email", {"data": {"count": 1}})
    publish_test_payload(tmp_path, "soul", {"data": {"voices": ["x"]}})
    assert _call(agent, "email", 30)["status"] == "ok"

    replacement = _call(agent, "soul", 30)
    assert replacement["status"] == "ok"
    assert replacement["replaced_channel"] == "email"
    payloads = _observed(agent).payloads
    assert "email" in payloads
    assert "soul" not in payloads


def test_expiry_reexposes_unchanged_target_and_writes_one_conservative_alarm(tmp_path: Path) -> None:
    agent = _DelayAgent(tmp_path)
    target = {"data": {"count": 7, "events": [{"event_id": "one"}]}}
    publish_test_payload(tmp_path, "email", target)
    original = (tmp_path / ".notification" / "email.json").read_bytes()
    assert _call(agent, "email", 30)["status"] == "ok"
    _expire_state(agent)

    assert reconcile_notification_delay(tmp_path, agent._notification_store) is True
    first_alarm = (tmp_path / ".notification" / "delay-alarm.json").read_bytes()
    assert reconcile_notification_delay(tmp_path, agent._notification_store) is False
    assert (tmp_path / ".notification" / "delay-alarm.json").read_bytes() == first_alarm
    assert (tmp_path / ".notification" / "email.json").read_bytes() == original

    payloads = _observed(agent).payloads
    assert "email" in payloads
    alarm = payloads[DELAY_ALARM_CHANNEL]
    assert alarm["priority"] == "high"
    data = alarm["data"]["delay_alarm"]
    assert data["target"] == "email"
    assert data["requested_seconds"] == 30
    assert data["actual_seconds"] >= 0
    assert data["changed"] is False
    assert data["current"]["producer_reported_count"] == 7
    assert data["current"]["retained_event_count"] == 1
    assert "not asserted total" in data["current"]["retained_event_count_scope"]


def test_expiry_does_not_reuse_prelock_stats_after_delay_identity_replacement(tmp_path: Path, monkeypatch) -> None:
    import lingtai.kernel.notifications as notifications

    agent = _expired_delay(tmp_path, payload={"data": {"count": 7}})

    @contextmanager
    def replace_delay_identity(workdir, _store):
        state_path = Path(workdir) / ".notification" / ".delay_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["target"] = "soul"
        state["request_id"] = "replacement-request"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        yield

    monkeypatch.setattr(notifications, "_delay_transaction", replace_delay_identity)
    assert reconcile_notification_delay(tmp_path, agent._notification_store) is True

    alarm = snapshot_notifications(tmp_path)[DELAY_ALARM_CHANNEL]["data"]["delay_alarm"]
    assert alarm["target"] == "soul"
    assert alarm["current"] == {"present": None}


def test_expiry_marks_changed_without_claiming_capped_event_total(tmp_path: Path) -> None:
    agent = _expired_delay(
        tmp_path, "daemon", {"data": {"daemon_id": "delay-test", "events": [{"event_id": "old"}]}}
    )
    publish_test_payload(
        tmp_path,
        "daemon",
        {
            "data": {
                "daemon_id": "delay-test",
                "events": [{"event_id": "new-a"}, {"event_id": "new-b"}],
            }
        },
    )
    assert reconcile_notification_delay(tmp_path, agent._notification_store) is True
    alarm = snapshot_notifications(tmp_path)[DELAY_ALARM_CHANNEL]["data"]["delay_alarm"]
    assert alarm["changed"] is True
    assert alarm["current"]["retained_event_count"] == 2
    assert "producer_reported_count" not in alarm["current"]
    assert "exact total" in alarm["statistics_scope"]


def test_delay_core_rejects_mismatched_early_cancel(tmp_path: Path) -> None:
    agent = _DelayAgent(tmp_path)
    assert delay_notification_channel(agent, "email", 30)["status"] == "ok"
    mismatch = delay_notification_channel(agent, "soul", 0)
    assert mismatch["reason"] == "delay_target_mismatch"
    assert "email" not in _observed(agent).payloads  # delay remains active


def test_replacement_recovers_an_overdue_alarm_before_overwriting_state(tmp_path: Path) -> None:
    agent = _expired_delay(tmp_path, payload={"data": {"count": 1}})

    replacement = _call(agent, "soul", 30)
    assert replacement["status"] == "ok"
    assert snapshot_notifications(tmp_path)[DELAY_ALARM_CHANNEL]["data"]["delay_alarm"]["target"] == "email"
    # The new request is now the only live delay; it hides soul, not email.
    payloads = _observed(agent).payloads
    assert "email" in payloads
    assert "soul" not in payloads


def test_process_timer_prompts_expiry_recovery(monkeypatch, tmp_path: Path) -> None:
    import lingtai.kernel.notifications as notifications

    fired = []

    class _Timer:
        daemon = False

        def __init__(self, _delay, callback, args=()):
            self.callback = callback
            self.args = args
            self.cancelled = False
            fired.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    monkeypatch.setattr(notifications.threading, "Timer", _Timer)
    agent = _expired_delay(tmp_path, payload={"data": {"count": 1}})
    assert len(fired) == 1

    fired[0].callback(*fired[0].args)
    alarm = snapshot_notifications(tmp_path)[DELAY_ALARM_CHANNEL]
    assert alarm["priority"] == "high"
    assert "email" in _observed(agent).payloads


def test_concurrent_expiry_recovery_claims_one_alarm_publication(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    agent = _expired_delay(tmp_path)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: reconcile_notification_delay(tmp_path, agent._notification_store), range(6)))

    assert results.count(True) == 1
    assert results.count(False) == 5
    state = json.loads((tmp_path / ".notification" / ".delay_state.json").read_text())
    assert state["status"] == "published"
    assert snapshot_notifications(tmp_path)[DELAY_ALARM_CHANNEL]["priority"] == "high"
