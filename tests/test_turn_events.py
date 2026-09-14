"""Isolation tests for the turn-scoped tool lifecycle observer."""
from __future__ import annotations

from lingtai.kernel.execution_workspace import copy_execution_context
from lingtai.kernel.turn_events import (
    ToolLifecycleEvent,
    ToolLifecycleState,
    bind_turn_tool_observer,
    current_turn_tool_observer,
    notify_tool_lifecycle,
    reset_turn_tool_observer,
)


class _Observer:
    def __init__(self, *, raises: bool = False):
        self.events = []
        self.raises = raises

    def on_tool_lifecycle(self, event):
        self.events.append(event)
        if self.raises:
            raise RuntimeError("observer failure")


def test_bind_notify_reset_and_unbound_noop():
    event = ToolLifecycleEvent("tc-1", "shell", ToolLifecycleState.STARTED)
    observer = _Observer()
    token = bind_turn_tool_observer(observer)
    try:
        assert current_turn_tool_observer() is observer
        notify_tool_lifecycle(event)
    finally:
        reset_turn_tool_observer(token)

    notify_tool_lifecycle(event)
    assert observer.events == [event]
    assert current_turn_tool_observer() is None


def test_observer_failure_is_swallowed():
    observer = _Observer(raises=True)
    token = bind_turn_tool_observer(observer)
    try:
        notify_tool_lifecycle(
            ToolLifecycleEvent("tc-1", "shell", ToolLifecycleState.FAILED)
        )
    finally:
        reset_turn_tool_observer(token)
    assert len(observer.events) == 1


def test_explicit_execution_context_copy_carries_observer():
    observer = _Observer()
    token = bind_turn_tool_observer(observer)
    try:
        copied = copy_execution_context()
    finally:
        reset_turn_tool_observer(token)

    copied.run(
        notify_tool_lifecycle,
        ToolLifecycleEvent("tc-2", "search", ToolLifecycleState.COMPLETED),
    )
    assert observer.events[0].tool_call_id == "tc-2"
