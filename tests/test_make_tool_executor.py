"""Parity tests for the consolidated ToolExecutor construction helper (#511).

Two turn.py paths built a ToolExecutor with identical wiring and only a
different LoopGuard. `_make_tool_executor(agent, guard)` centralizes the wiring
while keeping the guard caller-supplied; these tests pin that the helper passes
each field through unchanged (especially the provider on the tool-result
factory) and honors the supplied guard.
"""

from pathlib import Path

from lingtai.kernel.base_agent import turn
from lingtai.kernel.loop_guard import LoopGuard


class _FakeService:
    def __init__(self):
        self.seen = []

    def make_tool_result(self, name, result, provider=None, **kw):
        self.seen.append((name, result, provider, kw))
        return {"name": name, "result": result, "provider": provider, **kw}


class _FakeConfig:
    provider = "anthropic"


class _FakeAgent:
    def __init__(self, tmp_path):
        self.service = _FakeService()
        self._config = _FakeConfig()
        self._intrinsics = {"system": object(), "psyche": object()}
        self._tool_handlers = {"custom_tool": object()}
        self._PARALLEL_SAFE_TOOLS = {"read"}
        self._working_dir = tmp_path
        self._summarize_notification_threshold = None

    def _dispatch_tool(self, *a, **k):  # pragma: no cover - identity check only
        return None

    def _log(self, *a, **k):  # pragma: no cover - identity check only
        return None


def test_make_tool_executor_wires_shared_fields(tmp_path):
    agent = _FakeAgent(tmp_path)
    guard = LoopGuard(max_total_calls=7, dup_free_passes=3, dup_hard_block=8)

    ex = turn._make_tool_executor(agent, guard)

    assert ex._guard is guard
    # Bound methods are re-created per attribute access, so compare by equality.
    assert ex._dispatch_fn == agent._dispatch_tool
    assert ex._known_tools == {"system", "psyche", "custom_tool"}
    assert ex._parallel_safe_tools == {"read"}
    assert ex._logger_fn == agent._log
    assert ex._working_dir == Path(tmp_path)


def test_make_tool_executor_passes_provider_through(tmp_path):
    agent = _FakeAgent(tmp_path)
    ex = turn._make_tool_executor(agent, LoopGuard())

    out = ex._make_tool_result_fn("tool_x", "payload", extra=1)

    assert out["provider"] == "anthropic"
    assert agent.service.seen == [("tool_x", "payload", "anthropic", {"extra": 1})]


def test_make_tool_executor_honors_distinct_guards(tmp_path):
    # The request path uses dup_free_passes=3, the tc-wake path uses 2; the
    # helper must not normalize them.
    agent = _FakeAgent(tmp_path)
    g_request = LoopGuard(max_total_calls=5, dup_free_passes=3, dup_hard_block=8)
    g_tcwake = LoopGuard(max_total_calls=5, dup_free_passes=2, dup_hard_block=8)

    assert turn._make_tool_executor(agent, g_request)._guard is g_request
    assert turn._make_tool_executor(agent, g_tcwake)._guard is g_tcwake
