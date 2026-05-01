"""Involuntary tool-call inbox — queue of synthetic (call, result) pairs.

The agent's wire chat normally only contains tool calls the agent itself made.
Some events fire mechanically — soul flow on a cadence, scheduled wakeups,
periodic system pings — and the cleanest way to surface them in the agent's
history is as synthetic ``(ToolCallBlock, ToolResultBlock)`` pairs that look
like real tool calls the agent didn't initiate.

Producers (background timer threads) build fully-formed pairs and enqueue
them here. The agent's main run loop drains the queue at safe wire-chat
boundaries — when the chat tail has no unanswered tool_calls and no other
turn is mid-flight — and splices each pair into the wire chat.

Coalescing: producers can mark items ``coalesce=True`` and supply a ``source``
key. On enqueue, any existing item with the same source is replaced. Used by
soul flow so multiple firings during a busy stretch collapse to one
reflection (the latest voice wins) rather than spamming the agent with stale
back-to-back pairs when it next reaches a safe boundary.

Thread safety: the queue is a list guarded by a single Lock. Producers run
on background timer threads; the drain runs on the main agent thread. The
lock protects enqueue / drain / coalesce-replace; the drain copies the list
under the lock then splices outside it so chat-interface mutations don't
hold the lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .llm.interface import ToolCallBlock, ToolResultBlock


@dataclass
class InvoluntaryToolCall:
    """One synthetic tool-call pair queued for splicing into the wire chat."""

    call: "ToolCallBlock"
    result: "ToolResultBlock"
    source: str               # e.g. "soul.flow", "system.wakeup"
    enqueued_at: float        # time.time() at enqueue
    coalesce: bool = False    # if True, replace prior item with same source


class TCInbox:
    """Thread-safe queue of involuntary tool-call pairs."""

    def __init__(self) -> None:
        self._items: list[InvoluntaryToolCall] = []
        self._lock = threading.Lock()

    def enqueue(self, item: InvoluntaryToolCall) -> None:
        """Add an item.

        If ``item.coalesce`` is True, replace any existing item with the same
        ``source`` key (in place, preserving order). Otherwise append.
        """
        with self._lock:
            if item.coalesce:
                for i, existing in enumerate(self._items):
                    if existing.source == item.source:
                        self._items[i] = item
                        return
            self._items.append(item)

    def drain(self) -> list[InvoluntaryToolCall]:
        """Atomically remove and return all queued items in FIFO order."""
        with self._lock:
            items = self._items
            self._items = []
        return items

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
