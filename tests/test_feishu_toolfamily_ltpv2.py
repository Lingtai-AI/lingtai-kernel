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


class _CountingManager:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def handle(self, args: dict) -> dict:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args.get("action")}


def _branches(schema: dict) -> dict[str, dict]:
    inputs = schema["properties"]["input"]
    branches = inputs.get("oneOf") or inputs.get("anyOf")
    return {branch["title"].removesuffix(" input"): branch for branch in branches}


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


def test_family_manual_action_returns_manager_manual_result_verbatim():
    manager = _CountingManager()
    manager.handle = lambda args: {"status": "ok", "action": "manual", "manual": "body"}
    result = handle_feishu(manager, {"action": "manual", "input": {}, "reasoning": "x"})
    assert result == {"status": "ok", "action": "manual", "manual": "body"}


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
    from lingtai.llm.openai.adapter import _scrub_responses_schema

    wire = _scrub_responses_schema(copy.deepcopy(FEISHU_SCHEMA), is_root=True)
    assert wire["required"] == FEISHU_SCHEMA["required"]
    assert wire["properties"]["action"]["enum"] == list(FEISHU_ACTIONS)
    assert wire["properties"]["input"]["anyOf"]
    assert len(wire["allOf"]) == len(FEISHU_ACTIONS)
    assert wire["additionalProperties"] is False


def test_published_input_schema_uses_anyof_and_empty_actions_are_unambiguous():
    """A conformant JSON-Schema oneOf rejects {} whenever more than one
    branch matches it. check/contacts/accounts/manual all have an empty (or
    all-optional) input branch, so the published discovery schema must use
    anyOf rather than oneOf, matching Telegram's ``_family.py``. Strict
    action<->input correlation still lives in the allOf discriminator and in
    dispatch (``_basic_validate``/``handle_feishu``), not in this branch
    list's own combinator choice.
    """
    input_schema = FEISHU_SCHEMA["properties"]["input"]
    assert "oneOf" not in input_schema
    assert "anyOf" in input_schema

    branches = input_schema["anyOf"]
    empty_input_actions = {"check", "contacts", "accounts", "manual"}
    matching_titles = {
        branch["title"].removesuffix(" input")
        for branch in branches
        if _basic_validate({}, branch)
    }
    assert matching_titles == empty_input_actions
