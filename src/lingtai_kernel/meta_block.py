"""Unified per-turn metadata injection.

Single source of truth for "what the agent sees about its own runtime state
on every turn." Both injection sites — text-input prefix (in BaseAgent) and
tool-result stamp (in ToolExecutor) — read from here.

Curate carefully: every field added to `build_meta` ships on every text input
and every tool result.
"""
from __future__ import annotations

from .i18n import t as _t
from .time_veil import now_iso


def build_meta(agent) -> dict:
    """Return the current meta-data snapshot for the agent.

    Respects ``agent._config.time_awareness`` / ``timezone_awareness``
    internally; callers never need to special-case those flags.

    When the agent is time-blind and no other meta fields are curated in,
    returns ``{}``.
    """
    meta: dict = {}
    ts = now_iso(agent)
    if ts:
        meta["current_time"] = ts
    return meta


def render_meta(agent, meta: dict) -> str:
    """Render the meta dict as the line prepended to text input.

    Returns '' when the meta dict is empty — callers should treat '' as
    "no prefix" and skip concatenation.

    Today this only knows how to render ``current_time`` (via the existing
    ``system.current_time`` i18n key). Future fields are composed here.
    """
    if not meta:
        return ""
    if "current_time" in meta:
        return _t(agent._config.language, "system.current_time", time=meta["current_time"])
    return ""
