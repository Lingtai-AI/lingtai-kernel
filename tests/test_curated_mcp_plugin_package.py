"""Curated-MCP plugin packaging invariants, proven on the Telegram and WeChat slices.

A curated MCP is a plugin-style package: the same folder ships the server, the
bundled ``SKILL.md``, and the stdio MCP declaration the curated catalog
publishes. ``lingtai.mcp_servers._plugin.CuratedMcpPlugin`` binds those three
and owns reserved action composition: optional ``settings`` immediately before
the ``manual`` action sourced from the packaged skill.

These tests pin the packaging promise, Telegram's explicit settings opt-in, and
the existing WeChat public surface around it. They make no network call and
stand up no account: the manual is account-independent and each family's
dispatch boundary rejects every invalid envelope before any manager I/O.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers import _plugin
from lingtai.mcp_servers._plugin import CuratedMcpPlugin, CuratedMcpPluginError
from lingtai.mcp_servers.telegram import _family, server as telegram_server
from lingtai.mcp_servers.telegram import manager as telegram_mgr
from lingtai.mcp_servers.telegram.plugin import (
    TELEGRAM_ACTIONS,
    TELEGRAM_DECLARED_ACTIONS,
    TELEGRAM_PLUGIN,
)
from lingtai.mcp_servers.wechat import _family as wechat_family, server as wechat_server
from lingtai.mcp_servers.wechat import manager as wechat_mgr
from lingtai.mcp_servers.wechat.plugin import (
    WECHAT_ACTIONS,
    WECHAT_DECLARED_ACTIONS,
    WECHAT_PLUGIN,
)
from lingtai.services import mcp_registry
from lingtai.tools.tool_family import ChildTool
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]


class _RecordingManager:
    """Stands in for TelegramManager/WechatManager; records every flat call."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


# ---------------------------------------------------------------------------
# The package ships its own MCP declaration
# ---------------------------------------------------------------------------

