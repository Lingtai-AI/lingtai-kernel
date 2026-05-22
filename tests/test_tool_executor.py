"""Tests for ToolExecutor — sequential and parallel tool execution."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

from lingtai_kernel.llm.base import ToolCall
from lingtai_kernel.llm.interface import ToolResultBlock
from lingtai_kernel.loop_guard import LoopGuard
from lingtai_kernel.tool_executor import ToolExecutor
from lingtai_kernel.types import UnknownToolError


def make_executor(dispatch_fn=None, parallel_safe=None, known_tools=None):
    if dispatch_fn is None:
        dispatch_fn = lambda tc: {"status": "ok", "result": f"ran {tc.name}"}
    make_result = MagicMock(side_effect=lambda name, result, **kw: {"name": name, "result": result})
    guard = LoopGuard(max_total_calls=50)
    return ToolExecutor(
        dispatch_fn=dispatch_fn,
        make_tool_result_fn=make_result,
        guard=guard,
        known_tools=known_tools,
        parallel_safe_tools=parallel_safe or set(),
    )


def test_execute_single_tool():
    executor = make_executor()
    calls = [ToolCall(name="read", args={"path": "/tmp"}, id="tc1")]
    results, intercepted, text = executor.execute(calls)
    assert len(results) == 1
    assert not intercepted


def test_execute_sequential_multiple():
    order = []
    def dispatch(tc):
        order.append(tc.name)
        return {"status": "ok"}
    executor = make_executor(dispatch_fn=dispatch)
    calls = [
        ToolCall(name="a", args={}, id="1"),
        ToolCall(name="b", args={}, id="2"),
    ]
    results, intercepted, text = executor.execute(calls)
    assert len(results) == 2
    assert order == ["a", "b"]


def test_execute_parallel():
    def dispatch(tc):
        time.sleep(0.05)
        return {"status": "ok", "tool": tc.name}
    executor = make_executor(
        dispatch_fn=dispatch,
        parallel_safe={"a", "b"},
    )
    calls = [
        ToolCall(name="a", args={}, id="1"),
        ToolCall(name="b", args={}, id="2"),
    ]
    t0 = time.monotonic()
    results, intercepted, text = executor.execute(calls)
    elapsed = time.monotonic() - t0
    assert len(results) == 2
    assert elapsed < 0.15


def test_intercept_hook():
    executor = make_executor()
    hook = MagicMock(return_value="intercepted!")
    calls = [ToolCall(name="read", args={}, id="1")]
    results, intercepted, text = executor.execute(calls, on_result_hook=hook)
    assert intercepted
    assert text == "intercepted!"


def test_error_collected():
    def dispatch(tc):
        raise ValueError("something broke")
    executor = make_executor(dispatch_fn=dispatch)
    calls = [ToolCall(name="bad", args={}, id="1")]
    errors = []
    results, intercepted, text = executor.execute(calls, collected_errors=errors)
    assert len(results) == 1
    assert "bad" in errors[0]
    assert "something broke" in errors[0]


def test_cancel_event_stops_sequential():
    cancel = threading.Event()
    cancel.set()
    executor = make_executor()
    calls = [ToolCall(name="a", args={}, id="1")]
    results, intercepted, text = executor.execute(calls, cancel_event=cancel)
    assert results == []


def test_unknown_tool_with_known_tools():
    executor = make_executor(known_tools={"read", "write"})
    calls = [ToolCall(name="bogus", args={}, id="1")]
    errors = []
    results, intercepted, text = executor.execute(calls, collected_errors=errors)
    assert len(results) == 1
    assert any("bogus" in e for e in errors)


def test_guard_property():
    executor = make_executor()
    old_guard = executor.guard
    new_guard = LoopGuard(max_total_calls=10)
    executor.guard = new_guard
    assert executor.guard is new_guard


def test_reasoning_stripped_from_args():
    dispatched_args = []
    def dispatch(tc):
        dispatched_args.append(tc.args)
        return {"status": "ok"}
    executor = make_executor(dispatch_fn=dispatch)
    calls = [ToolCall(name="read", args={"path": "/tmp", "reasoning": "because"}, id="1")]
    executor.execute(calls)
    assert "reasoning" not in dispatched_args[0]
    assert dispatched_args[0].get("_reasoning") == "because"


def test_tool_executor_uses_meta_fn_for_stamping():
    """ToolExecutor calls meta_fn once per tool call and merges the returned
    dict onto the result alongside _elapsed_ms."""
    meta_calls = {"n": 0}

    def meta_fn():
        meta_calls["n"] += 1
        return {"current_time": "FAKE-TS", "future_field": meta_calls["n"]}

    def dispatch(tc):
        return {"status": "ok", "echo": tc.args}

    def make_result(name, result, **kw):
        return {"name": name, "result": result, **kw}

    exe = ToolExecutor(
        dispatch_fn=dispatch,
        make_tool_result_fn=make_result,
        guard=LoopGuard(max_total_calls=10, dup_free_passes=2, dup_hard_block=8),
        known_tools={"noop"},
        parallel_safe_tools=set(),
        logger_fn=None,
        meta_fn=meta_fn,
    )
    results, intercepted, _ = exe.execute([ToolCall(id="c1", name="noop", args={})])
    assert not intercepted
    assert meta_calls["n"] == 1
    payload = results[0]["result"]
    assert payload["current_time"] == "FAKE-TS"
    assert payload["future_field"] == 1
    assert "_elapsed_ms" in payload


def test_tool_executor_meta_fn_covers_parallel_path():
    """meta_fn is called per-tool in the parallel execution path too,
    and each stamped result carries its meta fields and _elapsed_ms."""
    meta_calls = {"n": 0}

    def meta_fn():
        meta_calls["n"] += 1
        return {"current_time": "FAKE-TS"}

    def dispatch(tc):
        return {"status": "ok"}

    def make_result(name, result, **kw):
        return {"name": name, "result": result, **kw}

    exe = ToolExecutor(
        dispatch_fn=dispatch,
        make_tool_result_fn=make_result,
        guard=LoopGuard(max_total_calls=10, dup_free_passes=2, dup_hard_block=8),
        known_tools={"noop"},
        parallel_safe_tools={"noop"},  # force parallel path
        logger_fn=None,
        meta_fn=meta_fn,
    )
    results, intercepted, _ = exe.execute([
        ToolCall(id="c1", name="noop", args={}),
        ToolCall(id="c2", name="noop", args={}),
    ])
    assert not intercepted
    assert meta_calls["n"] == 2
    for r in results:
        payload = r["result"]
        assert payload["current_time"] == "FAKE-TS"
        assert "_elapsed_ms" in payload


def test_secondary_executes_before_primary_and_is_stripped():
    seen = []

    def dispatch(tc):
        seen.append((tc.name, dict(tc.args)))
        if tc.name == "telegram":
            return {"status": "sent", "message_id": "secret-should-not-leak"}
        assert "secondary" not in tc.args
        return {"status": "ok", "echo": tc.args}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "path": "/tmp",
            "secondary": {
                "tool": "telegram",
                "args": {"action": "send", "chat_id": 123, "text": "starting"},
            },
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert [name for name, _ in seen] == ["telegram", "read"]
    assert "secondary" not in seen[1][1]
    payload = results[0]["result"]
    assert payload["status"] == "ok"
    assert payload["_secondary"] == {
        "status": "success",
        "tool": "telegram",
        "action": "send",
    }
    assert "secret-should-not-leak" not in str(payload["_secondary"])


def test_secondary_unknown_tool_does_not_block_primary():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={"secondary": {"tool": "bash", "args": {"action": "run"}}},
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == ["read"]
    payload = results[0]["result"]
    assert payload["status"] == "ok"
    assert payload["_secondary"]["status"] == "error"
    assert payload["_secondary"]["tool"] == "bash"
    assert "not allowed" in payload["_secondary"]["message"]


def test_secondary_disallowed_action_does_not_block_primary():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {"action": "read", "chat_id": 123},
            }
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == ["read"]
    payload = results[0]["result"]
    assert payload["_secondary"]["status"] == "error"
    assert payload["_secondary"]["tool"] == "telegram"
    assert payload["_secondary"]["action"] == "read"
    assert "action" in payload["_secondary"]["message"]


def test_secondary_recursive_call_rejected():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {
                    "action": "send",
                    "chat_id": 123,
                    "text": "starting",
                    "secondary": {"tool": "telegram", "args": {"action": "send"}},
                },
            }
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == ["read"]
    payload = results[0]["result"]
    assert payload["_secondary"]["status"] == "error"
    assert "recursive" in payload["_secondary"]["message"]


def test_secondary_exception_does_not_block_primary():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        if tc.name == "telegram":
            raise RuntimeError("network down")
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {"action": "send", "chat_id": 123, "text": "starting"},
            }
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == ["telegram", "read"]
    payload = results[0]["result"]
    assert payload["status"] == "ok"
    assert payload["_secondary"]["status"] == "error"
    assert "network down" in payload["_secondary"]["message"]


def test_secondary_parallel_path():
    seen = []
    lock = threading.Lock()

    def dispatch(tc):
        with lock:
            seen.append((tc.name, dict(tc.args)))
        return {"status": "ok", "tool": tc.name}

    executor = make_executor(
        dispatch_fn=dispatch,
        known_tools={"read", "telegram"},
        parallel_safe={"read"},
    )
    calls = [
        ToolCall(
            name="read",
            args={
                "path": "/tmp/a",
                "secondary": {
                    "tool": "telegram",
                    "args": {"action": "send", "chat_id": 123, "text": "a"},
                },
            },
            id="tc1",
        ),
        ToolCall(name="read", args={"path": "/tmp/b"}, id="tc2"),
    ]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert len(results) == 2
    assert results[0]["result"]["_secondary"]["status"] == "success"
    assert "_secondary" not in results[1]["result"]
    assert all("secondary" not in args for _, args in seen if args.get("path"))



def test_secondary_rejected_when_primary_is_communication_tool():
    seen = []

    def dispatch(tc):
        seen.append((tc.name, dict(tc.args)))
        return {"status": "ok", "tool": tc.name}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"telegram"})
    calls = [ToolCall(
        name="telegram",
        args={
            "action": "send",
            "chat_id": 123,
            "text": "primary message",
            "secondary": {
                "tool": "telegram",
                "args": {"action": "send", "chat_id": 123, "text": "nested message"},
            },
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == [("telegram", {"action": "send", "chat_id": 123, "text": "primary message"})]
    payload = results[0]["result"]
    assert payload["status"] == "ok"
    assert payload["_secondary"] == {
        "status": "error",
        "message": "primary tool 'telegram' may not carry a secondary",
    }


def test_secondary_reasoning_fields_are_stripped_from_secondary_args():
    seen = []

    def dispatch(tc):
        seen.append((tc.name, dict(tc.args)))
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {
                    "action": "send",
                    "chat_id": 123,
                    "text": "starting",
                    "reasoning": "nested reason should not reach handler",
                    "commentary": "nested commentary should not reach handler",
                    "_sync": True,
                },
            }
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    telegram_args = seen[0][1]
    assert seen[0][0] == "telegram"
    assert "reasoning" not in telegram_args
    assert "commentary" not in telegram_args
    assert "_sync" not in telegram_args
    assert results[0]["result"]["_secondary"]["status"] == "success"


def test_secondary_missing_action_is_rejected_without_blocking_primary():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={"secondary": {"tool": "telegram", "args": {"chat_id": 123, "text": "starting"}}},
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == ["read"]
    secondary = results[0]["result"]["_secondary"]
    assert secondary["status"] == "error"
    assert secondary["tool"] == "telegram"
    assert "action" in secondary["message"]


def test_secondary_deep_recursive_key_rejected():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {
                    "action": "send",
                    "chat_id": 123,
                    "text": "starting",
                    "reply_markup": {"secondary": {"tool": "telegram", "args": {"action": "send"}}},
                },
            }
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    assert seen == ["read"]
    assert results[0]["result"]["_secondary"]["status"] == "error"
    assert "recursive" in results[0]["result"]["_secondary"]["message"]


def test_secondary_still_reports_when_primary_unknown_sequential():
    seen = []

    def dispatch(tc):
        seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(dispatch_fn=dispatch, known_tools={"telegram"})
    calls = [ToolCall(
        name="bogus",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {"action": "send", "chat_id": 123, "text": "starting"},
            }
        },
        id="tc1",
    )]

    errors = []
    results, intercepted, _ = executor.execute(calls, collected_errors=errors)

    assert not intercepted
    assert seen == ["telegram"]
    payload = results[0]["result"]
    assert payload["status"] == "error"
    assert payload["_secondary"]["status"] == "success"
    assert any("bogus" in err for err in errors)


def test_secondary_still_reports_when_primary_unknown_parallel():
    seen = []
    lock = threading.Lock()

    def dispatch(tc):
        with lock:
            seen.append(tc.name)
        return {"status": "ok"}

    executor = make_executor(
        dispatch_fn=dispatch,
        known_tools={"read", "telegram"},
        parallel_safe={"read", "bogus"},
    )
    calls = [
        ToolCall(
            name="bogus",
            args={
                "secondary": {
                    "tool": "telegram",
                    "args": {"action": "send", "chat_id": 123, "text": "starting"},
                }
            },
            id="tc1",
        ),
        ToolCall(name="read", args={"path": "/tmp/b"}, id="tc2"),
    ]

    errors = []
    results, intercepted, _ = executor.execute(calls, collected_errors=errors)

    assert not intercepted
    assert "telegram" in seen
    assert "read" in seen
    assert results[0]["result"]["status"] == "error"
    assert results[0]["result"]["_secondary"]["status"] == "success"
    assert any("bogus" in err for err in errors)


def test_secondary_wraps_non_dict_primary_result_under_reserved_key():
    def dispatch(tc):
        if tc.name == "telegram":
            return {"status": "sent", "message_id": "secret-should-not-leak"}
        return "plain primary result"

    executor = make_executor(dispatch_fn=dispatch, known_tools={"read", "telegram"})
    calls = [ToolCall(
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {"action": "send", "chat_id": 123, "text": "starting"},
            }
        },
        id="tc1",
    )]

    results, intercepted, _ = executor.execute(calls)

    assert not intercepted
    payload = results[0]["result"]
    assert payload["result"] == "plain primary result"
    assert payload["_secondary"] == {"status": "success", "tool": "telegram", "action": "send"}


def test_secondary_survives_canonical_tool_result_block_wire_shape():
    def dispatch(tc):
        if tc.name == "telegram":
            return {"status": "sent", "message_id": "secret-should-not-leak"}
        return {"status": "ok"}

    def make_result(name, result, **kw):
        return ToolResultBlock(
            id=kw.get("tool_call_id") or name,
            name=name,
            content=result,
        )

    exe = ToolExecutor(
        dispatch_fn=dispatch,
        make_tool_result_fn=make_result,
        guard=LoopGuard(max_total_calls=10, dup_free_passes=2, dup_hard_block=8),
        known_tools={"read", "telegram"},
        parallel_safe_tools=set(),
    )
    results, intercepted, _ = exe.execute([ToolCall(
        id="tc1",
        name="read",
        args={
            "secondary": {
                "tool": "telegram",
                "args": {"action": "send", "chat_id": 123, "text": "starting"},
            }
        },
    )])

    assert not intercepted
    block = results[0]
    assert isinstance(block, ToolResultBlock)
    assert block.content["_secondary"] == {"status": "success", "tool": "telegram", "action": "send"}
    assert block.to_dict()["content"]["_secondary"]["status"] == "success"
