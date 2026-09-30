"""Live, provider-aware Claude Code reasoning-effort control tests."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from lingtai.kernel.config import AgentConfig
from lingtai.kernel.llm.reasoning_effort import (
    ReasoningEffortCapability,
    ReasoningEffortController,
)
from lingtai.kernel.session import SessionManager, _safe_usage_extra_for_event
from lingtai.llm.claude_code.adapter import ClaudeCodeAdapter
from lingtai.llm.claude_code.live_effort import resolve_claude_effort_descriptor


class _FakeProc:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _envelope(text: str, *, session_id: str = "sess-123") -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "session_id": session_id,
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }
    )


def _make_bound_session(*, thinking: str = "default"):
    adapter = ClaudeCodeAdapter(model="opus")
    chat = adapter.create_chat("opus", "system", thinking=thinking)
    service = MagicMock()
    service.model = "opus"
    service.create_session.return_value = chat
    manager = SessionManager(
        llm_service=service,
        config=AgentConfig(provider="claude-code", model="opus", thinking=thinking),
        agent_name="test",
        streaming=False,
        build_system_prompt_fn=lambda: "system",
        build_tool_schemas_fn=lambda: [],
        logger_fn=None,
    )
    manager.chat = chat
    return manager, chat


def test_claude_descriptor_is_provider_local_and_fail_closed():
    descriptor = resolve_claude_effort_descriptor(
        model=" opus ", cli_path=" /usr/local/bin/claude ", construction_baseline=None
    )
    assert descriptor is not None
    assert descriptor.model == "opus"
    assert descriptor.cli_path == "/usr/local/bin/claude"
    assert descriptor.provider_default is None
    assert descriptor.construction_baseline is None
    assert descriptor.values == ("low", "medium", "high", "xhigh", "max")
    assert descriptor.to_capability().available is True
    assert descriptor.fingerprint
    assert descriptor.fingerprint != resolve_claude_effort_descriptor(
        model="sonnet", cli_path="/usr/local/bin/claude", construction_baseline=None
    ).fingerprint
    assert resolve_claude_effort_descriptor(
        model="", cli_path="claude", construction_baseline=None
    ) is None
    assert resolve_claude_effort_descriptor(
        model="opus", cli_path="", construction_baseline=None
    ) is None
    assert resolve_claude_effort_descriptor(
        model="opus", cli_path="claude", construction_baseline="minimal"
    ) is None


def test_controller_rejects_invalid_requests_without_mutating_state():
    controller = ReasoningEffortController()
    assert controller.set("high").reason == "unavailable"
    capability = ReasoningEffortCapability(
        available=True,
        route="claude-code:opus",
        values=("low", "high"),
        baseline=None,
        settable=True,
        fingerprint="route-1",
        reason=None,
    )
    controller.bind_capability(capability)
    before = controller.status()
    result = controller.set("medium")
    assert not result.ok
    assert result.reason == "unsupported_value"
    assert controller.status() == before
    assert controller.set("high").ok
    assert controller.status()["effective"] == "high"
    assert controller.clear().ok
    assert controller.status()["effective"] is None


def test_controller_drops_override_when_route_fingerprint_drifts():
    controller = ReasoningEffortController()
    first = ReasoningEffortCapability(
        available=True, values=("low",), settable=True, fingerprint="route-1", reason=None
    )
    second = ReasoningEffortCapability(
        available=True, values=("low",), settable=True, fingerprint="route-2", reason=None
    )
    controller.bind_capability(first)
    assert controller.set("low").ok
    assert not controller.bind_capability(second)
    assert controller.status()["override"] is None
    assert controller.status()["revision"] == 2


def test_live_set_clear_changes_next_dispatch_and_preserves_baseline_omission():
    manager, chat = _make_bound_session(thinking="default")
    captured: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        captured.append(list(cmd))
        return _FakeProc(_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        first = chat.send("first")
        assert first.usage.extra["claude_reasoning_effort"] == "omitted"
        assert manager.set_reasoning_effort("high").ok
        second = chat.send("second")
        assert second.usage.extra["claude_reasoning_effort_emitted"] == "high"
        assert manager.clear_reasoning_effort().ok
        third = chat.send("third")
        assert third.usage.extra["claude_reasoning_effort"] == "omitted"

    assert "--effort" not in captured[0]
    assert captured[1][captured[1].index("--effort") + 1] == "high"
    assert "--effort" not in captured[2]
    assert manager.last_reasoning_effort_dispatch()["completed"] is True


def test_dispatch_uses_one_snapshot_when_set_changes_during_subprocess():
    manager, chat = _make_bound_session(thinking="default")
    captured: list[list[str]] = []
    changed = False

    def fake_run(cmd, **kwargs):
        nonlocal changed
        captured.append(list(cmd))
        if not changed:
            changed = True
            assert manager.set_reasoning_effort("low").ok
        return _FakeProc(_envelope('{"action":"final","text":"ok"}'))

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        chat.send("first")
        chat.send("second")

    assert "--effort" not in captured[0]
    assert captured[1][captured[1].index("--effort") + 1] == "low"


def test_failed_dispatch_records_incomplete_evidence_and_rolls_back():
    manager, chat = _make_bound_session(thinking="default")

    def fake_run(cmd, **kwargs):
        return _FakeProc("", "permission denied", returncode=1)

    with patch("lingtai.llm.claude_code.adapter.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError):
            chat.send("will fail")

    evidence = manager.last_reasoning_effort_dispatch()
    assert evidence is not None
    assert evidence["completed"] is False
    assert evidence["effective"] is None
    assert manager.reasoning_effort_status()["provider_controlled"] is True
    assert not any(
        getattr(block, "text", None) == "will fail"
        for entry in chat.interface.entries
        for block in entry.content
    )


def test_session_manager_rebinds_same_route_without_losing_override():
    manager, chat = _make_bound_session(thinking="default")
    assert manager.set_reasoning_effort("max").ok
    before = manager.reasoning_effort_status()
    manager.chat = chat
    after = manager.reasoning_effort_status()
    assert after["override"] == "max"
    assert after["fingerprint"] == before["fingerprint"]
    assert after["revision"] == before["revision"]


def test_claude_effort_evidence_is_safe_for_llm_events():
    assert _safe_usage_extra_for_event({
        "claude_reasoning_effort": "high",
        "claude_reasoning_effort_emitted": "high",
        "claude_reasoning_effort_source": "override",
        "claude_reasoning_effort_revision": 3,
        "prompt": "must-not-leak",
    }) == {
        "claude_reasoning_effort": "high",
        "claude_reasoning_effort_emitted": "high",
        "claude_reasoning_effort_source": "override",
        "claude_reasoning_effort_revision": "3",
    }


def test_claude_code_is_manifest_thinking_capable_without_global_vocab_claim():
    from lingtai.init_schema import validate_init

    base = {"manifest": {"llm": {"provider": "claude-code", "model": "sonnet"}}, "pad": ""}
    base["manifest"]["llm"]["thinking"] = "high"
    validate_init(base)
    base["manifest"]["llm"]["provider"] = "claude_code"
    with pytest.raises(ValueError, match="use provider claude-code"):
        validate_init(base)
