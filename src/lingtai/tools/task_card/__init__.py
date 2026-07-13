"""``task_card`` — the public, model-facing programmable Telegram Task Card tool.

This is the concrete tool package that owns the public ``task_card`` tool surface
(actions ``start``/``inspect``/``retry``/``stop``) and its watch lifecycle. It is
a ``lingtai.tools`` concrete tool, not kernel machinery: the kernel owns the tool
executor/guard/dispatch and the private Telegram reverse channel + automatic Task
Card driver, while this package owns the programmable-slot controller that drives
a workdir-contained Python renderer and projects validated frames onto the
resident card.

Unlike the registry capabilities in :mod:`lingtai.tools.registry`, this tool is
registered by the composition root (``Agent._maybe_setup_task_card_controller``)
only once a Telegram MCP client is present — no Telegram means no public
``task_card`` tool. See ``CONTRACT.md`` for the behavioral contract, ``ANATOMY.md``
for structure, and ``manual/SKILL.md`` for the model-facing usage manual.
"""

from __future__ import annotations

from .controller import (
    TaskCardController,
    TaskCardControllerError,
    get_description,
    get_schema,
    setup,
)

__all__ = [
    "TaskCardController",
    "TaskCardControllerError",
    "get_description",
    "get_schema",
    "setup",
]
