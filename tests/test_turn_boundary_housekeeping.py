"""Parity tests for the consolidated turn-boundary housekeeping helper (#511).

Before consolidation, three turn.py branches inlined the same trio:

    _check_molt_pressure(agent)          # bare — errors propagate
    try: agent._sync_notifications()     # guarded — errors swallowed
    except Exception: pass
    try: agent._rescan_large_tool_results()  # guarded — errors swallowed
    except Exception: pass

These tests pin that exact contract on `_turn_boundary_housekeeping` so the
refactor cannot silently change which step swallows errors or the call order.
"""

import pytest

from lingtai_kernel.base_agent import turn


class _FakeAgent:
    def __init__(self):
        self.calls = []
        # No "psyche" intrinsic → real _check_molt_pressure returns early,
        # but tests monkeypatch it anyway to assert it is invoked first.
        self._intrinsics = {}
        self.sync_raises = False
        self.rescan_raises = False

    def _sync_notifications(self):
        self.calls.append("sync")
        if self.sync_raises:
            raise RuntimeError("boom-sync")

    def _rescan_large_tool_results(self):
        self.calls.append("rescan")
        if self.rescan_raises:
            raise RuntimeError("boom-rescan")


def test_runs_trio_in_order(monkeypatch):
    agent = _FakeAgent()
    molt_called = []
    monkeypatch.setattr(turn, "_check_molt_pressure", lambda a: molt_called.append(a))
    turn._turn_boundary_housekeeping(agent)
    assert molt_called == [agent]
    assert agent.calls == ["sync", "rescan"]


def test_sync_error_is_swallowed_and_rescan_still_runs(monkeypatch):
    agent = _FakeAgent()
    agent.sync_raises = True
    monkeypatch.setattr(turn, "_check_molt_pressure", lambda a: None)
    turn._turn_boundary_housekeeping(agent)  # must not raise
    assert agent.calls == ["sync", "rescan"]


def test_rescan_error_is_swallowed(monkeypatch):
    agent = _FakeAgent()
    agent.rescan_raises = True
    monkeypatch.setattr(turn, "_check_molt_pressure", lambda a: None)
    turn._turn_boundary_housekeeping(agent)  # must not raise
    assert agent.calls == ["sync", "rescan"]


def test_molt_pressure_error_propagates(monkeypatch):
    # Parity: _check_molt_pressure was a bare call, so its errors must NOT be
    # swallowed, and the guarded steps must not run if it raises.
    agent = _FakeAgent()

    def boom(a):
        raise RuntimeError("molt-boom")

    monkeypatch.setattr(turn, "_check_molt_pressure", boom)
    with pytest.raises(RuntimeError, match="molt-boom"):
        turn._turn_boundary_housekeeping(agent)
    assert agent.calls == []


def test_real_check_molt_pressure_noop_without_psyche():
    # Exercises the real _check_molt_pressure early-return path (no "psyche"
    # intrinsic), confirming the helper wires the real function, not a stub.
    agent = _FakeAgent()
    turn._turn_boundary_housekeeping(agent)
    assert agent.calls == ["sync", "rescan"]
