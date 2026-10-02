"""Slim default runtime snapshot and the read-only ``system(action="meta")``.

The default final-carrier ``agent_meta.agent_state`` carries only the current
time, context size and active warnings; the complete diagnostics come from the
one ``build_full_runtime_meta`` builder on demand.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

from lingtai.kernel.llm.interface import ToolResultBlock
from lingtai.kernel.meta_block import (
    agent_state_from_meta,
    attach_active_runtime,
    build_full_runtime_meta,
    build_meta,
    build_synthetic_meta_envelope,
    build_tool_meta_overflow_comment,
    finalize_two_axis_sidecars,
    slim_agent_state_for_tail,
)
from lingtai.tools import system as system_tool


def _agent(*, context_limit: int = 10_000, reconstruction=None, adapter=None):
    taken = {"count": 0}

    def take():
        taken["count"] += 1
        return reconstruction

    chat = SimpleNamespace(
        take_pending_reconstruction_event=take,
        dynamic_adapter_comment=(lambda: adapter) if adapter else None,
    )
    session = SimpleNamespace(
        chat=chat,
        _token_decomp_dirty=False,
        _system_prompt_tokens=100,
        _tools_tokens=50,
        _latest_input_tokens=1000,
        latest_token_usage_snapshot=lambda: {
            "input_tokens": 1000,
            "cache_miss_tokens": 100,
            "cache_rate": 0.9,
            "output_tokens": 50,
            "thinking_tokens": 5,
            "context_window": context_limit,
        },
    )
    usage = {
        "input_tokens": 400_000,
        "output_tokens": 9_000,
        "cached_tokens": 300_000,
        "api_calls": 40,
        "ctx_total_tokens": 1000,
    }
    agent = SimpleNamespace(
        _config=SimpleNamespace(
            time_awareness=True,
            timezone_awareness=False,
            context_limit=context_limit,
            language="en",
        ),
        _intrinsics={"context", "system"},
        _session=session,
        _executor=SimpleNamespace(guard=SimpleNamespace(total_calls=7)),
        get_token_usage=lambda: dict(usage),
    )
    agent.taken = taken
    return agent


def _pending_from(agent) -> dict:
    return {"agent_state": agent_state_from_meta(build_meta(agent))}


def _carrier(agent, *, result=None, id="t1") -> ToolResultBlock:
    state = _pending_from(agent)["agent_state"]
    return ToolResultBlock(
        id=id,
        name="bash",
        content={"status": "ok"} if result is None else result,
        metadata={"tool_meta": {"id": id}},
        _agent_pending={"agent_state": state},
    )


def test_default_tail_is_exactly_time_and_context_size():
    agent = _agent()
    block = _carrier(agent)

    attach_active_runtime(agent, [block])

    state = block.metadata["agent_meta"]["agent_state"]
    assert set(state) == {"current_time", "token_usage"}
    assert state["current_time"].endswith("Z")
    assert state["token_usage"] == {
        "session": {
            "context_tokens": 1000,
            "context_window": 10_000,
            "context_usage": 0.1,
        }
    }


def test_default_tail_payload_is_much_smaller_than_full_snapshot():
    agent = _agent(adapter={"adapter": "x", "cache_ledger": {"rows": [[i] * 6 for i in range(20)]}})
    full = build_full_runtime_meta(agent)["agent_state"]
    slim = slim_agent_state_for_tail(full)

    full_chars = len(json.dumps(full, sort_keys=True))
    slim_chars = len(json.dumps(slim, sort_keys=True))
    assert slim_chars * 3 < full_chars
    for dropped in (
        "current_tool_result_chars",
        "adapter_comment",
        "active_turn_tool_calls",
    ):
        assert dropped in full and dropped not in slim
    assert set(full["token_usage"]) == {"current_call", "session", "ref"}


def test_context_and_cache_miss_warnings_survive_the_slim_tail():
    agent = _agent(context_limit=1000)  # 1000/1000 -> rebuild hint at >= 85%
    agent._intrinsics = {"context", "system"}
    agent.resolve_cache_miss_budget = lambda: 50_000  # 100k miss >= budget
    block = _carrier(agent)

    attach_active_runtime(agent, [block])

    context = block.metadata["agent_meta"]["agent_state"]["context"]
    assert "rebuild" in context
    assert context["cache_miss_budget"] == 50_000
    assert context["cache_miss_tokens"] == 100_000
    assert "molt now" in context["molt"]


def test_reconstruction_event_rides_the_final_carrier_only():
    event = {"type": "delayed_summarize_reconstruction", "warning": "rebuilt"}
    agent = _agent()
    early = ToolResultBlock(
        id="t1", name="x", content={"status": "ok"},
        metadata={"tool_meta": {"id": "t1"}},
        _agent_pending={"agent_state": {"events": {"reconstruction": event}}},
    )
    final = _carrier(agent, id="t2")

    holder = attach_active_runtime(agent, [early, final])

    assert holder is final
    assert final.metadata["agent_meta"]["agent_state"]["events"]["reconstruction"] == event
    assert "agent_meta" not in early.metadata


def test_multi_call_batch_uses_the_final_result_as_the_only_carrier():
    agent = _agent()
    blocks = [_carrier(agent, id=f"t{i}") for i in range(3)]

    holder = attach_active_runtime(agent, blocks)
    finalize_two_axis_sidecars(blocks)

    assert holder is blocks[-1]
    assert [("agent_meta" in b.metadata) for b in blocks] == [False, False, True]


def test_notifications_on_the_carrier_are_preserved_by_slimming():
    agent = _agent()
    block = _carrier(agent)
    notifications = {
        "attention": {"mail": {"message_ids": ["m1", "m2"], "sender": "a@b"}},
        "persistent": {"mcp": {"telegram": {"messages": [{"id": "17050"}]}}},
    }
    block.metadata["agent_meta"] = {"notifications": copy.deepcopy(notifications)}

    attach_active_runtime(agent, [block])

    assert block.metadata["agent_meta"]["notifications"] == notifications


def test_synthetic_notification_pair_uses_the_same_slim_state():
    agent = _agent()
    envelope = build_synthetic_meta_envelope(agent, {"notifications": {}}, call_id="c1")

    state = envelope["agent_meta"]["agent_state"]
    assert "current_tool_result_chars" not in state
    assert state["token_usage"] == {
        "session": {
            "context_tokens": 1000,
            "context_window": 10_000,
            "context_usage": 0.1,
        }
    }


def test_full_snapshot_reuses_builder_numbers_and_has_fresh_time():
    agent = _agent(adapter={"adapter": "x", "turns": 3})
    full = build_full_runtime_meta(agent)["agent_state"]

    session = full["token_usage"]["session"]
    assert session["session_cache_rate"] == 0.75
    assert session["cache_miss_tokens"] == 100_000
    assert session["context_usage"] == 0.1
    assert full["token_usage"]["current_call"]["input"] == 1000
    assert full["current_tool_result_chars"]["top_results"] == []
    assert full["adapter_comment"] == {"adapter": "x", "turns": 3}
    assert full["active_turn_tool_calls"] == 7
    assert full["current_time"].endswith("Z")


def test_full_snapshot_does_not_consume_the_reconstruction_one_shot():
    event = {"type": "delayed_summarize_reconstruction"}
    agent = _agent(reconstruction=event)

    build_full_runtime_meta(agent)
    build_full_runtime_meta(agent)

    assert agent.taken["count"] == 0
    assert "events" not in build_full_runtime_meta(agent)["agent_state"]


def test_system_meta_action_returns_full_snapshot_and_strict_input(tmp_path):
    agent = _agent()
    calls = []
    agent.runtime_meta = lambda: calls.append(1) or build_full_runtime_meta(agent)
    agent._working_dir = tmp_path

    result = system_tool.handle(agent, {"action": "meta", "input": {}})

    assert result["status"] == "ok"
    assert calls == [1]
    assert "current_call" in result["agent_state"]["token_usage"]
    assert agent.taken["count"] == 0
    schema = system_tool.DECLARATION.input_schemas["meta"]
    assert schema["properties"] == {} and schema["additionalProperties"] is False

    rejected = system_tool.handle(agent, {"action": "meta", "input": {"x": 1}})
    assert rejected["status"] == "failed"
    assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert calls == [1]


def test_overflow_comment_is_compact_locator_plus_one_instruction():
    comment = build_tool_meta_overflow_comment("tc-1")

    assert set(comment) == {"full_original", "after_consuming"}
    assert "logs/events.jsonl" in comment["full_original"]
    assert "tool_call_id=tc-1" in comment["full_original"]
    assert "context(action=\"summarize\")" in comment["after_consuming"]
    assert "system(action=\"summarize\")" not in json.dumps(comment)
