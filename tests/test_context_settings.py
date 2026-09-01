"""Focused evidence for Context's five-field settings opt-in."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from lingtai.agent import Agent
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.tools import context as context_tool
from lingtai.tools.context import ACTION_ORDER, DECLARATION, get_schema
from lingtai.tools.context.settings import build_context_settings_provider
from lingtai.tools.tool_family import ChildTool, ToolFamily
from tests._service_helpers import make_gemini_mock_service


_EMPTY = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}
_COMMENTS = {
    "context_limit": "context-manual#context-limit",
    "summarize_notification_threshold": (
        "context-manual#summarize-notification-threshold"
    ),
    "system_prompt_pressure_ratio": (
        "context-manual#system-prompt-pressure-ratio"
    ),
    "pressure_high_ratio": "context-manual#pressure-high-ratio",
    "forced_rebuild_ratio": "context-manual#forced-rebuild-ratio",
    "pressure_warn_after_rounds": "context-manual#pressure-warn-after-rounds",
    "recovery_target_ratio": "context-manual#recovery-target-ratio",
}


@pytest.fixture
def context_agent(tmp_path):
    service = make_gemini_mock_service()
    service._context_window = 272_000
    agent = Agent(
        service=service,
        agent_name="context-settings",
        working_dir=tmp_path / "agent",
        capabilities={},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


def _settings(agent, action_input) -> dict:
    return agent._tool_handlers["context"](
        {"action": "settings", "input": action_input, "reasoning": "test"}
    )


def _rows(agent) -> dict[str, dict]:
    return {row["key"]: row for row in _settings(agent, {})["settings"]}


def test_context_is_the_only_family_opted_in_here():
    assert DECLARATION.settings is True
    assert DECLARATION.actions == ("molt", "summarize", "rebuild")
    assert DECLARATION.public_actions == (
        "molt",
        "summarize",
        "rebuild",
        "settings",
        "manual",
    )
    assert ACTION_ORDER == DECLARATION.public_actions
    assert get_schema()["properties"]["action"]["enum"] == list(ACTION_ORDER)


def test_exact_five_field_inventory_and_manual_targets(context_agent, monkeypatch):
    monkeypatch.delenv("LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO", raising=False)
    expected = {
        "context_limit": (272_000, 272_000, True),
        "summarize_notification_threshold": (3000, 3000, True),
        "system_prompt_pressure_ratio": (0.4, 0.4, True),
        "pressure_high_ratio": (0.85, 0.85, False),
        "forced_rebuild_ratio": (1.0, 1.0, False),
        "pressure_warn_after_rounds": (3, 3, False),
        "recovery_target_ratio": (0.75, 0.75, False),
    }
    result = _settings(context_agent, {})
    assert result == {
        "settings": [
            {
                "key": key,
                "current": current,
                "default": default,
                "configurable": configurable,
                "comment": _COMMENTS[key],
            }
            for key, (current, default, configurable) in expected.items()
        ]
    }
    assert all(set(row) == {
        "key", "current", "default", "configurable", "comment",
    } for row in result["settings"])
    assert "<redacted>" not in repr(result)  # These seven scalars are non-sensitive.

    manual = (
        context_agent.working_dir
        / ".library/intrinsic/capabilities/context-manual/SKILL.md"
    ).read_text(encoding="utf-8")
    headings = {
        "context-manual#" + heading.lower().replace(" ", "-"): heading
        for heading in (
            "Context limit",
            "Summarize notification threshold",
            "System prompt pressure ratio",
            "Pressure high ratio",
            "Forced rebuild ratio",
            "Pressure warn after rounds",
            "Recovery target ratio",
        )
    }
    assert headings.keys() == set(_COMMENTS.values())
    assert all(f"## {heading}" in manual for heading in headings.values())


def test_live_runtime_and_environment_values_are_read_fresh(context_agent, monkeypatch):
    context_agent._config.context_limit = 64_000
    context_agent._summarize_notification_threshold = 0
    monkeypatch.setenv("LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO", "0.625")
    rows = _rows(context_agent)
    assert rows["context_limit"]["current"] == 64_000
    assert rows["summarize_notification_threshold"]["current"] == 0
    assert rows["system_prompt_pressure_ratio"]["current"] == 0.625

    monkeypatch.setenv("LINGTAI_SYSTEM_PROMPT_PRESSURE_RATIO", "invalid")
    assert _rows(context_agent)["system_prompt_pressure_ratio"]["current"] == 0.4


def test_strict_empty_input_and_unavailable_current_fail_as_one_action(context_agent):
    for value in (None, [], {"set": "context_limit"}):
        assert _settings(context_agent, value)["status"] == "failed"

    unavailable = build_context_settings_provider(lambda: 0, lambda: 3000)
    family = ToolFamily(
        "context-probe",
        [ChildTool("manual", _EMPTY, lambda _value: {})],
        settings_provider=unavailable,
    )
    assert family.handle(
        {"action": "settings", "input": {}, "reasoning": "test"}
    ) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_rebuild_dispatch_is_unchanged_by_settings_opt_in(tmp_path):
    class Chat:
        def __init__(self):
            self.interface = ChatInterface()
            self.rebuilds = 0

        def request_history_rebuild(self, reason=""):
            self.rebuilds += 1
            return True

    subject = SimpleNamespace(
        _working_dir=tmp_path,
        _tool_handlers={},
        _config=SimpleNamespace(context_limit=None),
        _summarize_notification_threshold=3000,
        _chat=Chat(),
        _log=lambda *_args, **_kwargs: None,
    )
    reconstructions = []
    subject._reconstruct_context = lambda: reconstructions.append("reconstructed")

    result = context_tool.handle(subject, {"action": "rebuild", "input": {}})
    assert result["status"] == "ok"
    assert result["prompt_reconstructed"] is True
    assert reconstructions == ["reconstructed"]
    assert subject._chat.rebuilds == 1
