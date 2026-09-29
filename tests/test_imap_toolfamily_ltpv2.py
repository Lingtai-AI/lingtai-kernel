"""Focused IMAP LTP-v2 family invariants (mirrors telegram's toolfamily test)."""
from __future__ import annotations

import copy

from lingtai.mcp_servers.imap._family import (
    IMAP_ACTIONS,
    IMAP_SCHEMA,
    _basic_validate,
    _imap_input_schemas,
    handle_imap,
)
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
        {"action": "accounts", "input": {"email_id": "a:b:1"}, "reasoning": "x"},
        {"action": "send", "input": {"note": "wrong branch key"}, "reasoning": "x"},
        {"action": "send", "input": {}, "reasoning": "x"},
        {"action": "move", "input": {"email_id": "a:b:1"}, "reasoning": "x"},
        {"action": "flag", "input": {"email_id": "a:b:1"}, "reasoning": "x"},
        {"action": "add_contact", "input": {"address": "a@b.com"}, "reasoning": "x"},
    ]
    for args in invalid:
        result = handle_imap(manager, args)
        assert result["status"] == "failed", args
        assert manager.calls == []
    assert handle_imap(manager, valid)["status"] == "ok"
    assert len(manager.calls) == 1
    assert manager.calls[0] == {"action": "accounts"}


def test_family_dispatch_forwards_flat_input_to_manager_handle():
    manager = _CountingManager()
    handle_imap(manager, {
        "action": "send",
        "input": {"address": "a@b.com", "message": "hi"},
        "reasoning": "x",
    })
    handle_imap(manager, {
        "action": "move",
        "input": {"email_id": "a:b:1", "folder": "Archive"},
        "reasoning": "x",
    })
    handle_imap(manager, {
        "action": "flag",
        "input": {"email_id": "a:b:1", "flags": {"seen": True}},
        "reasoning": "x",
    })
    assert manager.calls == [
        {"action": "send", "address": "a@b.com", "message": "hi"},
        {"action": "move", "email_id": "a:b:1", "folder": "Archive"},
        {"action": "flag", "email_id": "a:b:1", "flags": {"seen": True}},
    ]


def test_manual_action_bypasses_manager_when_manager_is_none():
    result = handle_imap(None, {"action": "manual", "input": {}, "reasoning": "x"})
    assert result["status"] == "ok"
    assert result["skill"] == "imap-mcp-manual"
    assert isinstance(result["manual"], str) and result["manual"].strip()


def test_imap_actions_match_exact_public_inventory():
    assert IMAP_ACTIONS == (
        "send", "check", "read", "reply", "search",
        "delete", "move", "flag", "folders",
        "contacts", "add_contact", "remove_contact", "edit_contact",
        "accounts", "settings", "manual",
    )
    assert list(IMAP_SCHEMA["properties"]["action"]["enum"]) == list(IMAP_ACTIONS)


def test_imap_send_schema_requires_address_and_rejects_unknown_fields():
    send = _branches(IMAP_SCHEMA)["send"]
    assert send["required"] == ["address"]
    assert _basic_validate({"address": "a@b.com", "message": "hi"}, send)
    assert not _basic_validate({"message": "missing address"}, send)
    assert not _basic_validate({"address": "a@b.com", "bogus": 1}, send)


def test_imap_move_schema_requires_email_id_and_destination_folder():
    move = _branches(IMAP_SCHEMA)["move"]
    assert set(move["required"]) == {"email_id", "folder"}
    assert _basic_validate({"email_id": "a:b:1", "folder": "Archive"}, move)
    assert not _basic_validate({"email_id": "a:b:1"}, move)


def test_imap_flag_schema_requires_email_id_and_flags():
    flag = _branches(IMAP_SCHEMA)["flag"]
    assert set(flag["required"]) == {"email_id", "flags"}
    assert _basic_validate({"email_id": "a:b:1", "flags": {"seen": True}}, flag)
    assert not _basic_validate({"email_id": "a:b:1"}, flag)


def test_imap_root_envelope_is_strict_ltp_v2():
    assert IMAP_SCHEMA["required"] == ["action", "input", "reasoning"]
    assert IMAP_SCHEMA["additionalProperties"] is False
    assert "reasoning" in IMAP_SCHEMA["properties"]
    assert "summarize" in IMAP_SCHEMA["properties"]
    # One root ``oneOf`` branch per action, discriminated by action const
    # and carrying that action's own canonical input; no root allOf/anyOf
    # and no duplicate branch list under ``input``.
    assert_compact_envelope(IMAP_SCHEMA, list(IMAP_ACTIONS))
    branches = _branches(IMAP_SCHEMA)
    canonical = _imap_input_schemas()
    for action in IMAP_ACTIONS:
        if action == "settings":
            assert branches[action] == {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
            continue
        assert branches[action] == canonical[action], action


def test_openai_responses_scrub_preserves_family_root_and_action_branches():
    """The root ``oneOf`` discriminated union survives the Responses scrub
    as ``oneOf`` (only nested ``oneOf`` is rewritten), with the identical
    per-action ``input`` correlation as the canonical schema."""
    from lingtai.llm.openai.adapter import _scrub_responses_schema

    wire = _scrub_responses_schema(copy.deepcopy(IMAP_SCHEMA), is_root=True)
    assert wire["required"] == IMAP_SCHEMA["required"]
    assert wire["properties"]["action"]["enum"] == list(IMAP_ACTIONS)
    assert wire["additionalProperties"] is False
    assert "allOf" not in wire and "anyOf" not in wire
    assert branch_actions(wire) == list(IMAP_ACTIONS)
    # The typed root ``input`` only gains an empty ``properties`` map on the
    # Responses wire; it never regains a duplicate branch list.
    root_input = wire["properties"]["input"]
    assert root_input["properties"] == {}
    assert "oneOf" not in root_input and "anyOf" not in root_input
    # Identical per-action correlation on the wire: same order, same input
    # fields and required lists, every branch still closed. IMAP's child
    # inputs use nullable ``anyOf`` wrappers only — no nested ``oneOf`` to
    # rewrite anywhere.
    wire_branches = action_input_schemas(wire)
    canonical_branches = action_input_schemas(IMAP_SCHEMA)
    assert list(wire_branches) == list(canonical_branches) == list(IMAP_ACTIONS)
    assert not {a for a, b in canonical_branches.items() if "oneOf" in b}
    for action in IMAP_ACTIONS:
        assert set(wire_branches[action]["properties"]) == set(
            canonical_branches[action]["properties"]
        ), action
        assert wire_branches[action].get("required", []) == canonical_branches[action].get(
            "required", []
        ), action
        assert wire_branches[action]["additionalProperties"] is False, action
        assert "oneOf" not in wire_branches[action], action
