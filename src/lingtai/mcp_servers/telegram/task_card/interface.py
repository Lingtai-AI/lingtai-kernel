"""Narrow host-agent interface for the programmable Telegram Task Card controller.

The controller drives the *programmable* slot of the single resident Telegram
Task Card, but it must not import or name the concrete outer ``Agent``
(``src/lingtai/agent.py``) or the kernel ``BaseAgent``. This ``Protocol`` is the
only surface the controller depends on; the real agent satisfies it
*structurally*, so the unit stays decoupled from the composition root and is
trivially faked in tests.

Every member below is read defensively (``getattr`` with a default) at call
time, so a host that omits an optional member simply disables that path — e.g.
a host without ``_enqueue_system_notification`` drops fail-loud wakes rather than
crashing a watcher thread.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Callable, Protocol


class TelegramTaskCardAgent(Protocol):
    """The minimal agent surface the Task Card controller consumes.

    See ``CONTRACT.md`` (the Port section) for the normative promise and
    ``ANATOMY.md`` for how the concrete ``Agent`` supplies each member.
    """

    #: Absolute agent working directory — renderer-path confinement root and the
    #: subprocess ``cwd`` for every renderer run.
    _working_dir: str | os.PathLike[str]
    #: Stable ``tool_name -> MCP client`` map built at MCP registration time; the
    #: ``"telegram"`` entry is the private Task Card reverse channel.
    _mcp_clients_by_tool: dict[str, Any]
    #: Turn-local automatic-driver route (``{"account": str, "chat_id": int, ...}``)
    #: or ``None`` when no Telegram turn is active. Both slots share this route so
    #: they compose into one resident message.
    _telegram_task_card_context: dict | None
    #: Set at agent teardown so watcher loops exit promptly (optional).
    _shutdown: threading.Event

    def add_tool(
        self,
        name: str,
        *,
        schema: dict,
        handler: Callable[[dict], dict],
        description: str = ...,
        glossary_package: Any = ...,
    ) -> Any:
        """Register a model-facing tool (used once to add ``task_card``)."""
        ...

    def _enqueue_system_notification(self, **kwargs: Any) -> Any:
        """Publish a deduped durable system-notification wake (optional)."""
        ...