def test_telegram_package_declaration_matches_the_shipped_curated_catalog_entry():
    """The package owns its launcher; mcp_catalog.json publishes exactly it."""
    catalog = json.loads(
        (_REPO_ROOT / "src/lingtai/mcp_catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["telegram"] == TELEGRAM_PLUGIN.mcp_declaration()


def test_declaration_launches_the_declaring_package_and_validates_as_a_record():
    declaration = TELEGRAM_PLUGIN.mcp_declaration()
    assert declaration["transport"] == "stdio"
    assert declaration["command"] == _plugin.PYTHON_PLACEHOLDER
    assert declaration["args"] == ["-m", "lingtai.mcp_servers.telegram"]
    assert declaration["source"] == _plugin.CURATED_SOURCE
    # The host's registry validator — unchanged — still accepts the record.
    assert mcp_registry.validate_record(declaration) == (True, None)


def test_catalog_loading_is_unchanged_and_still_the_runtime_source():
    """The descriptor documents the record; it does not replace catalog I/O."""
    assert mcp_registry.load_catalog()["telegram"] == TELEGRAM_PLUGIN.mcp_declaration()


# ---------------------------------------------------------------------------
# `manual` is mandatory, reserved, and sourced from the packaged skill
# ---------------------------------------------------------------------------

def test_package_declares_no_reserved_action_and_plugin_appends_both_in_order():
    assert _plugin.MANUAL_ACTION not in TELEGRAM_DECLARED_ACTIONS
    assert _plugin.RESERVED_SETTINGS_NAME not in TELEGRAM_DECLARED_ACTIONS
    assert TELEGRAM_ACTIONS == (*TELEGRAM_DECLARED_ACTIONS, "settings", "manual")
    assert TELEGRAM_ACTIONS[-1] == "manual"


@pytest.mark.parametrize(
    "compose",
    [
        pytest.param(lambda: TELEGRAM_PLUGIN.actions(["send", "manual"]), id="actions"),
        pytest.param(
            lambda: TELEGRAM_PLUGIN.action_input_schemas({"send": {}, "manual": {}}),
            id="schemas",
        ),
        pytest.param(
            lambda: TELEGRAM_PLUGIN.build_family(
                [
                    ChildTool("send", {"type": "object"}, lambda _i: {}),
                    ChildTool("manual", {"type": "object"}, lambda _i: {"hijacked": True}),
                ]
            ),
            id="family",
        ),
    ],
)
def test_a_package_cannot_declare_re_schema_or_rebind_the_reserved_manual(compose):
    with pytest.raises(CuratedMcpPluginError, match="reserved 'manual'"):
        compose()


def test_composed_family_always_carries_a_manual_child_with_a_strict_empty_input():
    family = _family.build_telegram_family(None)
    assert family.has_manual()
    assert family.child_names == TELEGRAM_ACTIONS
    assert _family._telegram_input_schemas()["manual"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_manual_answers_from_the_packaged_skill_without_entering_the_manager():
    manager = _RecordingManager()
    result = _family.handle_telegram(
        manager, {"action": "manual", "input": {}, "reasoning": "read the manual"}
    )
    assert manager.calls == []
    assert result == TELEGRAM_PLUGIN.manual_payload()
    assert result["status"] == "ok"
    assert result["action"] == "manual"
    assert result["skill"] == "telegram-mcp-manual"
    skill_path = Path(result["path"])
    assert skill_path.is_absolute() and skill_path.name == "SKILL.md"
    assert result["manual"] == TELEGRAM_PLUGIN.skill_body


def test_manual_payload_is_the_same_document_the_legacy_manager_action_returns():
    """Routing manual through the plugin preserves the existing public result."""
    bare = object.__new__(telegram_mgr.TelegramManager)
    assert bare._manual() == TELEGRAM_PLUGIN.manual_payload()


def test_manual_still_requires_root_reasoning_like_every_other_action():
    manager = _RecordingManager()
    rejected = _family.handle_telegram(manager, {"action": "manual", "input": {}})
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert rejected["message"] == "reasoning is required"
    # ... and its input stays strictly empty.
    assert _family.handle_telegram(
        manager, {"action": "manual", "input": {"topic": "send"}, "reasoning": "x"}
    )["status"] == "failed"
    assert manager.calls == []


# ---------------------------------------------------------------------------
# The public envelope shape is unchanged by the packaging
# ---------------------------------------------------------------------------

def test_public_schema_keeps_the_strict_action_family_shape():
    schema = _family.TELEGRAM_SCHEMA
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(TELEGRAM_ACTIONS)
    assert "telegram-mcp-manual" in schema["properties"]["action"]["description"]
    # One root ``oneOf`` branch per action in plugin order (settings
    # immediately before manual), each discriminated by its action const.
    assert_compact_envelope(schema, list(TELEGRAM_ACTIONS))
    assert branch_actions(schema) == list(TELEGRAM_ACTIONS)
    branches = action_input_schemas(schema)
    canonical = _family._telegram_input_schemas()
    for action in TELEGRAM_DECLARED_ACTIONS:
        assert branches[action] == canonical[action], action
    assert branches["manual"] == canonical["manual"]
    assert branches["settings"] == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_declared_actions_still_dispatch_flat_into_the_manager():
    manager = _RecordingManager()
    result = _family.handle_telegram(
        manager,
        {"action": "send", "input": {"chat_id": 7, "text": "hi"}, "reasoning": "probe"},
    )
    assert result["status"] == "ok"
    assert manager.calls == [{"action": "send", "chat_id": 7, "text": "hi"}]


# ---------------------------------------------------------------------------
# The server advertises the packaged identity, not a hand-copied one
# ---------------------------------------------------------------------------

def test_server_profile_manifest_is_sourced_from_the_plugin_descriptor(monkeypatch):
    monkeypatch.delenv("LINGTAI_TELEGRAM_CONFIG", raising=False)
    manifest = telegram_server._profile_manifest(None)
    assert manifest["server"]["name"] == TELEGRAM_PLUGIN.server_name == "lingtai-telegram"
    assert manifest["server"]["registry_name"] == TELEGRAM_PLUGIN.name == "telegram"
    assert manifest["server"]["homepage"] == TELEGRAM_PLUGIN.homepage
    assert manifest["tools"] == [
        {
            "name": "telegram",
            "description": "Strict Telegram LTP-v2 family.",
            "actions": list(TELEGRAM_ACTIONS),
        }
    ]


# ---------------------------------------------------------------------------
# WeChat parity slice — the same packaging invariants, proven independently
# ---------------------------------------------------------------------------

def test_wechat_package_declaration_matches_the_shipped_curated_catalog_entry():
    """The package owns its launcher; mcp_catalog.json publishes exactly it."""
    catalog = json.loads(
        (_REPO_ROOT / "src/lingtai/mcp_catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["wechat"] == WECHAT_PLUGIN.mcp_declaration()


def test_wechat_declaration_launches_the_declaring_package_and_validates_as_a_record():
    declaration = WECHAT_PLUGIN.mcp_declaration()
    assert declaration["transport"] == "stdio"
    assert declaration["command"] == _plugin.PYTHON_PLACEHOLDER
    assert declaration["args"] == ["-m", "lingtai.mcp_servers.wechat"]
    assert declaration["source"] == _plugin.CURATED_SOURCE
    assert mcp_registry.validate_record(declaration) == (True, None)


def test_wechat_package_declares_neither_reserved_child_and_plugin_orders_them():
    assert _plugin.MANUAL_ACTION not in WECHAT_DECLARED_ACTIONS
    assert "settings" not in WECHAT_DECLARED_ACTIONS
    assert WECHAT_ACTIONS == (*WECHAT_DECLARED_ACTIONS, "settings", "manual")
    assert WECHAT_ACTIONS[-2] == "settings"
    assert WECHAT_ACTIONS[-1] == "manual"


def test_wechat_composed_family_always_carries_a_manual_child_with_a_strict_empty_input():
    family = wechat_family.build_wechat_family(None)
    assert family.has_manual()
    assert family.child_names == WECHAT_ACTIONS
    assert wechat_family._wechat_input_schemas()["settings"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert wechat_family._wechat_input_schemas()["manual"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_wechat_manual_answers_from_the_packaged_skill_without_entering_the_manager():
    manager = _RecordingManager()
    result = wechat_family.handle_wechat(
        manager, {"action": "manual", "input": {}, "reasoning": "read the manual"}
    )
    assert manager.calls == []
    assert result == WECHAT_PLUGIN.manual_payload()
    assert result["status"] == "ok"
    assert result["action"] == "manual"
    assert result["skill"] == "wechat-mcp-manual"
    skill_path = Path(result["path"])
    assert skill_path.is_absolute() and skill_path.name == "SKILL.md"
    assert result["manual"] == WECHAT_PLUGIN.skill_body


def test_wechat_manual_payload_is_the_same_document_the_legacy_manager_action_returns():
    """Routing manual through the plugin preserves the existing public result."""
    bare = object.__new__(wechat_mgr.WechatManager)
    assert bare._handle_manual() == WECHAT_PLUGIN.manual_payload()


def test_wechat_manual_still_requires_root_reasoning_like_every_other_action():
    manager = _RecordingManager()
    rejected = wechat_family.handle_wechat(manager, {"action": "manual", "input": {}})
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert rejected["message"] == "reasoning is required"
    assert wechat_family.handle_wechat(
        manager, {"action": "manual", "input": {"topic": "send"}, "reasoning": "x"}
    )["status"] == "failed"
    assert manager.calls == []


def test_wechat_public_schema_keeps_the_strict_action_family_shape():
    schema = wechat_family.WECHAT_SCHEMA
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(WECHAT_ACTIONS)
    assert "wechat-mcp-manual" in schema["properties"]["action"]["description"]
    assert_compact_envelope(schema, list(WECHAT_ACTIONS))
    assert branch_actions(schema) == list(WECHAT_ACTIONS)
    branches = action_input_schemas(schema)
    canonical = wechat_family._wechat_input_schemas()
    for action in WECHAT_DECLARED_ACTIONS:
        assert branches[action] == canonical[action], action
    assert branches["manual"] == canonical["manual"]
    assert branches["settings"] == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_wechat_declared_actions_still_dispatch_flat_into_the_manager():
    manager = _RecordingManager()
    result = wechat_family.handle_wechat(
        manager,
        {"action": "accounts", "input": {}, "reasoning": "probe"},
    )
    assert result["status"] == "ok"
    assert manager.calls == [{"action": "accounts"}]


def test_wechat_server_profile_manifest_is_sourced_from_the_plugin_descriptor(monkeypatch):
    monkeypatch.delenv("LINGTAI_WECHAT_CONFIG", raising=False)
    manifest = wechat_server._profile_manifest(None)
    assert manifest["server"]["name"] == WECHAT_PLUGIN.server_name == "lingtai-wechat"
    assert manifest["server"]["registry_name"] == WECHAT_PLUGIN.name == "wechat"
    assert manifest["server"]["homepage"] == WECHAT_PLUGIN.homepage
    assert manifest["tools"] == [
        {
            "name": "wechat",
            "description": "Strict WeChat LTP-v2 family.",
            "actions": list(WECHAT_ACTIONS),
        }
    ]


# ---------------------------------------------------------------------------
# Descriptor defects fail loudly at import time
# ---------------------------------------------------------------------------

def test_descriptor_rejects_a_package_that_is_not_its_own_module():
    with pytest.raises(CuratedMcpPluginError, match="must be the 'telegram' module"):
        CuratedMcpPlugin(
            name="telegram",
            package="lingtai.mcp_servers.feishu",
            server_name="lingtai-telegram",
            summary="s",
            homepage="h",
            skill_name="telegram-mcp-manual",
        )


def test_descriptor_rejects_a_manual_that_is_not_the_packaged_skill():
    with pytest.raises(CuratedMcpPluginError, match="declares name"):
        CuratedMcpPlugin(
            name="telegram",
            package="lingtai.mcp_servers.telegram",
            server_name="lingtai-telegram",
            summary="s",
            homepage="h",
            skill_name="somebody-elses-manual",
        )


@pytest.mark.parametrize("blank_field", ["name", "package", "server_name", "summary", "homepage", "skill_name"])
def test_descriptor_rejects_blank_identity_fields(blank_field):
    fields = {
        "name": "telegram",
        "package": "lingtai.mcp_servers.telegram",
        "server_name": "lingtai-telegram",
        "summary": "s",
        "homepage": "h",
        "skill_name": "telegram-mcp-manual",
    }
    fields[blank_field] = "  "
    with pytest.raises(CuratedMcpPluginError, match="non-empty string"):
        CuratedMcpPlugin(**fields)
