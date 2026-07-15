"""Tests for Codex WS tool-result output freezing (resident-meta canonicalization).

Root cause being guarded here (see report
``reports/claude-p-codex-tool-result-canonicalization.md``):

The kernel records each batch's coherent current ``_meta.agent_meta`` snapshot
on its final tool result. Older ``ToolResultBlock`` content remains historical,
so the same tool call's ``function_call_output.output`` can legitimately differ
between turns when canonical history is replayed.

For the stateful Codex WS delta path that is fatal: the next request's full
converted input must *strict-prefix-match* the prior baseline, and a changed
older ``function_call_output`` (same ``call_id`` / keys, different ``output``
hash) breaks the prefix and forces ``ws_full`` every turn — the exact
``prefix_mismatch`` Jason observed.

The fix freezes each ``function_call_output.output`` by ``call_id`` at first
send for the life of the WS session, so replay is byte-identical regardless of
in-place resident-meta movement. The freshest result is first-seen on its own
turn, so it is frozen WITH its live meta — the model never loses guidance /
notifications it must see.

These tests are content-free: they assert structure/identity only and never log
tool-result bodies.
"""

from __future__ import annotations

from lingtai.llm.interface_converters import _RESPONSES_ORPHAN_OUTPUT_PLACEHOLDER
from lingtai.llm.openai.adapter import _freeze_responses_outputs


def _fco(call_id: str, output: str) -> dict:
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def test_freeze_registers_first_seen_output_unchanged():
    frozen: dict[str, str] = {}
    items = [
        {"role": "user", "content": "hi"},
        _fco("call_a", '{"ok": true, "_meta": {"agent_meta": {"x": 1}}}'),
    ]

    out = _freeze_responses_outputs(items, frozen)

    # First pass returns the items byte-equal and records the output.
    assert out == items
    assert frozen == {"call_a": '{"ok": true, "_meta": {"agent_meta": {"x": 1}}}'}


def test_freeze_replays_frozen_output_when_resident_meta_moves_away():
    frozen: dict[str, str] = {}
    # Turn N: call_a is the LAST result and carries the moving meta.
    turn_n = [_fco("call_a", '{"ok": true, "_meta": {"agent_meta": {"x": 1}}}')]
    _freeze_responses_outputs(turn_n, frozen)

    # Turn N+1: a newer result arrived; the kernel stripped call_a's latest-only
    # meta and moved it onto call_b. call_a's raw content now serializes
    # differently (meta gone) — but the frozen replay must keep the original.
    turn_n1 = [
        _fco("call_a", '{"ok": true}'),  # resident meta stripped in place
        _fco("call_b", '{"done": true, "_meta": {"agent_meta": {"x": 2}}}'),
    ]

    out = _freeze_responses_outputs(turn_n1, frozen)

    # call_a replays its FROZEN (turn-N) output byte-for-byte: strict prefix holds.
    assert out[0]["output"] == '{"ok": true, "_meta": {"agent_meta": {"x": 1}}}'
    # call_b is first-seen and freezes WITH its live meta (model still sees it).
    assert out[1]["output"] == '{"done": true, "_meta": {"agent_meta": {"x": 2}}}'
    assert frozen["call_b"] == '{"done": true, "_meta": {"agent_meta": {"x": 2}}}'


def test_freeze_is_idempotent_across_repeated_calls():
    frozen: dict[str, str] = {}
    items = [_fco("call_a", "result-1")]

    first = _freeze_responses_outputs(items, frozen)
    # Even if the underlying raw output changes on a later pass, replay is stable.
    mutated = [_fco("call_a", "result-1-with-meta-appended")]
    second = _freeze_responses_outputs(mutated, frozen)
    third = _freeze_responses_outputs(mutated, frozen)

    assert first[0]["output"] == "result-1"
    assert second[0]["output"] == "result-1"
    assert third[0]["output"] == "result-1"


def test_freeze_does_not_mutate_caller_items():
    frozen = {"call_a": "frozen-original"}
    original = _fco("call_a", "live-mutated")
    items = [original]

    out = _freeze_responses_outputs(items, frozen)

    # A fresh dict is returned; the caller's item is untouched.
    assert out[0]["output"] == "frozen-original"
    assert original["output"] == "live-mutated"
    assert out[0] is not original


def test_freeze_leaves_non_tool_result_items_untouched():
    frozen: dict[str, str] = {}
    items = [
        {"role": "user", "content": "u"},
        {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": "{}"},
        {"role": "assistant", "content": "a"},
        _fco("c1", "tool-out"),
    ]

    out = _freeze_responses_outputs(items, frozen)

    assert out[0] == {"role": "user", "content": "u"}
    assert out[1] == {"type": "function_call", "call_id": "c1", "name": "bash", "arguments": "{}"}
    assert out[2] == {"role": "assistant", "content": "a"}
    assert out[3]["output"] == "tool-out"
    # Only the function_call_output registered a freeze entry.
    assert set(frozen) == {"c1"}


def test_freeze_never_freezes_synthesized_orphan_placeholder():
    # Turn N ends on an unanswered function_call -> to_responses_input injects
    # the orphan placeholder for call_a (issue #170 wire guard). It must NOT be
    # frozen, or the real continuation next turn would be hidden behind it.
    frozen: dict[str, str] = {}
    turn_n = [_fco("call_a", _RESPONSES_ORPHAN_OUTPUT_PLACEHOLDER)]
    _freeze_responses_outputs(turn_n, frozen)
    assert "call_a" not in frozen

    # Turn N+1: the REAL tool result arrives for call_a. It freezes for real and
    # is replayed (not the placeholder) on subsequent passes.
    turn_n1 = [_fco("call_a", '{"real": "result"}')]
    out = _freeze_responses_outputs(turn_n1, frozen)
    assert out[0]["output"] == '{"real": "result"}'
    assert frozen["call_a"] == '{"real": "result"}'


def test_freeze_ignores_function_call_output_without_call_id():
    frozen: dict[str, str] = {}
    # Defensive: a malformed item missing call_id must pass through untouched and
    # never register a None key.
    items = [{"type": "function_call_output", "output": "x"}]

    out = _freeze_responses_outputs(items, frozen)

    assert out == items
    assert frozen == {}
