"""Focused proofs for Soul's five-field, read-only settings action."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import SimpleNamespace

from lingtai.kernel.config import DEFAULT_SOUL_DELAY_SECONDS
from lingtai.kernel.tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES
from lingtai.tools import soul

_DEFAULT_INPUT = object()


class _Runtime:
    def __init__(
        self,
        *,
        delay: float = DEFAULT_SOUL_DELAY_SECONDS,
        count: int = 0,
        voice: str = "inner",
        prompt: str = "",
    ) -> None:
        self.soul_delay = delay
        self.config = SimpleNamespace(
            consultation_past_count=count,
            soul_voice=voice,
            soul_voice_prompt=prompt,
        )
        self.dismissed: list[tuple[str, str]] = []

    def dismiss_notification(self, channel: str, *, invoked_by: str) -> dict:
        self.dismissed.append((channel, invoked_by))
        return {"status": "ok"}


def _family(runtime: object, manual_source: Path):
    return soul._build_family(runtime, manual_source)


def _show(family, action_input: object = _DEFAULT_INPUT) -> dict:
    if action_input is _DEFAULT_INPUT:
        action_input = {}
    return family.handle(
        {"action": "settings", "input": action_input, "reasoning": "inspect"}
    )


def test_exact_five_field_rows_are_fresh_and_fully_redacted(tmp_path, monkeypatch):
    monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "yes")
    runtime = _Runtime(
        delay=300.0,
        count=2,
        voice="custom",
        prompt="fixture soul framing",
    )
    family = _family(runtime, tmp_path)

    result = _show(family)

    assert result == {
        "settings": [
            {
                "key": "flow_enabled",
                "current": True,
                "default": False,
                "configurable": True,
                "comment": "soul-manual#flow-enabled",
            },
            {
                "key": "delay_seconds",
                "current": 300.0,
                "default": DEFAULT_SOUL_DELAY_SECONDS,
                "configurable": True,
                "comment": "soul-manual#delay-seconds",
            },
            {
                "key": "consultation_past_count",
                "current": 2,
                "default": 0,
                "configurable": True,
                "comment": "soul-manual#consultation-past-count",
            },
            {
                "key": "voice",
                "current": "custom",
                "default": "inner",
                "configurable": True,
                "comment": "soul-manual#voice",
            },
            {
                "key": "voice_prompt",
                "current": "<redacted>",
                "default": "<redacted>",
                "configurable": True,
                "comment": "soul-manual#voice-prompt",
            },
        ]
    }
    assert "fixture soul framing" not in repr(result)
    assert [row["key"] for row in result["settings"]] == [
        "flow_enabled",
        "delay_seconds",
        "consultation_past_count",
        "voice",
        "voice_prompt",
    ]
    assert all(
        list(row) == ["key", "current", "default", "configurable", "comment"]
        for row in result["settings"]
    )

    monkeypatch.setenv("LINGTAI_SOUL_FLOW_ENABLED", "0")
    runtime.soul_delay = 600.0
    runtime.config.consultation_past_count = 4
    runtime.config.soul_voice = "observer"
    runtime.config.soul_voice_prompt = "replacement fixture"
    refreshed = _show(family)["settings"]
    assert [row["current"] for row in refreshed] == [
        False,
        600.0,
        4,
        "observer",
        "<redacted>",
    ]
    assert "replacement fixture" not in repr(refreshed)


def test_settings_is_strict_read_only_and_comments_target_manual_sections(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("LINGTAI_SOUL_FLOW_ENABLED", raising=False)
    runtime = _Runtime(delay=120.0, count=4, voice="observer")
    before = (
        runtime.soul_delay,
        vars(runtime.config).copy(),
        os.environ.get("LINGTAI_SOUL_FLOW_ENABLED"),
        tuple(tmp_path.rglob("*")),
    )
    family = _family(runtime, tmp_path)

    rows = _show(family)["settings"]

    assert (
        runtime.soul_delay,
        vars(runtime.config),
        os.environ.get("LINGTAI_SOUL_FLOW_ENABLED"),
        tuple(tmp_path.rglob("*")),
    ) == before
    assert all(
        _show(family, invalid)["error_code"] == "INVALID_ARGUMENT"
        for invalid in ({"set": "voice"}, [], None)
    )
    assert (runtime.soul_delay, vars(runtime.config)) == before[:2]
    manual = (
        Path(soul.__file__).with_name("manual").joinpath("SKILL.md").read_text(
            encoding="utf-8"
        )
    )
    expected_headings = {
        "soul-manual#flow-enabled": "### Flow enabled",
        "soul-manual#delay-seconds": "### Delay seconds",
        "soul-manual#consultation-past-count": "### Consultation past count",
        "soul-manual#voice": "### Voice",
        "soul-manual#voice-prompt": "### Voice prompt",
    }
    assert {row["comment"] for row in rows} == set(expected_headings)
    assert all(heading in manual for heading in expected_headings.values())


def test_unavailable_or_non_json_live_current_fails_the_whole_action(tmp_path):
    class _UnavailableRuntime:
        config = SimpleNamespace(
            consultation_past_count=2,
            soul_voice="inner",
            soul_voice_prompt="",
        )

        @property
        def soul_delay(self):
            raise RuntimeError("private live-state failure")

    failure = {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }
    assert _show(_family(_UnavailableRuntime(), tmp_path)) == failure
    assert _show(_family(_Runtime(delay=float("inf")), tmp_path)) == failure


def test_family_opt_in_order_and_unchanged_dismiss_action(tmp_path):
    assert soul.DECLARATION.settings is True
    module_names = {"shell": "bash._tool_family", "web": "web_search"}
    enabled = [
        name
        for name in OFFICIAL_TOOL_PLUGIN_NAMES
        if importlib.import_module(
            f"lingtai.tools.{module_names.get(name, name)}"
        ).DECLARATION.settings
    ]
    assert enabled == [
        "mcp", "avatar", "daemon", "email", "file", "plugin", "notification",
        "shell", "soul", "system", "task_card", "vision", "web",
    ]
    assert soul.DECLARATION.public_actions == (
        "inquiry",
        "flow",
        "config",
        "voice",
        "dismiss",
        "settings",
        "manual",
    )
    schema = soul.get_schema()
    assert schema["properties"]["action"]["enum"] == list(
        soul.DECLARATION.public_actions
    )
    assert next(
        branch
        for branch in schema["properties"]["input"]["anyOf"]
        if branch["title"] == "settings inventory input"
    ) == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
        "title": "settings inventory input",
    }

    runtime = _Runtime()
    result = _family(runtime, tmp_path).handle(
        {"action": "dismiss", "input": {}, "reasoning": "clear notice"}
    )
    assert result == {
        "status": "ok",
        "message": "Soul flow notification dismissed.",
    }
    assert runtime.dismissed == [("soul", "soul")]
