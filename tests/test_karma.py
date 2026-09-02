"""Tests for karma/nirvana lifecycle control via system intrinsic."""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lingtai.kernel import config
from lingtai.kernel.base_agent import BaseAgent
from lingtai.kernel.state import AgentState
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store


def _make_agent(tmp_path, **kwargs):
    """Create a minimal BaseAgent for testing."""
    svc = MagicMock()
    svc.create_session.return_value = MagicMock()
    kwargs.setdefault("working_dir", str(tmp_path / "test000000ab"))
    agent = BaseAgent(svc, intrinsics=_TEST_INTRINSICS, **kwargs, workdir_lease=make_test_lease(), agent_presence=make_test_presence_store(), snapshot_port=make_test_snapshot_port(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(kwargs["working_dir"]))
    return agent


class TestSignalFiles:
    """Signal file detection in heartbeat loop."""

    def test_interrupt_signal_sets_cancel_event(self, tmp_path):
        agent = _make_agent(tmp_path)
        detected = threading.Event()
        observations = []
        original_request_cancel = agent._request_turn_cancel

        def request_cancel():
            observations.append(not (agent.working_dir / ".interrupt").exists())
            original_request_cancel()
            detected.set()

        agent._request_turn_cancel = request_cancel
        agent.start()
        try:
            (agent.working_dir / ".interrupt").write_text("")
            assert detected.wait(timeout=5), "heartbeat did not consume .interrupt"
            assert observations == [True]
            assert agent._cancel_event.is_set()
            assert not (agent.working_dir / ".interrupt").exists(), "signal file should be deleted"
        finally:
            agent.stop()

    def test_sleep_signal_sets_asleep(self, tmp_path):
        agent = _make_agent(tmp_path)
        detected = threading.Event()
        observations = []
        original_request_cancel = agent._request_turn_cancel

        def request_cancel():
            observations.append((
                not (agent.working_dir / ".sleep").exists(),
                agent.state,
            ))
            original_request_cancel()
            detected.set()

        agent._request_turn_cancel = request_cancel
        agent.start()
        try:
            (agent.working_dir / ".sleep").write_text("")
            assert detected.wait(timeout=5), "heartbeat did not consume .sleep"
            assert agent._asleep.wait(timeout=5), "sleep transition did not publish ASLEEP"
            assert observations == [(True, AgentState.IDLE)]
            assert agent._cancel_event.is_set()
            assert agent.state == AgentState.ASLEEP
            assert not (agent.working_dir / ".sleep").exists(), "signal file should be deleted"
        finally:
            agent.stop()


class TestSystemIntrinsicKarma:
    """Karma actions in system intrinsic."""

    def test_interrupt_requires_karma_admin(self, tmp_path):
        agent = _make_agent(tmp_path, admin={})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "interrupt", "input": {"address": "/some/path"}})
        assert "error" in result

    def test_interrupt_with_karma_admin(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / ".agent.json").write_text('{"agent_id": "t1"}')
        (target_dir / ".agent.heartbeat").write_text(str(time.time()))

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "interrupt", "input": {"address": str(target_dir)}})
        assert result["status"] == "interrupted"
        assert (target_dir / ".interrupt").is_file()

    def test_lull_writes_signal_file(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / ".agent.json").write_text('{"agent_id": "t1"}')
        (target_dir / ".agent.heartbeat").write_text(str(time.time()))

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "lull", "input": {"address": str(target_dir)}})
        assert result["status"] == "asleep"
        assert (target_dir / ".sleep").is_file()

    def test_target_refresh_requires_karma_admin(self, tmp_path):
        agent = _make_agent(tmp_path, admin={})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "target_refresh", "input": {"address": "/some/path"}})
        assert "error" in result

    def test_target_refresh_writes_refresh_signal_file(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / ".agent.json").write_text('{"agent_id": "t1"}')
        (target_dir / ".agent.heartbeat").write_text(str(time.time()))

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "target_refresh", "input": {"address": str(target_dir)}})
        assert result["status"] == "refresh_requested"
        assert (target_dir / ".refresh").is_file()

    def test_target_refresh_rejects_not_running_target(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / ".agent.json").write_text('{"agent_id": "t1", "admin": {}}')

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "target_refresh", "input": {"address": str(target_dir)}})
        assert "error" in result
        assert not (target_dir / ".refresh").exists()

    def test_lull_rejects_asleep_target(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        # admin must be non-null — without it, is_human() returns True and
        # is_alive() short-circuits to always-alive, defeating the
        # not-running rejection path.
        (target_dir / ".agent.json").write_text('{"agent_id": "t1", "admin": {}}')

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "lull", "input": {"address": str(target_dir)}})
        assert "error" in result

    def test_interrupt_self_rejected(self, tmp_path):
        agent = _make_agent(tmp_path, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "interrupt", "input": {"address": str(agent.working_dir)}})
        assert "error" in result

    def test_nirvana_requires_nirvana_admin(self, tmp_path):
        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "nirvana", "input": {"address": "/some/path"}})
        assert "error" in result

    def test_nirvana_with_nirvana_admin(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        # admin={} so is_human returns False; no heartbeat file → is_alive returns False
        (target_dir / ".agent.json").write_text('{"agent_id": "t1", "admin": {}}')

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True, "nirvana": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "nirvana", "input": {"address": str(target_dir)}})
        assert result["status"] == "nirvana"
        assert not target_dir.exists()

    def test_nirvana_self_rejected(self, tmp_path):
        agent = _make_agent(tmp_path, admin={"karma": True, "nirvana": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "nirvana", "input": {"address": str(agent.working_dir)}})
        assert "error" in result

    def test_cpr_rejects_alive_target(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        (target_dir / ".agent.json").write_text('{"agent_id": "t1"}')
        (target_dir / ".agent.heartbeat").write_text(str(time.time()))

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "cpr", "input": {"address": str(target_dir)}})
        assert "error" in result
        assert "already running" in result["message"]

    def test_cpr_without_handler_returns_error(self, tmp_path):
        target_dir = tmp_path / "target"
        target_dir.mkdir()
        # admin={} so is_human returns False (admin is not None)
        (target_dir / ".agent.json").write_text('{"agent_id": "t1", "admin": {}}')

        sender_base = tmp_path / "sender"
        sender_base.mkdir()
        agent = _make_agent(sender_base, admin={"karma": True})
        from lingtai.tools.system import handle
        result = handle(agent, {"action": "cpr", "input": {"address": str(target_dir)}})
        assert "error" in result
        assert "not supported" in result["message"].lower()


