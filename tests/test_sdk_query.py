"""query(): yields the documented lifecycle events and honors the no-turn-loop
caveat. Uses a fake client + mock agent so no real loop or LLM call runs."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from lingtai_sdk import LingTaiOptions, query


class _FakeClient:
    def __init__(self, options):
        self.options = options
        self.agent = MagicMock()
        self.agent.agent_name = "alice"

    def create_agent(self, *, service=None, connect_mcp=False):
        return self.agent


def _collect(coro_iter):
    async def run():
        return [ev async for ev in coro_iter]

    return asyncio.run(run())


def test_query_emits_lifecycle_events_with_autostart():
    options = LingTaiOptions(working_dir="/a", agent_name="alice")
    fake = _FakeClient(options)
    events = _collect(
        query("hello", options=options, autostart=True, client=fake)
    )
    types = [e["type"] for e in events]
    assert types == ["agent_created", "started", "message_sent", "note", "stopped"]
    # Side effects on the agent.
    fake.agent.start.assert_called_once()
    fake.agent.send.assert_called_once_with("hello", sender="user")
    fake.agent.stop.assert_called_once()


def test_query_without_autostart_skips_loop_control():
    options = LingTaiOptions(working_dir="/a")
    fake = _FakeClient(options)
    events = _collect(
        query("hi", options=options, autostart=False, client=fake)
    )
    types = [e["type"] for e in events]
    assert types == ["agent_created", "message_sent", "note"]
    fake.agent.start.assert_not_called()
    fake.agent.stop.assert_not_called()
    fake.agent.send.assert_called_once_with("hi", sender="user")


def test_query_note_documents_no_turn_loop():
    options = LingTaiOptions(working_dir="/a")
    fake = _FakeClient(options)
    events = _collect(query("hi", options=options, autostart=False, client=fake))
    note = next(e for e in events if e["type"] == "note")
    assert "does not stream" in note["message"]
