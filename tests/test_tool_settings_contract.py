"""Minimal proofs for opt-in, five-field SHOW settings discovery."""
from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from lingtai.kernel.tool_plugin import (
    BoundToolPlugin, OFFICIAL_TOOL_PLUGIN_NAMES, ToolPluginDeclaration,
    ToolPluginDeclarationError, ToolPluginHost,
)
from lingtai.tools import tool_family as public_family
from lingtai.tools.tool_family import ChildTool, SettingRow, ToolFamily
from lingtai.tools.tool_family import settings as settings_module

_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}
_DEFAULT_INPUT = object()


def _child(name: str) -> ChildTool:
    return ChildTool(name, _EMPTY, lambda value: {"input": dict(value)})


def _family(provider=None) -> ToolFamily:
    return ToolFamily("widget", [_child("probe"), _child("manual")], settings_provider=provider)


def _show(family: ToolFamily, value: Any = _DEFAULT_INPUT) -> dict[str, Any]:
    action_input = {} if value is _DEFAULT_INPUT else value
    return family.handle({"action": "settings", "input": action_input, "reasoning": "test"})


def _declaration(enabled: bool, family: ToolFamily) -> ToolPluginDeclaration:
    return ToolPluginDeclaration(
        "widget", ("probe",), {"probe": _EMPTY}, _EMPTY,
        "widget-manual", "test", lambda _host: BoundToolPlugin(
            "widget", family.build_schema(), family.handle), settings=enabled,
    )


def test_boolean_opt_ins_inject_one_reserved_action():
    plain, opted = _family(), _family(lambda: ())
    assert plain.child_names == ("probe", "manual")
    assert opted.child_names == ("probe", "settings", "manual")
    declaration = _declaration(True, opted)
    assert declaration.public_actions == opted.child_names
    assert declaration.public_input_schemas()["settings"] == {**_EMPTY, "required": []}
    declaration.bind(ToolPluginHost.grant(declaration, {}))
    with pytest.raises(ToolPluginDeclarationError, match="advertising"):
        replace(declaration, binder=lambda _host: BoundToolPlugin(
            "widget", plain.build_schema(), plain.handle)).bind(
                ToolPluginHost.grant(declaration, {}))
    telegram = importlib.import_module("lingtai.mcp_servers.telegram.plugin").TELEGRAM_PLUGIN
    curated = replace(telegram, settings=True)
    assert curated.actions(("probe",)) == ("probe", "settings", "manual")
    built = curated.build_family([_child("probe")], settings_provider=tuple)
    assert built.child_names == ("probe", "settings", "manual")


def test_exact_input_and_exact_five_field_success():
    calls = 0
    def provider():
        nonlocal calls
        calls += 1
        return [SettingRow("example.timeout", 30, 15, True, "example-manual#timeout")]
    family = _family(provider)
    expected = {"settings": [{
        "key": "example.timeout", "current": 30, "default": 15,
        "configurable": True, "comment": "example-manual#timeout",
    }]}
    assert _show(family) == expected
    assert all(_show(family, value)["status"] == "failed"
               for value in (None, [], {"set": 1}))
    assert calls == 1


def test_null_default_and_private_redaction_flag_are_five_fields_only():
    rows = _show(_family(lambda: [
        SettingRow("fixed", "ready", None, False, "manual#fixed"),
        SettingRow("secret", "now", "fallback", True, "manual#secret", _sensitive=True),
    ]))["settings"]
    assert rows[0] == {"key": "fixed", "current": "ready", "default": None,
                       "configurable": False, "comment": "manual#fixed"}
    assert rows[1] == {"key": "secret", "current": "<redacted>",
                       "default": "<redacted>", "configurable": True,
                       "comment": "manual#secret"}
    assert "now" not in repr(rows) and "fallback" not in repr(rows)

def _raises():
    raise RuntimeError("private provider exception or unavailable current")


