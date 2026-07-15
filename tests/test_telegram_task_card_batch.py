"""Retired: BaseAgent batch/rows/heartbeat orchestration for the Task Card.

This file used to test the BaseAgent-owned rolling-window/heartbeat/env-parser
machinery (``_on_tool_pre_dispatch_hook`` row batching, ``_task_card_max_tool_rows``,
the 0.5s heartbeat thread, and BaseAgent-side teardown finalize). All of it was
retired: the automatic Telegram Task Card is now a mechanical, bounded broadcast
of the agent's authoritative ``logs/events.jsonl``, owned entirely by
``TelegramManager`` (see ``mcp_servers/telegram/manager.py``'s event-tail worker),
not a turn-local BaseAgent callback/heartbeat model. There is no rolling window of
*rendered* rows to freeze/tick/cap on the BaseAgent side anymore — the manager's
own bounded latest-N window (``TelegramManager._TASK_CARD_EVENT_WINDOW``) replaces
it, and it is exercised in ``tests/test_telegram_task_card_event_tail.py``
(latest-N order, safe-field projection, broadcast to every resident target,
restart rehydration, no BaseAgent dependency).

Kept as an empty module (not deleted) so history and intent stay discoverable at
this path.
"""

from __future__ import annotations
