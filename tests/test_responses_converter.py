"""Tests for OpenAI Responses API input conversion."""

from __future__ import annotations

import pytest

from lingtai.llm.interface_converters import (
    _responses_replay_fingerprint,
    to_responses_input,
)
from lingtai.llm.openai.adapter import CodexOpenAIAdapter
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ThinkingBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def test_responses_input_replays_thinking_block_as_reasoning_summary():
    iface = ChatInterface()
    iface.add_user_message("What should we do?")
    iface.add_assistant_message([
        ThinkingBlock(text="Need to inspect the inbox before answering."),
        TextBlock(text="I'll check first."),
        ToolCallBlock(id="call_123", name="email", args={"action": "check"}),
    ])
    iface.add_tool_results([
        ToolResultBlock(id="call_123", name="email", content={"count": 0}),
    ])

    items = to_responses_input(iface)

    assert items == [
        {"role": "user", "content": "What should we do?"},
        {
            "type": "reasoning",
            "summary": [
                {
                    "type": "summary_text",
                    "text": "Need to inspect the inbox before answering.",
                }
            ],
        },
        {"role": "assistant", "content": "I'll check first."},
        {
            "type": "function_call",
            "call_id": "call_123",
            "name": "email",
            "arguments": '{"action": "check"}',
        },
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": '{"count": 0}',
        },
    ]


def test_responses_input_omits_empty_thinking_blocks():
    iface = ChatInterface()
    iface.add_assistant_message([
        ThinkingBlock(text=""),
        TextBlock(text="visible"),
    ])

    assert to_responses_input(iface) == [
        {"role": "assistant", "content": "visible"},
    ]


def test_responses_input_replays_valid_raw_items_in_original_order_and_without_mutation():
    iface = ChatInterface()
    iface.add_user_message("start")
    blocks = [
        ThinkingBlock(text=""),
        TextBlock(text="visible"),
        ToolCallBlock(id="call_raw", name="lookup", args={"query": "x"}),
    ]
    raw_items = [
        {
            "type": "message",
            "id": "msg_raw",
            "phase": "commentary",
            "content": [{"type": "output_text", "text": "visible"}],
        },
        {
            "type": "reasoning",
            "id": "rs_raw",
            "summary": [],
            "encrypted_content": "opaque",
        },
        {
            "type": "function_call",
            "id": "fc_raw",
            "status": "completed",
            "call_id": "call_raw",
            "name": "lookup",
            "arguments": '{"query":"x"}',
        },
    ]
    iface.add_assistant_message(
        blocks,
        provider_data={
            "openai_responses_output_items": raw_items,
            "openai_responses_replay_fingerprint": _responses_replay_fingerprint(blocks),
        },
    )
    iface.add_tool_results([
        ToolResultBlock(id="call_raw", name="lookup", content={"ok": True}),
    ])

    first = to_responses_input(iface, replay_raw_output_items=True)
    first[1]["phase"] = "mutated locally"
    second = to_responses_input(iface, replay_raw_output_items=True)

    assert second[1:4] == raw_items
    assert second[1]["phase"] == "commentary"
    assert second[4] == {
        "type": "function_call_output",
        "call_id": "call_raw",
        "output": '{"ok": true}',
    }


def test_responses_input_invalidates_raw_items_when_canonical_content_changes():
    iface = ChatInterface()
    iface.add_user_message("start")
    blocks = [ThinkingBlock(text="old summary"), TextBlock(text="old text")]
    iface.add_assistant_message(
        blocks,
        provider_data={
            "openai_responses_output_items": [
                {
                    "type": "message",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "old text"}],
                },
            ],
            "openai_responses_replay_fingerprint": _responses_replay_fingerprint(blocks),
        },
    )
    blocks[1].text = "edited text"

    assert to_responses_input(iface, replay_raw_output_items=True) == [
        {"role": "user", "content": "start"},
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "old summary"}],
        },
        {"role": "assistant", "content": "edited text"},
    ]


@pytest.mark.parametrize("bad_leaf", [object(), float("nan"), {1: "non-string key"}])
def test_responses_input_rejects_non_json_raw_snapshot_leaves(bad_leaf):
    iface = ChatInterface()
    blocks = [TextBlock(text="visible")]
    iface.add_assistant_message(
        blocks,
        provider_data={
            "openai_responses_output_items": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": bad_leaf}],
                },
            ],
            "openai_responses_replay_fingerprint": _responses_replay_fingerprint(blocks),
        },
    )

    assert to_responses_input(iface, replay_raw_output_items=True) == [
        {"role": "assistant", "content": "visible"},
    ]


def test_codex_conversion_keeps_generic_raw_sidecar_opt_in_only():
    iface = ChatInterface()
    blocks = [TextBlock(text="visible")]
    iface.add_assistant_message(
        blocks,
        provider_data={
            "openai_responses_output_items": [
                {
                    "type": "message",
                    "id": "generic_message",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "visible"}],
                },
            ],
            "openai_responses_replay_fingerprint": _responses_replay_fingerprint(blocks),
        },
    )

    generic = to_responses_input(iface, replay_raw_output_items=True)
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
    )
    codex = adapter.create_chat("gpt-5.5", "system", interface=iface)

    assert generic[0]["phase"] == "commentary"
    assert codex._frozen_responses_input(iface) == [
        {"role": "assistant", "content": "visible"},
    ]


def test_responses_input_never_replays_redacted_raw_value():
    iface = ChatInterface()
    iface.add_assistant_message(
        [ThinkingBlock(text="visible summary")],
        provider_data={
            "openai_responses_output_items": [
                {
                    "type": "reasoning",
                    "summary": [],
                    "encrypted_content": "<REDACTED:secret>",
                },
            ],
            "openai_responses_replay_fingerprint": _responses_replay_fingerprint(
                [ThinkingBlock(text="visible summary")]
            ),
        },
    )

    replay = to_responses_input(iface, replay_raw_output_items=True)

    assert replay == [
        {
            "type": "reasoning",
            "summary": [{"type": "summary_text", "text": "visible summary"}],
        },
    ]
    assert "REDACTED" not in str(replay)