@pytest.mark.parametrize("provider", [
    _raises,
    lambda: [object()],
    lambda: [SettingRow("", 1, None, False, "manual#x")],
    lambda: [SettingRow("x", 1, None, 1, "manual#x")],
    lambda: [SettingRow("x", {"not": {"json"}}, None, False, "manual#x")],
    lambda: [SettingRow("x", float("nan"), None, False, "manual#x")],
    lambda: [SettingRow("x", {"nested": [float("inf")]}, None, False, "manual#x")],
    lambda: [SettingRow("ok", 1, None, False, "manual#ok"), object()],
])
def test_provider_row_and_serialization_defects_are_one_fixed_failure(provider):
    assert _show(_family(provider)) == {"status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable"}


def test_complete_response_bound_stops_without_partial_rows():
    consumed = 0
    def provider():
        nonlocal consumed
        for index in range(10):
            consumed += 1
            yield SettingRow(str(index), "x" * 40_000, None, False, "manual#large")
    result = _show(_family(provider))
    assert consumed == 2 and "settings" not in result
    assert (result["error_code"], result["max_bytes"]) == (
        "SETTINGS_RESPONSE_TOO_LARGE", 65_536)


def test_public_export_and_exact_production_opt_ins():
    assert public_family.SettingRow is settings_module.SettingRow is SettingRow
    assert public_family.SettingsProvider is settings_module.SettingsProvider
    assert not hasattr(importlib.import_module("lingtai.kernel.tool_plugin"),
                       "ToolSettingsContract")
    curated = {}
    for name in ("telegram", "imap", "feishu", "wechat", "whatsapp", "cloud_mail"):
        module = importlib.import_module(f"lingtai.mcp_servers.{name}.plugin")
        curated[name] = (
            getattr(module, f"{name.upper()}_PLUGIN"),
            getattr(module, f"{name.upper()}_ACTIONS"),
        )
    expected_curated = {"cloud_mail", "feishu", "imap", "telegram", "wechat", "whatsapp"}
    assert {
        name for name, (plugin, _actions) in curated.items() if plugin.settings
    } == expected_curated
    assert all(
        ("settings" in actions) is (name in expected_curated)
        for name, (_plugin, actions) in curated.items()
    )
    modules = {"shell": "bash._tool_family", "web": "web_search"}
    declarations = {
        name: importlib.import_module(f"lingtai.tools.{modules.get(name, name)}").DECLARATION
        for name in OFFICIAL_TOOL_PLUGIN_NAMES
    }
    expected_official = {"avatar", "context", "daemon", "email", "file", "mcp", "notification", "plugin", "shell", "soul", "system", "task_card", "vision", "web"}
    assert {name for name, item in declarations.items() if item.settings} == expected_official
    assert expected_curated | expected_official == {
        "avatar", "cloud_mail", "context", "daemon", "email", "feishu", "file", "imap", "mcp", "notification", "plugin", "shell", "soul", "system", "task_card", "telegram", "vision", "web", "wechat", "whatsapp"
    }
    for name, item in declarations.items():
        assert ("settings" in item.public_actions) is (name in expected_official)
    psyche_actions = importlib.import_module(
        "lingtai.tools.psyche").get_schema()["properties"]["action"]["enum"]
    assert ("settings" in psyche_actions) is True


def test_parent_contract_states_declaration_provider_opt_in_not_owner_file():
    repo = Path(__file__).parents[1]
    manual = (
        repo
        / "src/lingtai/intrinsic_skills/system-manual/reference/tool-plugin-settings/SKILL.md"
    ).read_text(encoding="utf-8")
    contract = (repo / "src/lingtai/tools/CONTRACT.md").read_text(encoding="utf-8")

    assert "Production families opt\nin only through their own reviewed vertical" in manual
    assert "declaration opt-in\n   plus this bound provider" in manual
    assert "may be absent" in manual
    assert "ToolPluginDeclaration(settings=True)" in contract
    assert "v1 is exactly the cache-miss-budget source" in contract
    assert "v2 may carry the seven ordinary runtime-policy fields" in contract
    assert "A v1 document is exactly the cache-miss-budget source" in manual
    assert "A v2 document may carry any subset" in manual
    assert "Its absence leaves SHOW available" in contract