class TestCPRLingtai:
    """CPR via lingtai Agent (full reconstruction)."""

    def test_cpr_reconstructs_agent(self, tmp_path):
        from lingtai.agent import Agent

        svc = MagicMock()
        svc.create_session.return_value = MagicMock()
        svc.provider = "mock"
        svc.model = "test-model"
        svc._base_url = None

        # Create an agent — this should persist LLM config
        agent = Agent(svc, working_dir=tmp_path / "alice000001",
                      agent_name="alice", admin={"karma": True})

        # Verify LLM config was persisted to working dir
        import json
        llm_config_path = agent.working_dir / "system" / "llm.json"
        assert llm_config_path.is_file()
        llm_config = json.loads(llm_config_path.read_text())
        assert llm_config["provider"] == "mock"
        assert llm_config["model"] == "test-model"

    @staticmethod
    def _cpr_service():
        svc = MagicMock()
        svc.create_session.return_value = MagicMock()
        svc.provider = "mock"
        svc.model = "test-model"
        svc._base_url = None
        return svc

    @staticmethod
    def _stage_cpr_target(target: Path) -> None:
        """Create the non-human target shape needed to reach CPR's watchdog."""
        import json

        target.mkdir(parents=True)
        (target / ".agent.json").write_text(json.dumps({
            "agent_id": "target",
            "admin": {},
        }), encoding="utf-8")
        (target / "init.json").write_text(json.dumps({
            "manifest": {
                "agent_name": "target",
                "llm": {"provider": "mock", "model": "test-model"},
            },
        }), encoding="utf-8")

    @staticmethod
    def _cpr_reviver(tmp_path, svc):
        from lingtai.agent import Agent

        reviver_dir = tmp_path / "reviver"
        reviver_dir.mkdir()
        return Agent(
            svc,
            working_dir=reviver_dir / "admin000001",
            agent_name="admin",
            admin={"karma": True},
        )

    def test_cpr_agent_hook_returns_truthy(self, tmp_path):
        """A confirmed fresh heartbeat keeps the established truthy path."""
        target = tmp_path / "agents" / "bobbob000001"
        self._stage_cpr_target(target)
        reviver = self._cpr_reviver(tmp_path, self._cpr_service())
        fake_proc = MagicMock()
        fake_proc.pid = 99999

        # Stub Popen and venv resolution so no child process or venv probe runs.
        # Patching the Core observation at its imported source makes this a
        # confirmed-heartbeat test even though the staged target is non-human.
        with (
            patch.object(reviver, "_log") as log,
            patch("subprocess.Popen", return_value=fake_proc),
            patch("lingtai.venv_resolve.resolve_venv", return_value=Path("/fake/venv")),
            patch("lingtai.venv_resolve.venv_python", return_value="/fake/venv/bin/python"),
            patch("lingtai.kernel.agent_presence.observe_alive", return_value=True),
        ):
            resuscitated = reviver._cpr_agent(str(target))

        assert resuscitated is True
        assert fake_proc.poll.call_count == 0
        log.assert_any_call("cpr_alive", target=str(target), pid=99999)

    def test_cpr_agent_returns_unconfirmed_running_child(self, tmp_path):
        """A live child with delayed heartbeat is not a false CPR failure."""
        target = tmp_path / "agents" / "delayed00001"
        self._stage_cpr_target(target)
        reviver = self._cpr_reviver(tmp_path, self._cpr_service())
        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_proc.poll.return_value = None
        log_path = target / "logs" / "cpr_relaunch.log"

        # The fake clock enters one poll iteration then reaches the derived
        # twice-liveness confirmation boundary. The final values drive the
        # required final observation.
        with (
            patch.object(reviver, "_log") as log,
            patch("subprocess.Popen", return_value=fake_proc),
            patch("lingtai.venv_resolve.resolve_venv", return_value=Path("/fake/venv")),
            patch("lingtai.venv_resolve.venv_python", return_value="/fake/venv/bin/python"),
            patch("lingtai.kernel.agent_presence.observe_alive", return_value=False) as observe_alive,
            patch("time.time", side_effect=[0.0, 0.0, 0.0, 2 * config.HEARTBEAT_LIVENESS_SECONDS, 2 * config.HEARTBEAT_LIVENESS_SECONDS]),
            patch("time.sleep"),
        ):
            resuscitated = reviver._cpr_agent(str(target))

        assert resuscitated is True
        assert observe_alive.call_count == 2
        assert fake_proc.poll.call_count == 2
        log.assert_any_call(
            "cpr_launch_unconfirmed",
            target=str(target),
            pid=99999,
            log=str(log_path),
        )
        assert all(call.args[0] != "cpr_timeout" for call in log.call_args_list)

    @pytest.mark.parametrize("exit_code", [0, 23])
    def test_cpr_agent_reports_early_exit_before_heartbeat(self, tmp_path, exit_code):
        """Any observed child exit, including zero, remains a launch failure."""
        target = tmp_path / "agents" / "exited000001"
        self._stage_cpr_target(target)
        reviver = self._cpr_reviver(tmp_path, self._cpr_service())
        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_proc.poll.return_value = exit_code
        log_path = target / "logs" / "cpr_relaunch.log"

        with (
            patch.object(reviver, "_log") as log,
            patch("subprocess.Popen", return_value=fake_proc),
            patch("lingtai.venv_resolve.resolve_venv", return_value=Path("/fake/venv")),
            patch("lingtai.venv_resolve.venv_python", return_value="/fake/venv/bin/python"),
            patch("lingtai.kernel.agent_presence.observe_alive", return_value=False),
            patch("time.time", side_effect=[0.0, 0.0, 0.0]),
        ):
            result = reviver._cpr_agent(str(target))

        assert result["error"] is True
        assert result["exit_code"] == exit_code
        assert result["log"] == str(log_path)
        assert f"exit code {exit_code}" in result["message"]
        assert "Last log output:" in result["message"]
        log.assert_any_call(
            "cpr_failed",
            target=str(target),
            pid=99999,
            exit_code=exit_code,
            log=str(log_path),
        )

    def test_cpr_agent_reports_exit_observed_at_confirmation_deadline(self, tmp_path):
        """The final status poll also classifies a just-exited child as failed."""
        target = tmp_path / "agents" / "deadline00001"
        self._stage_cpr_target(target)
        reviver = self._cpr_reviver(tmp_path, self._cpr_service())
        fake_proc = MagicMock()
        fake_proc.pid = 99999
        fake_proc.poll.side_effect = [None, 0]
        log_path = target / "logs" / "cpr_relaunch.log"

        with (
            patch.object(reviver, "_log") as log,
            patch("subprocess.Popen", return_value=fake_proc),
            patch("lingtai.venv_resolve.resolve_venv", return_value=Path("/fake/venv")),
            patch("lingtai.venv_resolve.venv_python", return_value="/fake/venv/bin/python"),
            patch("lingtai.kernel.agent_presence.observe_alive", return_value=False) as observe_alive,
            patch("time.time", side_effect=[0.0, 0.0, 0.0, 2 * config.HEARTBEAT_LIVENESS_SECONDS, 2 * config.HEARTBEAT_LIVENESS_SECONDS]),
            patch("time.sleep"),
        ):
            result = reviver._cpr_agent(str(target))

        assert result["error"] is True
        assert result["exit_code"] == 0
        assert result["log"] == str(log_path)
        assert observe_alive.call_count == 2
        assert fake_proc.poll.call_count == 2
        log.assert_any_call(
            "cpr_failed",
            target=str(target),
            pid=99999,
            exit_code=0,
            log=str(log_path),
        )
        assert all(call.args[0] != "cpr_launch_unconfirmed" for call in log.call_args_list)


