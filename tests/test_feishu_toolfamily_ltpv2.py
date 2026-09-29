"""Focused Feishu LTP-v2 family tests.

Mirrors ``tests/test_telegram_toolfamily_ltpv2.py``'s coverage of the strict
envelope, cross-branch rejection before manager I/O, and wire-schema parity,
scoped to Feishu's single public family (no Task Card sibling).
"""
from __future__ import annotations

import copy

from lingtai.mcp_servers.feishu._family import (
    FEISHU_ACTIONS,
    FEISHU_SCHEMA,
    _basic_validate,
    handle_feishu,
)
from lingtai.mcp_servers.feishu.manager import FeishuManager
from tests._tool_family_schema_helpers import (
    action_input_schemas,
    assert_compact_envelope,
    branch_actions,
)


class _CountingManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


def _branches(schema: dict) -> dict[str, dict]:
    """Map each action to the ``input`` schema its root ``oneOf`` branch carries."""
    return action_input_schemas(schema)


def test_family_dispatch_rejects_root_and_cross_branch_before_manager_io():
    manager = _CountingManager()
    valid = {
        "action": "accounts",
        "input": {},
        "reasoning": "schema probe",
    }
    invalid = [
        {"action": "accounts", "input": {}, "reasoning": "x", "_reasoning": "legacy"},
        {"action": "accounts", "input": {}, "reasoning": "x", "unknown": 1},
        {"action": "accounts", "input": {}, "reasoning": 7},
        {"action": "accounts", "input": {"receive_id": "ou_x"}, "reasoning": "x"},
        {"action": "send", "input": {"message_id": "a:b:c", "text": "x"}, "reasoning": "x"},
        {"action": "send", "input": {"text": "missing receive_id"}, "reasoning": "x"},
        {"action": "send", "input": {"receive_id": "ou_x", "text": 17}, "reasoning": "x"},
        {"action": "not_a_real_action", "input": {}, "reasoning": "x"},
        {"action": "send", "input": {}, "reasoning": "x", "summarize": "yes"},
    ]
    for args in invalid:
        result = handle_feishu(manager, args)
        assert result["status"] == "failed", args
        assert manager.calls == []
    assert handle_feishu(manager, valid)["status"] == "ok"
    assert len(manager.calls) == 1
    assert manager.calls[0] == {"action": "accounts"}


def test_feishu_outbound_schema_requires_text_xor_strict_content():
    send = _branches(FEISHU_SCHEMA)["send"]
    assert send["required"] == ["receive_id"]
    assert _basic_validate({"receive_id": "ou_1", "text": "hello"}, send)
    assert _basic_validate(
        {"receive_id": "ou_1", "text": "hello", "receive_id_type": "chat_id"}, send
    )
    for content in (
        {"type": "text", "text": "hello"},
        {"type": "markdown", "markdown": "**hello**"},
        {"type": "post", "post": {"zh_cn": {"title": "hello"}}},
    ):
        assert _basic_validate({"receive_id": "ou_1", "content": content}, send)
    assert not _basic_validate({"receive_id": "ou_1"}, send)
    assert not _basic_validate({"text": "missing receive_id"}, send)
    assert not _basic_validate({"receive_id": "ou_1", "text": 17}, send)
    assert not _basic_validate(
        {
            "receive_id": "ou_1",
            "text": "ambiguous",
            "content": {"type": "text", "text": "also present"},
        },
        send,
    )
    assert not _basic_validate(
        {
            "receive_id": "ou_1",
            "content": {"type": "markdown", "text": "wrong field"},
        },
        send,
    )

    reply = _branches(FEISHU_SCHEMA)["reply"]
    assert _basic_validate(
        {
            "message_id": "main:oc_chat:om_1",
            "content": {"type": "markdown", "markdown": "reply"},
            "reply_in_thread": True,
        },
        reply,
    )
    assert not _basic_validate(
        {
            "message_id": "main:oc_chat:om_1",
            "content": {"type": "markdown", "markdown": "reply"},
            "reply_in_thread": "yes",
        },
        reply,
    )


def test_feishu_remove_contact_schema_accepts_exactly_one_of_alias_or_open_id():
    remove_contact = _branches(FEISHU_SCHEMA)["remove_contact"]
    assert _basic_validate({"alias": "friend"}, remove_contact)
    assert _basic_validate({"open_id": "ou_1"}, remove_contact)
    assert not _basic_validate({}, remove_contact)
    assert not _basic_validate({"alias": "friend", "open_id": "ou_1"}, remove_contact)


def test_family_manual_action_answers_from_the_packaged_skill_without_entering_the_manager():
    """``manual`` is reserved and plugin-owned (``CuratedMcpPlugin``): it never
    routes through the manager, even when one is present — see
    ``tests/test_feishu_curated_mcp_plugin_package.py`` for the full packaging
    coverage this mirrors from the Telegram reference slice."""
    manager = _CountingManager()
    manager.handle = lambda args: {"status": "ok", "action": "manual", "manual": "body"}
    result = handle_feishu(manager, {"action": "manual", "input": {}, "reasoning": "x"})
    assert manager.calls == []
    assert result["status"] == "ok"
    assert result["skill"] == "feishu-mcp-manual"
    assert result["manual"] != "body"


