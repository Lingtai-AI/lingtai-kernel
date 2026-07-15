"""Deterministic contract tests for the canonical two-axis metadata boundary."""

import json
from copy import deepcopy
from types import SimpleNamespace

from lingtai.kernel.llm.interface import ChatInterface, ToolResultBlock, content_block_from_dict
from lingtai.kernel.meta_block import finalize_two_axis_sidecars
from lingtai.llm.interface_converters import (
    _normalize_runtime_envelope,
    to_anthropic,
    to_gemini,
    to_openai,
    to_responses_input,
)
from lingtai.kernel.loop_guard import LoopGuard
from lingtai.kernel.tool_executor import ToolExecutor
from lingtai.kernel.llm.base import ToolCall


class _NotificationStore:
    def __init__(self, snapshot):
        self.current = snapshot

    def snapshot(self, _allowed):
        return deepcopy(self.current)

    def fingerprint(self, _allowed):
        return "stable"


def _metadata():
    return {
        "tool_meta": {"id": "call-1", "status": "ok", "char_count": 3},
        "agent_meta": {
            "instruction": "Only the latest agent_meta in conversation is current; older ones are historical traces.",
            "agent_state": {"token_usage": {"session": {"input_tokens": 4}}, "events": {"reconstruction": {"one_shot": True}}},
            "notifications": {"attention": {"mail": {"count": 1}}, "persistent": {"email": {"thread": "t1"}}},
            "guidance": {"persistent": {"ref": "meta_guidance"}, "transient": {"ref": "notification"}},
        },
    }


def test_tool_result_sidecar_round_trips_and_projects_for_dict_and_string():
    for content in ({"ok": True}, "plain text"):
        block = ToolResultBlock("call-1", "read", content, metadata=_metadata())
        restored = content_block_from_dict(block.to_dict())
        assert restored.content == content
        assert restored.metadata == _metadata()
        interface = ChatInterface()
        interface.add_assistant_message([])
        interface.entries[-1].content = []
        interface.add_tool_results([block])
        rendered = to_responses_input(interface)
        output = json.loads(rendered[-1]["output"])
        assert output["_meta"] == _metadata()
        if isinstance(content, dict):
            assert output["ok"] is True
        else:
            assert output["result"] == content


def test_finalizer_preserves_handler_content_and_canonicalizes_sidecar():
    # The handler content below is an explicit legacy input envelope. The
    # finalizer must preserve it byte-for-byte while canonicalizing sidecars.
    block = ToolResultBlock(
        "call-2",
        "notify",
        {"value": 1, "_meta": {
            "tool_meta": {"id": "call-2"},
            "agent_meta": {"agent_state": {"context": {"usage": 0.8}}},
            "notifications": {"mcp": {"count": 1}},
            "notification_persistent": {"email": {"thread": "t2"}},
            "guidance": {"ref": "stable"},
            "notification_guidance": {"ref": "transient"},
        }},
        metadata={"tool_meta": {"id": "call-2"}},
    )
    finalize_two_axis_sidecars([block])
    assert set(block.metadata) == {"tool_meta"}
    assert block.content["_meta"]["notifications"] == {"mcp": {"count": 1}}
    assert block.content["_meta"]["notification_persistent"] == {"email": {"thread": "t2"}}


def test_tool_only_sidecar_has_no_invented_agent_axis_on_all_providers():
    block = ToolResultBlock("d1", "read", {"ok": True}, metadata={
        "tool_meta": {"id": "d1", "timestamp": "T"},
    })
    iface = ChatInterface()
    iface.add_assistant_message([])
    iface.entries[-1].content = []
    iface.add_tool_results([block])
    wires = [
        to_anthropic(iface)[-1]["content"][0]["content"],
        to_openai(iface)[-1]["content"],
        to_responses_input(iface)[-1]["output"],
        to_gemini(iface)[-1]["content"][0]["result"],
    ]
    for wire in wires:
        value = json.loads(wire)
        assert value["_meta"] == {"tool_meta": {"id": "d1", "timestamp": "T"}}


