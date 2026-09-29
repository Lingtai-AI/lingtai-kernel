"""Curated-MCP plugin packaging invariants for the Feishu vertical slice.

Mirrors ``tests/test_curated_mcp_plugin_package.py`` (the Telegram reference)
for the Feishu MCP. ``lingtai.mcp_servers._plugin.CuratedMcpPlugin`` binds one
package's registry name, bundled ``SKILL.md``, and stdio MCP declaration
together and owns the reserved ``settings``/``manual`` composition promises.
Feishu opts into bounded SHOW; the plugin inserts it immediately before the
manual sourced from the packaged skill.

These tests pin the packaging promise and the *unchanged* public Feishu
surface around it. They make no network call and stand up no account: the
manual is account-independent and the family's dispatch boundary rejects
every invalid envelope before any manager I/O.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers import _plugin
from lingtai.mcp_servers._plugin import CuratedMcpPlugin, CuratedMcpPluginError
from lingtai.mcp_servers.feishu import _family, server as feishu_server
from lingtai.mcp_servers.feishu import manager as feishu_mgr
from lingtai.mcp_servers.feishu.plugin import (
    FEISHU_ACTIONS,
    FEISHU_DECLARED_ACTIONS,
    FEISHU_PLUGIN,
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
    """Stands in for FeishuManager; records every flat action it is handed."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


# ---------------------------------------------------------------------------
# The package ships its own MCP declaration
# ---------------------------------------------------------------------------