def test_family_manual_action_without_manager_uses_bundled_skill():
    result = handle_feishu(None, {"action": "manual", "input": {}, "reasoning": "x"})
    assert result["status"] == "ok"
    assert result["skill"] == "feishu-mcp-manual"
    assert isinstance(result["manual"], str) and result["manual"].strip()


def test_reasoning_and_summarize_never_reach_a_child_schema_or_handler():
    manager = _CountingManager()
    handle_feishu(
        manager,
        {
            "action": "check",
            "input": {},
            "reasoning": "should not leak",
            "summarize": True,
        },
    )
    assert manager.calls == [{"action": "check"}]
    for branch in _branches(FEISHU_SCHEMA).values():
        assert "reasoning" not in branch.get("properties", {})
        assert "summarize" not in branch.get("properties", {})


def test_manager_handle_accepts_both_flat_and_ltpv2_shapes_identically():
    class _FakeService:
        def list_accounts(self):
            return ["main"]

        def account_details(self):
            return {"main": {}}

        def identity_path(self):
            return "/tmp/identities.json"

    inst = object.__new__(FeishuManager)
    inst._service = _FakeService()

    flat = inst.handle({"action": "accounts"})
    ltpv2 = inst.handle({"action": "accounts", "input": {}, "reasoning": "probe"})
    assert flat == ltpv2 == {
        "status": "ok",
        "accounts": ["main"],
        "details": {"main": {}},
        "identity_path": "/tmp/identities.json",
    }


def test_openai_responses_scrub_preserves_family_root_and_action_branches():
    """The root ``oneOf`` discriminated union survives the Responses scrub
    as ``oneOf`` with the same per-action mapping; only a ``oneOf`` nested
    inside a child input (send/reply's text-XOR-content choice and the
    ``content`` variants) is rewritten to ``anyOf`` on that wire."""
    from lingtai.llm.openai.adapter import _scrub_responses_schema

    wire = _scrub_responses_schema(copy.deepcopy(FEISHU_SCHEMA), is_root=True)
    assert wire["required"] == FEISHU_SCHEMA["required"]
    assert wire["properties"]["action"]["enum"] == list(FEISHU_ACTIONS)
    assert wire["additionalProperties"] is False
    assert "allOf" not in wire and "anyOf" not in wire
    assert branch_actions(wire) == list(FEISHU_ACTIONS)
    # The typed root ``input`` only gains an empty ``properties`` map on the
    # Responses wire; it never regains a duplicate branch list.
    root_input = wire["properties"]["input"]
    assert root_input["properties"] == {}
    assert "oneOf" not in root_input and "anyOf" not in root_input
    # Identical per-action correlation on the wire: same order, same input
    # fields and required lists, every branch still closed, and no nested
    # ``oneOf`` left anywhere below the root.
    wire_branches = action_input_schemas(wire)
    canonical_branches = action_input_schemas(FEISHU_SCHEMA)
    assert list(wire_branches) == list(canonical_branches) == list(FEISHU_ACTIONS)
    for action in FEISHU_ACTIONS:
        assert set(wire_branches[action]["properties"]) == set(
            canonical_branches[action]["properties"]
        ), action
        assert wire_branches[action].get("required", []) == canonical_branches[action].get(
            "required", []
        ), action
        assert wire_branches[action]["additionalProperties"] is False, action
        assert "oneOf" not in wire_branches[action], action
    # Exactly these children carry a nested ``oneOf`` (text-XOR-content,
    # add-XOR-remove reaction fields, alias-XOR-open_id); each is rewritten
    # to ``anyOf`` on this wire while the root ``oneOf`` survives.
    nested_one_of = {a for a, b in canonical_branches.items() if "oneOf" in b}
    assert nested_one_of == {"send", "reply", "react", "edit", "remove_contact"}
    for action in sorted(nested_one_of):
        assert "anyOf" not in canonical_branches[action], action
        assert "anyOf" in wire_branches[action], action
        assert len(wire_branches[action]["anyOf"]) == len(
            canonical_branches[action]["oneOf"]
        ), action


def test_root_input_carries_no_discovery_list_and_empty_actions_are_unambiguous():
    """A conformant JSON-Schema oneOf over bare inputs rejects {} whenever
    more than one branch matches it. check/contacts/accounts/settings/manual
    all have an empty (or all-optional) input, so the old ``input.anyOf``
    discovery list existed only to dodge that collision. The compact shape
    has no discovery list at all: the single root ``oneOf`` is keyed by the
    ``action`` const, so a well-formed zero-input call matches exactly one
    branch — strict correlation lives there and in dispatch
    (``_basic_validate``/``handle_feishu``).
    """
    input_schema = FEISHU_SCHEMA["properties"]["input"]
    assert "oneOf" not in input_schema
    assert "anyOf" not in input_schema
    assert "properties" not in input_schema
    assert "anyOf" not in FEISHU_SCHEMA and "allOf" not in FEISHU_SCHEMA

    branches = _branches(FEISHU_SCHEMA)
    matching_actions = {
        action for action, branch in branches.items() if _basic_validate({}, branch)
    }
    assert matching_actions == {"check", "contacts", "accounts", "settings", "manual"}
    for action in matching_actions:
        matching = [
            branch for branch in FEISHU_SCHEMA["oneOf"]
            if branch["properties"]["action"]["const"] == action
            and _basic_validate({}, branch["properties"]["input"])
        ]
        assert len(matching) == 1, action