def test_legacy_projection_moves_every_old_kernel_field_without_mutating_history():
    old = {
        "tool_meta": {
            "id": "old", "current_time": "T", "token_usage": {"n": 1},
            "context": {"usage": 0.8}, "reconstruction": {"kind": "rebuild"},
        },
        "notifications": {"email": {"count": 1}},
        "notification_persistent": {"email": {"thread": "t"}},
        "guidance": {"catalog": "persistent"},
        "notification_guidance": {"safety": "transient"},
    }
    before = json.loads(json.dumps(old))
    normalized = _normalize_runtime_envelope(old)
    assert normalized["tool_meta"] == {"id": "old"}
    state = normalized["agent_meta"]["agent_state"]
    assert state["current_time"] == "T"
    assert state["token_usage"] == {"n": 1}
    assert state["context"] == {"usage": 0.8}
    assert state["events"]["reconstruction"] == {"kind": "rebuild"}
    assert normalized["agent_meta"]["notifications"] == {
        "attention": {"email": {"count": 1}},
        "persistent": {"email": {"thread": "t"}},
    }
    assert normalized["agent_meta"]["guidance"] == {
        "persistent": {"catalog": "persistent"},
        "transient": {"safety": "transient"},
    }
    assert old == before


def test_business_meta_is_not_restored_as_runtime_axes():
    business = {"_meta": {"order_id": "b1", "status": "paid"}, "ok": True}
    block = ToolResultBlock("b1", "business", business)
    iface = ChatInterface()
    iface.add_assistant_message([])
    iface.entries[-1].content = []
    iface.add_tool_results([block])
    wire = json.loads(to_responses_input(iface)[-1]["output"])
    assert wire["_meta"] == business["_meta"]
    assert _normalize_runtime_envelope(business["_meta"]) is None


def test_latest_whole_snapshot_repeats_active_payload_and_explicitly_clears():
    from lingtai.kernel.meta_block import attach_active_notifications, attach_active_runtime

    agent = SimpleNamespace(
        _notification_store=_NotificationStore({"email": {"attention": "active"}}),
        _notification_payload_signature=None,
        _notification_live_holder=None,
        _notification_fp=None,
        _executor=SimpleNamespace(guard=SimpleNamespace(total_calls=1)),
    )

    def run(token):
        block = ToolResultBlock("x", "read", {"ok": True}, metadata={"tool_meta": {"id": "x"}})
        block._agent_pending = {"agent_state": {"session": token}}
        agent._notification_live_holder = attach_active_notifications(
            agent, [block], prior_holder=agent._notification_live_holder
        )
        attach_active_runtime(agent, [block], prior_holder=None)
        finalize_two_axis_sidecars([block])
        return block

    first = run("one")
    second = run("two")
    assert second.metadata["agent_meta"]["agent_state"]["session"] == "two"
    assert "attention" in second.metadata["agent_meta"]["notifications"]
    agent._notification_store.current = {}
    cleared = run("two")
    assert cleared.metadata["agent_meta"]["notifications"] == {}
    assert first.metadata["agent_meta"]["notifications"]


def test_executor_uses_final_block_for_all_handler_shapes_and_normalizes_state():
    def make_result(name, value, tool_call_id=None):
        return ToolResultBlock(tool_call_id or "", name, value)

    executor = ToolExecutor(
        lambda call: {"kind": "dict"} if call.name == "dict" else "plain",
        make_result,
        LoopGuard(20),
        known_tools={"dict", "text"},
        meta_fn=lambda: {"current_time": "NOW", "agent_state": {"session": "s"}},
    )
    for calls in (
        [ToolCall("dict", {}, "d1"), ToolCall("text", {}, "t1")],
        [ToolCall("text", {}, "t2"), ToolCall("dict", {}, "d2")],
    ):
        results, _, _ = executor.execute(calls)
        agent = type("AgentStub", (), {"_agent_meta_signature": None, "_executor": executor})()
        from lingtai.kernel.meta_block import attach_active_runtime

        attach_active_runtime(agent, results)
        finalize_two_axis_sidecars(results)
        assert results[-1].metadata.get("agent_meta")
        assert all("agent_meta" not in result.metadata for result in results[:-1])
        state = results[-1].metadata["agent_meta"]["agent_state"]
        assert state["current_time"] == "NOW"
        assert "agent_state" not in state
        assert all("_agent_pending" not in result.to_dict() for result in results)
