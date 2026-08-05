"""Focused unit tests for the preemptive parallel steering lane.

Covers: lane spawned during tool_call; reply delivered on channel; interrupt
request aborts at the boundary; no-interrupt leaves the main turn running.
"""
from __future__ import annotations

import json
import queue
import threading
from pathlib import Path

import pytest

from lingtai.kernel import steering
from lingtai.kernel.config import AgentConfig


class _FakeAgent:
    """Minimal agent surface the steering module touches."""

    def __init__(self, tmp_path: Path, *, steering_enabled: bool = True):
        self.working_dir = tmp_path
        self._config = AgentConfig(steering_enabled=steering_enabled)
        self._active_turn_kind = "tool_call"
        self._cancel_event = threading.Event()
        self._events: list[dict] = []
        self.inbox: queue.Queue = queue.Queue()
        self._woke: list[str] = []
        self._steering_reply_hook = None
        self._steering_current_tool_pid = None
        self._mcp_clients_by_tool = {}

    def _log(self, event_type: str, **fields) -> None:
        self._events.append({"type": event_type, **fields})

    def _wake_nap(self, reason: str) -> None:
        self._woke.append(reason)

    @property
    def _chat(self):
        return None


@pytest.fixture()
def fake_agent(tmp_path):
    return _FakeAgent(tmp_path)


def _wait_for(predicate, timeout=5.0):
    deadline = __import__("time").monotonic() + timeout
    while __import__("time").monotonic() < deadline:
        if predicate():
            return True
        __import__("time").sleep(0.02)
    return False


class TestLaneSpawnedDuringToolCall:
    def test_dispatch_creates_lane_run_dir_and_seed(self, fake_agent):
        notifications = {
            "mcp.telegram": {"data": {"text": "Stop the current run now"}},
            "email": {"body": "low priority"},
        }
        captured: dict = {}

        def runner(agent, run_dir, seed):
            captured["seed"] = seed
            captured["run_dir"] = run_dir

        result = steering.dispatch_steering_lane(
            fake_agent, notifications, runner=runner
        )
        assert result is not None
        assert result["status"] == "dispatched"
        assert result["channel"] == "mcp.telegram"
        # Lane run dir created under daemons/ with seed + prompt.
        run_dir = Path(fake_agent.working_dir) / "daemons" / result["run_id"]
        assert run_dir.is_dir()
        assert (run_dir / "steering_seed.json").is_file()
        prompt = (run_dir / "steering_prompt.txt").read_text(encoding="utf-8")
        assert "Stop the current run now" in prompt
        # Thread ran the runner.
        assert _wait_for(lambda: "seed" in captured)
        assert captured["seed"]["channel"] == "mcp.telegram"
        assert "Stop the current run now" in captured["seed"]["message"]

    def test_no_dispatch_when_steering_disabled(self, tmp_path):
        agent = _FakeAgent(tmp_path, steering_enabled=False)
        notifications = {"mcp.telegram": {"text": "hi"}}
        assert (
            steering.dispatch_steering_lane(agent, notifications, runner=lambda *a: None)
            is None
        )

    def test_no_dispatch_for_non_priority_channel(self, fake_agent):
        notifications = {"cron": {"text": "tick"}}
        assert (
            steering.dispatch_steering_lane(fake_agent, notifications, runner=lambda *a: None)
            is None
        )


class TestReplyDelivered:
    def test_reply_hook_called(self, fake_agent):
        delivered: list[tuple] = []
        fake_agent._steering_reply_hook = (
            lambda channel, message, reply: delivered.append((channel, message, reply))
        )
        steering.deliver_steering_reply(
            fake_agent, "mcp.telegram", "the message", "I'm on it."
        )
        assert delivered == [("mcp.telegram", "the message", "I'm on it.")]

    def test_reply_stored_when_no_hook(self, fake_agent):
        steering.deliver_steering_reply(
            fake_agent, "mcp.telegram", "the message", "I'm on it."
        )
        stored = (
            Path(fake_agent.working_dir) / "daemons" / "steering_reply.txt"
        )
        assert stored.is_file()
        data = json.loads(stored.read_text(encoding="utf-8"))
        assert data["reply"] == "I'm on it."
        assert data["channel"] == "mcp.telegram"

    def test_empty_reply_not_delivered(self, fake_agent):
        fake_agent._steering_reply_hook = (
            lambda channel, message, reply: delivered.append((channel, reply))
        )
        delivered: list = []
        steering.deliver_steering_reply(fake_agent, "mcp.telegram", "m", "")
        assert delivered == []


