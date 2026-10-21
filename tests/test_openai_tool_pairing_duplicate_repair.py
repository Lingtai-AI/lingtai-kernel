"""Regression tests for repeated tool results at the Chat Completions boundary.

A session rebuild or an interrupted turn can append a tool result that an
earlier segment already recorded, so the same ``tool_call_id`` shows up twice.
The strict pairing guard rejected such a history (``duplicate`` /
``non_contiguous``) and every later request failed, leaving the agent unable to
speak at all. The wire layer now drops the redundant repeat before validation.
"""
from __future__ import annotations

import pytest

from lingtai.llm.openai.adapter import (
    _OpenAIToolPairingError,
    _dedupe_openai_tool_results,
    _validate_openai_tool_pairing,
)


def _call(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "shell", "arguments": "{}"},
    }


def _assistant(*call_ids: str) -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [_call(i) for i in call_ids]}


def _result(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def test_duplicate_result_in_same_batch_is_dropped_before_validation() -> None:
    messages = [
        {"role": "user", "content": "go"},
        _assistant("call_a", "call_b"),
        _result("call_a", "first-a"),
        _result("call_b", "first-b"),
        _result("call_a", "repeat-a"),
        _result("call_b", "repeat-b"),
    ]

    with pytest.raises(_OpenAIToolPairingError) as excinfo:
        _validate_openai_tool_pairing(messages)
    assert excinfo.value.reason == "duplicate"

    repaired = _dedupe_openai_tool_results(messages)

    assert len(repaired) == len(messages) - 2
    # The first occurrence wins; the repeated copy is the one dropped.
    assert repaired[2]["content"] == "first-a"
    assert repaired[3]["content"] == "first-b"
    _validate_openai_tool_pairing(repaired)


def test_repeat_across_batches_is_dropped_before_validation() -> None:
    messages = [
        {"role": "user", "content": "go"},
        _assistant("call_a"),
        _result("call_a"),
        _assistant("call_b"),
        _result("call_a"),  # replayed from the previous segment
        _result("call_b"),
    ]

    # The replayed result for ``call_a`` is not part of this assistant batch,
    # so the run no longer covers its batch exactly once.
    with pytest.raises(_OpenAIToolPairingError) as excinfo:
        _validate_openai_tool_pairing(messages)
    assert excinfo.value.reason == "wrong_batch"

    repaired = _dedupe_openai_tool_results(messages)

    assert len(repaired) == len(messages) - 1
    _validate_openai_tool_pairing(repaired)


def test_history_without_repeats_is_unchanged() -> None:
    messages = [
        {"role": "user", "content": "go"},
        _assistant("call_a", "call_b"),
        _result("call_a"),
        _result("call_b"),
        {"role": "assistant", "content": "done"},
    ]

    repaired = _dedupe_openai_tool_results(messages)

    assert repaired == messages
    _validate_openai_tool_pairing(repaired)


def test_non_dict_and_idless_entries_are_preserved() -> None:
    messages = [
        {"role": "user", "content": "go"},
        _assistant("call_a"),
        _result("call_a"),
        "not-a-message",
        {"role": "tool", "content": "no id"},
    ]

    repaired = _dedupe_openai_tool_results(messages)

    assert repaired == messages


def test_repair_is_idempotent() -> None:
    messages = [
        {"role": "user", "content": "go"},
        _assistant("call_a"),
        _result("call_a"),
        _result("call_a"),
        _result("call_a"),
    ]

    once = _dedupe_openai_tool_results(messages)
    twice = _dedupe_openai_tool_results(once)

    assert once == twice
    assert len(once) == len(messages) - 2
    _validate_openai_tool_pairing(once)
