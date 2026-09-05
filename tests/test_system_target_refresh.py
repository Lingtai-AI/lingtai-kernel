"""Focused evidence for the karma-gated ``system.target_refresh`` action.

The action submits the established ``<target>/.refresh`` marker.  The target's
existing heartbeat/refresh handshake owns every later step, so a successful
receipt proves submission only.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from lingtai.tools import system as system_tool
from lingtai.tools.system.karma import _TARGET_REFRESH_SUBMITTED_MESSAGE

from tests._refresh_watcher_helpers import make_test_refresh_watcher
from tests._service_helpers import make_gemini_mock_service


_SIGNAL_FILES = (
    ".refresh",
    ".refresh.taken",
    ".sleep",
    ".suspend",
    ".interrupt",
    ".clear",
)


class _StubCaller:
    def __init__(self, working_dir: Path, *, karma: bool = True) -> None:
        self._working_dir = working_dir
        self._admin = {"karma": karma}
        self.agent_name = "caller"
        self.events: list[tuple[str, dict[str, Any]]] = []

    def _log(self, event: str, **fields: Any) -> None:
        self.events.append((event, fields))


def _make_caller(tmp_path: Path, *, karma: bool = True) -> _StubCaller:
    workdir = tmp_path / "caller"
    workdir.mkdir()
    return _StubCaller(workdir, karma=karma)


def _make_target_dir(tmp_path: Path, *, alive: bool = True) -> Path:
    target = tmp_path / "target"
    target.mkdir()
    # A non-null admin value identifies an agent rather than a human manifest.
    (target / ".agent.json").write_text(
        json.dumps({"agent_id": "target", "admin": {}}), encoding="utf-8"
    )
    if alive:
        (target / ".agent.heartbeat").write_text(str(time.time()), encoding="utf-8")
    return target


def _envelope(address: Path | str, reason: str | None = "rollout") -> dict[str, Any]:
    return {
        "action": "target_refresh",
        "input": {"address": str(address), "reason": reason},
        "reasoning": "focused target_refresh test",
    }


def _assert_no_refresh_signal(target: Path) -> None:
    assert not (target / ".refresh").exists()
    assert not (target / ".refresh.taken").exists()


def test_target_refresh_refuses_a_dead_target_without_marker(tmp_path: Path) -> None:
    target = _make_target_dir(tmp_path, alive=False)
    caller = _make_caller(tmp_path)

    result = system_tool.handle(caller, _envelope(target))

    assert result == {"error": True, "message": f"Agent at {target} is not running"}
    _assert_no_refresh_signal(target)
    assert caller.events == []


def test_target_refresh_submits_only_the_marker_and_truthful_receipt(
    tmp_path: Path,
) -> None:
    target = _make_target_dir(tmp_path)
    caller = _make_caller(tmp_path)

    result = system_tool.handle(caller, _envelope(target, reason="rollout 1.0.4"))

    assert result == {
        "status": "refresh_requested",
        "address": str(target),
        "message": _TARGET_REFRESH_SUBMITTED_MESSAGE,
    }
    assert "submitted" in result["message"].lower()
    assert "verify completion" in result["message"].lower()
    assert (target / ".refresh").read_text(encoding="utf-8") == ""
    for name in _SIGNAL_FILES:
        if name != ".refresh":
            assert not (target / name).exists(), name
    assert caller.events == [
        ("karma_target_refresh", {"target": str(target), "reason": "rollout 1.0.4"})
    ]


class _OneTickStop:
    """Run one heartbeat iteration without starting a real heartbeat thread."""

    def __init__(self, agent: Any) -> None:
        self._agent = agent

    def wait(self, _seconds: float) -> bool:
        self._agent._heartbeat_thread = None
        return True

    def set(self) -> None:  # pragma: no cover - not reached in one tick
        pass

    def is_set(self) -> bool:
        return False


def _run_one_heartbeat_tick(agent: Any) -> None:
    from lingtai.kernel.base_agent import lifecycle

    agent._heartbeat_thread = object()
    agent._heartbeat_stop = _OneTickStop(agent)
    with patch.object(lifecycle, "_check_rules_file"), patch(
        "lingtai.kernel.nudge.run_checks_nonblocking"
    ), patch("lingtai.kernel.nudge.run_system_notifications"):
        lifecycle._heartbeat_loop(agent)


def test_mounted_action_enters_the_targets_existing_refresh_handshake(
    tmp_path: Path,
) -> None:
    from lingtai.agent import Agent
    from lingtai.tools.system import DECLARATION

    target = Agent(
        service=make_gemini_mock_service(),
        working_dir=tmp_path / "target",
        capabilities={},
        admin={},
    )
    caller = Agent(
        service=make_gemini_mock_service(),
        working_dir=tmp_path / "caller",
        capabilities={},
        admin={"karma": True},
    )
    try:
        target._refresh_watcher = make_test_refresh_watcher()
        target._build_launch_cmd = lambda: [
            "python",
            "-c",
            "print('relaunch sentinel')",
        ]
        (target.working_dir / ".agent.heartbeat").write_text(
            str(time.time()), encoding="utf-8"
        )

        assert caller.official_tool_plugins["system"] is DECLARATION
        result = caller._tool_handlers["system"](
            _envelope(target.working_dir, reason="mounted")
        )

        assert result["status"] == "refresh_requested"
        assert (target.working_dir / ".refresh").is_file()
        assert not (target.working_dir / ".refresh.taken").exists()
        assert target._refresh_watcher.spawned is False
        assert getattr(target, "_refresh_started", False) is False

        _run_one_heartbeat_tick(target)

        assert not (target.working_dir / ".refresh").exists()
        assert (target.working_dir / ".refresh.taken").is_file()
        assert target._refresh_watcher.spawned is True
        assert len(target._refresh_watcher.calls) == 1
        request = target._refresh_watcher.last_request
        assert request.taken_path == str(target.working_dir / ".refresh.taken")
        assert request.working_dir == str(target.working_dir)
        assert target._refresh_started is True
        assert target._shutdown.is_set()
    finally:
        caller.stop(timeout=1.0)
        target.stop(timeout=1.0)