class TestInterruptContract:
    def test_interrupt_request_aborts_at_boundary(self, fake_agent):
        run_dir = Path(fake_agent.working_dir) / "daemons" / "steer-test1"
        run_dir.mkdir(parents=True)
        (run_dir / "steering_interrupt.json").write_text(
            json.dumps({"reason": "human said stop", "by": "steering_lane"}),
            encoding="utf-8",
        )

        interrupt = steering.check_steering_interrupt(fake_agent)
        assert interrupt is not None
        assert interrupt["reason"] == "human said stop"
        assert interrupt["run_id"] == "steer-test1"

        steering.abort_current_tool_call(fake_agent, interrupt)
        # Turn marked interrupted, cancel event set, steering message queued.
        assert fake_agent._active_turn_kind == "interrupted"
        assert fake_agent._cancel_event.is_set()
        assert fake_agent._woke == ["steering_interrupt"]
        msg = fake_agent.inbox.get_nowait()
        assert msg.type == "request"
        assert "steering" in msg.content.lower()
        # Interrupt file consumed (renamed) so it fires exactly once.
        assert not (run_dir / "steering_interrupt.json").exists()
        assert (
            run_dir / "steering_interrupt.json.consumed"
        ).exists()
        assert steering.check_steering_interrupt(fake_agent) is None

    def test_no_interrupt_leaves_turn_running(self, fake_agent):
        # No lane / no interrupt file anywhere.
        assert steering.check_steering_interrupt(fake_agent) is None
        assert not fake_agent._cancel_event.is_set()
        assert fake_agent._active_turn_kind == "tool_call"

    def test_interrupt_not_consumed_for_non_lane_dir(self, fake_agent):
        # A daemon run dir without steering_interrupt.json is ignored.
        run_dir = Path(fake_agent.working_dir) / "daemons" / "em-other"
        run_dir.mkdir(parents=True)
        (run_dir / "daemon.json").write_text("{}", encoding="utf-8")
        assert steering.check_steering_interrupt(fake_agent) is None

    def test_taskkill_windows_tree(self, monkeypatch):
        calls: list[list] = []

        class _Popen:
            def __init__(self, argv, **kw):
                calls.append(argv)
                self.returncode = 0

        monkeypatch.setattr("subprocess.run", _Popen)
        steering._taskkill_tree_windows(4242)
        assert calls and calls[0] == ["taskkill", "/T", "/F", "/PID", "4242"]


class TestLaneRunnerDecision:
    def test_interrupt_marker_detected(self):
        reply = "Okay, stopping now.\n[INTERRUPT] user explicitly asked to cancel"
        assert (
            steering._interrupt_reason_from_reply(reply)
            == "user explicitly asked to cancel"
        )

    def test_continue_marker_no_interrupt(self):
        reply = "Will do, one moment.\n[CONTINUE]"
        assert steering._interrupt_reason_from_reply(reply) is None

    def test_default_lane_runner_writes_result_and_interrupt(
        self, fake_agent, monkeypatch
    ):
        class _FakeSession:
            def send(self, prompt, timeout=None):
                class _Resp:
                    text = "Hold on, I will stop.\n[INTERRUPT] stop now"

                return _Resp()

        class _FakeLLM:
            def __init__(self, **kw):
                self.kw = kw

            def create_session(self, **kw):
                return _FakeSession()

        monkeypatch.setattr(steering, "_resolve_llm_service_class", lambda: _FakeLLM)
        fake_agent.service = object()  # type: ignore[attr-defined]
        run_dir = Path(fake_agent.working_dir) / "daemons" / "steer-test2"
        run_dir.mkdir(parents=True)
        (run_dir / "steering_prompt.txt").write_text(
            steering._build_lane_prompt(
                {"channel": "mcp.telegram", "message": "stop", "tail": ""}
            ),
            encoding="utf-8",
        )
        seed = {
            "channel": "mcp.telegram",
            "message": "stop",
            "tail": "",
            "timeout_s": 60.0,
        }
        steering._default_lane_runner(fake_agent, run_dir, seed)
        result = json.loads(
            (run_dir / "steering_result.json").read_text(encoding="utf-8")
        )
        assert result["status"] == "done"
        assert "Hold on" in result["reply"]
        interrupt = json.loads(
            (run_dir / "steering_interrupt.json").read_text(encoding="utf-8")
        )
        assert interrupt["reason"] == "stop now"
        assert interrupt["by"] == "steering_lane"

    def test_default_lane_runner_continue_no_interrupt(
        self, fake_agent, monkeypatch
    ):
        class _FakeSession:
            def send(self, prompt, timeout=None):
                class _Resp:
                    text = "Noted, continuing.\n[CONTINUE]"

                return _Resp()

        class _FakeLLM:
            def __init__(self, **kw):
                pass

            def create_session(self, **kw):
                return _FakeSession()

        monkeypatch.setattr(steering, "_resolve_llm_service_class", lambda: _FakeLLM)
        fake_agent.service = object()  # type: ignore[attr-defined]
        run_dir = Path(fake_agent.working_dir) / "daemons" / "steer-test3"
        run_dir.mkdir(parents=True)
        (run_dir / "steering_prompt.txt").write_text("prompt", encoding="utf-8")
        seed = {
            "channel": "mcp.telegram",
            "message": "ok",
            "tail": "",
            "timeout_s": 60.0,
        }
        steering._default_lane_runner(fake_agent, run_dir, seed)
        assert not (run_dir / "steering_interrupt.json").exists()