def test_feishu_package_declaration_matches_the_shipped_curated_catalog_entry():
    """The package owns its launcher; mcp_catalog.json publishes exactly it."""
    catalog = json.loads(
        (_REPO_ROOT / "src/lingtai/mcp_catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["feishu"] == FEISHU_PLUGIN.mcp_declaration()


def test_declaration_launches_the_declaring_package_and_validates_as_a_record():
    declaration = FEISHU_PLUGIN.mcp_declaration()
    assert declaration["transport"] == "stdio"
    assert declaration["command"] == _plugin.PYTHON_PLACEHOLDER
    assert declaration["args"] == ["-m", "lingtai.mcp_servers.feishu"]
    assert declaration["source"] == _plugin.CURATED_SOURCE
    # The host's registry validator — unchanged — still accepts the record.
    assert mcp_registry.validate_record(declaration) == (True, None)


def test_catalog_loading_is_unchanged_and_still_the_runtime_source():
    """The descriptor documents the record; it does not replace catalog I/O."""
    assert mcp_registry.load_catalog()["feishu"] == FEISHU_PLUGIN.mcp_declaration()


# ---------------------------------------------------------------------------
# `settings` and `manual` are reserved and composed by the plugin
# ---------------------------------------------------------------------------

def test_package_declares_neither_reserved_action_and_plugin_orders_them_last():
    assert _plugin.RESERVED_SETTINGS_NAME not in FEISHU_DECLARED_ACTIONS
    assert _plugin.MANUAL_ACTION not in FEISHU_DECLARED_ACTIONS
    assert FEISHU_ACTIONS == (*FEISHU_DECLARED_ACTIONS, "settings", "manual")
    assert FEISHU_ACTIONS[-2:] == ("settings", "manual")


@pytest.mark.parametrize(
    "compose",
    [
        pytest.param(lambda: FEISHU_PLUGIN.actions(["send", "manual"]), id="actions"),
        pytest.param(
            lambda: FEISHU_PLUGIN.actions(["send", "settings"]),
            id="settings-actions",
        ),
        pytest.param(
            lambda: FEISHU_PLUGIN.action_input_schemas({"send": {}, "manual": {}}),
            id="schemas",
        ),
        pytest.param(
            lambda: FEISHU_PLUGIN.build_family(
                [
                    ChildTool("send", {"type": "object"}, lambda _i: {}),
                    ChildTool("manual", {"type": "object"}, lambda _i: {"hijacked": True}),
                ]
            ),
            id="family",
        ),
    ],
)
def test_a_package_cannot_declare_re_schema_or_rebind_a_reserved_action(compose):
    with pytest.raises(CuratedMcpPluginError, match="reserved '(manual|settings)'"):
        compose()


def test_composed_family_carries_strict_settings_before_manual():
    family = _family.build_feishu_family(None)
    assert family.has_manual()
    assert family.child_names == FEISHU_ACTIONS
    assert _family._feishu_input_schemas()["manual"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert _family._feishu_input_schemas()["settings"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def test_manual_answers_from_the_packaged_skill_without_entering_the_manager():
    manager = _RecordingManager()
    result = _family.handle_feishu(
        manager, {"action": "manual", "input": {}, "reasoning": "read the manual"}
    )
    assert manager.calls == []
    assert result == FEISHU_PLUGIN.manual_payload()
    assert result["status"] == "ok"
    assert result["action"] == "manual"
    assert result["skill"] == "feishu-mcp-manual"
    skill_path = Path(result["path"])
    assert skill_path.is_absolute() and skill_path.name == "SKILL.md"
    assert result["manual"] == FEISHU_PLUGIN.skill_body


def test_manual_payload_is_the_same_document_the_legacy_manager_action_returns():
    """Routing manual through the plugin preserves the existing public result."""
    bare = object.__new__(feishu_mgr.FeishuManager)
    assert bare._manual() == FEISHU_PLUGIN.manual_payload()


def test_manual_still_requires_root_reasoning_like_every_other_action():
    manager = _RecordingManager()
    rejected = _family.handle_feishu(manager, {"action": "manual", "input": {}})
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert rejected["message"] == "reasoning is required"
    # ... and its input stays strictly empty.
    assert _family.handle_feishu(
        manager, {"action": "manual", "input": {"topic": "send"}, "reasoning": "x"}
    )["status"] == "failed"
    assert manager.calls == []


def test_manual_answers_even_when_no_manager_is_initialized():
    """Unlike every other action, ``manual`` bypasses the NOT_CONNECTED gate."""
    result = _family.handle_feishu(
        None, {"action": "manual", "input": {}, "reasoning": "no manager yet"}
    )
    assert result == FEISHU_PLUGIN.manual_payload()


# ---------------------------------------------------------------------------
# The public envelope shape is unchanged by the packaging
# ---------------------------------------------------------------------------

def test_public_schema_keeps_the_strict_action_family_shape():
    schema = _family.FEISHU_SCHEMA
    assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["action"]["enum"] == list(FEISHU_ACTIONS)
    assert "feishu-mcp-manual" in schema["properties"]["action"]["description"]
    # One root ``oneOf`` branch per action: declared actions, then the
    # plugin-inserted ``settings``, then ``manual`` — discriminated by const.
    assert_compact_envelope(schema, list(FEISHU_ACTIONS))
    assert branch_actions(schema) == [*FEISHU_DECLARED_ACTIONS, "settings", "manual"]
    branches = action_input_schemas(schema)
    canonical = _family._feishu_input_schemas()
    for action in FEISHU_DECLARED_ACTIONS:
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
    result = _family.handle_feishu(
        manager,
        {"action": "accounts", "input": {}, "reasoning": "probe"},
    )
    assert result["status"] == "ok"
    assert manager.calls == [{"action": "accounts"}]


# ---------------------------------------------------------------------------
# The server advertises the packaged identity, not a hand-copied one
# ---------------------------------------------------------------------------

def test_server_profile_manifest_is_sourced_from_the_plugin_descriptor(monkeypatch):
    monkeypatch.delenv("LINGTAI_FEISHU_CONFIG", raising=False)
    manifest = feishu_server._profile_manifest(None)
    assert manifest["server"]["name"] == FEISHU_PLUGIN.server_name == "lingtai-feishu"
    assert manifest["server"]["registry_name"] == FEISHU_PLUGIN.name == "feishu"
    assert manifest["server"]["homepage"] == FEISHU_PLUGIN.homepage
    assert manifest["tools"][0]["name"] == "feishu"
    assert manifest["tools"][0]["actions"] == list(FEISHU_ACTIONS)


# ---------------------------------------------------------------------------
# Descriptor defects fail loudly at import time
# ---------------------------------------------------------------------------

def test_descriptor_rejects_a_package_that_is_not_its_own_module():
    with pytest.raises(CuratedMcpPluginError, match="must be the 'feishu' module"):
        CuratedMcpPlugin(
            name="feishu",
            package="lingtai.mcp_servers.telegram",
            server_name="lingtai-feishu",
            summary="s",
            homepage="h",
            skill_name="feishu-mcp-manual",
        )


def test_descriptor_rejects_a_manual_that_is_not_the_packaged_skill():
    with pytest.raises(CuratedMcpPluginError, match="declares name"):
        CuratedMcpPlugin(
            name="feishu",
            package="lingtai.mcp_servers.feishu",
            server_name="lingtai-feishu",
            summary="s",
            homepage="h",
            skill_name="somebody-elses-manual",
        )


@pytest.mark.parametrize("blank_field", ["name", "package", "server_name", "summary", "homepage", "skill_name"])
def test_descriptor_rejects_blank_identity_fields(blank_field):
    fields = {
        "name": "feishu",
        "package": "lingtai.mcp_servers.feishu",
        "server_name": "lingtai-feishu",
        "summary": "s",
        "homepage": "h",
        "skill_name": "feishu-mcp-manual",
    }
    fields[blank_field] = "  "
    with pytest.raises(CuratedMcpPluginError, match="non-empty string"):
        CuratedMcpPlugin(**fields)