class TestSelfSleepPendingNotificationsGuard:
    """Regression: system(sleep) must not transition to ASLEEP while
    `.notification/` has unprocessed payloads on disk.

    Reported as lingtai-kernel#112 by @TZZheng: mail arriving during an
    ACTIVE turn that already decided to sleep was deferred (correct),
    but `system(sleep)` then transitioned the agent to ASLEEP without
    re-checking the queue, leaving the first email unprocessed until a
    second email arrived to wake the agent.
    """

    def _write_notification(self, agent, name, payload):
        import json
        notif_dir = agent.working_dir / ".notification"
        notif_dir.mkdir(parents=True, exist_ok=True)
        path = notif_dir / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_sleep_refused_when_notification_pending(self, tmp_path):
        """Pin the exact kernel#112 race: the turn observed an empty queue,
        mail arrived during the LLM call, then the agent requested sleep."""
        from lingtai.tools.system import handle
        agent = _make_agent(tmp_path)
        # Simulate the pre-turn baseline: no notifications observed yet; the
        # payload below is the mail that arrives mid-turn.
        agent._notification_fp = ()

        self._write_notification(agent, "email.json", {
            "header": "human mail",
            "icon": "📧",
            "priority": "normal",
            "data": {"count": 1},
        })

        result = handle(agent, {"action": "sleep", "input": {"reason": "test"}})

        assert result.get("status") == "ok"
        # Refusal message, not the sleep confirmation
        assert "refused" in result.get("message", "").lower()
        # State must NOT have transitioned.
        assert agent.state != AgentState.ASLEEP, (
            "kernel#112 regression: agent must not sleep with mail waiting"
        )
        assert not agent._asleep.is_set()

    def test_sleep_force_true_overrides_pending_guard(self, tmp_path):
        from lingtai.tools.system import handle
        agent = _make_agent(tmp_path)
        agent._notification_fp = ()
        self._write_notification(agent, "email.json", {
            "header": "mail", "icon": "📧",
            "priority": "normal", "data": {},
        })

        result = handle(agent, {
            "action": "sleep", "input": {"reason": "really tired", "force": True},
        })

        assert result.get("status") == "ok"
        assert agent.state == AgentState.ASLEEP
        assert agent._asleep.is_set()

    def test_sleep_proceeds_when_queue_empty(self, tmp_path):
        """No notifications on disk — sleep should behave as before."""
        from lingtai.tools.system import handle
        agent = _make_agent(tmp_path)
        agent._notification_fp = ()

        result = handle(agent, {"action": "sleep", "input": {"reason": "idle"}})

        assert result.get("status") == "ok"
        assert agent.state == AgentState.ASLEEP
        assert agent._asleep.is_set()

    def test_sleep_proceeds_when_fingerprint_already_committed(self, tmp_path):
        """A notification on disk whose fingerprint matches the agent's
        last-committed fingerprint = already processed. Sleep is fine."""
        from lingtai.tools.system import handle
        from tests._notification_store_helpers import fingerprint_notifications
        agent = _make_agent(tmp_path)

        self._write_notification(agent, "email.json", {
            "header": "old mail", "icon": "📧",
            "priority": "normal", "data": {},
        })
        # Pretend the notification heartbeat has already injected + committed
        agent._notification_fp = fingerprint_notifications(agent.working_dir)

        result = handle(agent, {"action": "sleep", "input": {"reason": "all caught up"}})

        assert result.get("status") == "ok"
        assert agent.state == AgentState.ASLEEP


def test_is_alive_default_threshold_synced_with_kernel():
    """karma's local _is_alive defaults to the kernel liveness window (no 2.0 drift)."""
    import inspect

    from lingtai.kernel import config
    from lingtai.kernel.agent_presence import DEFAULT_LIVENESS_THRESHOLD_SECONDS
    from lingtai.tools.system.karma import _is_alive

    default = inspect.signature(_is_alive).parameters["threshold"].default
    assert (
        default
        == DEFAULT_LIVENESS_THRESHOLD_SECONDS
        == config.HEARTBEAT_LIVENESS_SECONDS
    )
