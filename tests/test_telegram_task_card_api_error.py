"""Retired: LLM/provider API errors surfaced into the automatic Task Card.

This file used to test ``BaseAgent._report_task_card_api_error`` /
``_recover_task_card_api_error`` — the turn-local mechanism that upserted a
sanitized API-error row into the automatic Task Card's rolling window and
reverse-called the Telegram MCP manager to render it.

Human contract for this feature (Telegram 8266-8295): the automatic slot
whitelists exactly ``tool_call`` events from ``logs/events.jsonl`` and does not
inspect ``tool_result``/dispatch events or reconstruct completion, elapsed
time, or API-error state — that machinery, including this file's whole
premise, is intentionally out of scope and was retired along with the
BaseAgent-owned row/heartbeat model (see ``mcp_servers/telegram/manager.py``'s
event-tail worker and ``tests/test_telegram_task_card_event_tail.py``).

Kept as an empty module (not deleted) so history and intent stay discoverable
at this path.
"""

from __future__ import annotations
