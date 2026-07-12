"""Post-dispatch (result) hook is the completion signal that freezes card rows.

Requirement 5: the smallest optional post-dispatch lifecycle hook must run for
each attempted tool — on success, on a normal tool-error result, on an
intercepted result, and on parallel future completion — on the orchestrating
thread in input order, and must never replace, reorder, mutate, or suppress a
tool result even when the hook raises.

This drives the real ToolExecutor (no card involved) to pin those guarantees at
the owning layer.
"""

from __future__ import annotations

import threading

from lingtai.kernel.tool_executor import ToolExecutor
from lingtai.kernel.llm.base import ToolCall
from lingtai.kernel.loop_guard import LoopGuard


def _executor(dispatch, *, parallel=None, result_hook=None):
    return ToolExecutor(
        dispatch_fn=dispatch,
        make_tool_result_fn=lambda name, result, **kw: result,
        guard=LoopGuard(),
        known_tools={"bash", "read", "grep", "write"},
        parallel_safe_tools=parallel or set(),
    )


# ---------------------------------------------------------------------------
# Sequential: hook fires per call, in order, results preserved
# ---------------------------------------------------------------------------

def test_result_hook_sequential_fires_in_order():
    seen: list = []

    def dispatch(tc):
        return {"status": "ok", "who": tc.name}

    def hook(name, args, result, *, tool_call_id=None):
        seen.append((name, tool_call_id, result.get("who")))
        return None

    ex = _executor(dispatch)
    tcs = [ToolCall(name="bash", args={}, id="c1"),
           ToolCall(name="read", args={}, id="c2")]
    results, intercepted, _ = ex.execute(tcs, on_result_hook=hook)

    assert not intercepted
    assert [s[1] for s in seen] == ["c1", "c2"]
    assert [r["who"] for r in results] == ["bash", "read"]


def test_result_hook_fires_on_tool_error_result():
    seen: list = []

    def dispatch(tc):
        return {"status": "error", "error": "boom"}

    def hook(name, args, result, *, tool_call_id=None):
        seen.append(tool_call_id)
        return None

    ex = _executor(dispatch)
    ex.execute([ToolCall(name="bash", args={}, id="c1")], on_result_hook=hook)
    assert seen == ["c1"]


def test_result_hook_fires_on_intercept_result():
    seen: list = []

    def dispatch(tc):
        return {"intercept": True, "text": "stop here"}

    def hook(name, args, result, *, tool_call_id=None):
        seen.append(tool_call_id)
        return None

    ex = _executor(dispatch)
    results, intercepted, text = ex.execute(
        [ToolCall(name="bash", args={}, id="c1")], on_result_hook=hook)
    assert intercepted is True
    assert text == "stop here"
    assert seen == ["c1"]


# ---------------------------------------------------------------------------
# Parallel: hook fires in input order; results restored to input order
# ---------------------------------------------------------------------------

def test_result_hook_parallel_input_order_preserved():
    seen: list = []

    def dispatch(tc):
        # Make c1 slower so completion order != input order.
        if tc.name == "bash":
            import time
            time.sleep(0.05)
        return {"status": "ok", "who": tc.name}

    def hook(name, args, result, *, tool_call_id=None):
        seen.append(tool_call_id)
        return None

    ex = _executor(dispatch, parallel={"bash", "read"})
    tcs = [ToolCall(name="bash", args={}, id="c1"),
           ToolCall(name="read", args={}, id="c2")]
    results, intercepted, _ = ex.execute(tcs, on_result_hook=hook)

    # Result hook observes input order (deterministic), and results are in
    # input order regardless of which future finished first.
    assert seen == ["c1", "c2"]
    assert [r["who"] for r in results] == ["bash", "read"]


# ---------------------------------------------------------------------------
# Fail-open: a raising hook never alters the tool results
# ---------------------------------------------------------------------------

def test_raising_result_hook_does_not_alter_results_sequential():
    def dispatch(tc):
        return {"status": "ok", "who": tc.name}

    def hook(name, args, result, *, tool_call_id=None):
        raise RuntimeError("hook boom")

    ex = _executor(dispatch)
    tcs = [ToolCall(name="bash", args={}, id="c1"),
           ToolCall(name="read", args={}, id="c2")]
    # The executor swallows the hook exception; results are intact and ordered.
    results, intercepted, _ = ex.execute(tcs, on_result_hook=hook)
    assert not intercepted
    assert [r["who"] for r in results] == ["bash", "read"]


# ---------------------------------------------------------------------------
# Sequential raised dispatch exception fires the hook (parity with parallel),
# so a crashing sequential tool still freezes its Task Card row live rather than
# ticking until teardown. The hook is observe-only and cannot alter the error.
# ---------------------------------------------------------------------------

def test_result_hook_fires_on_sequential_raised_dispatch_exception():
    seen: list = []

    def dispatch(tc):
        raise RuntimeError("dispatch boom")

    def hook(name, args, result, *, tool_call_id=None):
        seen.append((tool_call_id, result))
        return None

    ex = _executor(dispatch)
    results, intercepted, _ = ex.execute(
        [ToolCall(name="bash", args={}, id="c1")], on_result_hook=hook)

    # Hook fired exactly once, with the exact call id and the final error result.
    assert len(seen) == 1
    assert seen[0][0] == "c1"
    assert isinstance(seen[0][1], dict) and seen[0][1].get("status") == "error"
    # The returned tool result is a proper, unchanged error (not intercepted).
    assert not intercepted
    assert len(results) == 1
    assert results[0].get("status") == "error"


def test_raising_result_hook_cannot_alter_sequential_dispatch_error():
    """Even a hook that itself raises on the raised-dispatch path must not
    re-enter the dispatch handler, fake an intercept, or mutate the error."""
    def dispatch(tc):
        raise RuntimeError("dispatch boom")

    def hook(name, args, result, *, tool_call_id=None):
        raise RuntimeError("hook boom")

    ex = _executor(dispatch)
    results, intercepted, _ = ex.execute(
        [ToolCall(name="bash", args={}, id="c1")], on_result_hook=hook)

    assert not intercepted
    assert len(results) == 1
    assert results[0].get("status") == "error"
    # The error is the tool's dispatch failure, not anything the hook injected.
    assert results[0].get("error_phase") == "dispatch"
