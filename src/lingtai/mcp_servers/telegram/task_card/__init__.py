"""Telegram-owned programmable Task Card unit.

The model-facing ``task_card`` tool drives the *programmable* slot of Telegram's
one tracked resident Task Card target, composed alongside the automatic
tool-activity slot into that one tracked resident owned by ``TelegramManager``
(Jason #7258/#7259). This capability is Telegram MCP-owned: registration is gated
by the Telegram reverse route, projection targets ``_lingtai_telegram_task_card``,
and rendering, in-place edits, the ``/taskcard`` toggle, persistence, and the
hard-at-most-one / last-message transport all live in the Telegram
manager/server/service. There is no cross-channel port and no second
implementation.

Public surface (intentionally small):

- ``setup(agent)`` — the composition-root hook that registers the ``task_card``
  tool once a Telegram reverse channel exists.
- ``TaskCardController`` / ``TaskCardControllerError`` — the controller and its
  synchronous user-visible error.
- ``get_schema`` / ``get_description`` — the tool schema and description.
- ``TelegramTaskCardAgent`` — the narrow host-agent Protocol the controller
  depends on instead of the concrete ``Agent`` class.

See ``SKILL.md`` for the manual (what/how/why), ``CONTRACT.md`` for the interface
promise, and ``ANATOMY.md`` for the structure.
"""

from __future__ import annotations

from .controller import (
    TaskCardController,
    TaskCardControllerError,
    get_description,
    get_schema,
    setup,
)
from .interface import TelegramTaskCardAgent
from .resident import TaskCardResident

__all__ = [
    "TaskCardController",
    "TaskCardResident",
    "TaskCardControllerError",
    "TelegramTaskCardAgent",
    "get_description",
    "get_schema",
    "setup",
]
