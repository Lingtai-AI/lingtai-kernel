"""Typed configuration-shape Nudge backed by the real init reader outcome."""
from __future__ import annotations

from typing import Any

from ...init_reader import InitReadOutcome, InitShapeDecision


_KIND = "init_config_shape"


def check(agent, outcome: InitReadOutcome | None = None) -> None:
    """Publish or clear the config finding without reading ``init.json`` again."""
    from . import remove, upsert

    if outcome is None:
        outcome = getattr(agent, "_last_init_read_outcome", None)
    if not isinstance(outcome, InitReadOutcome):
        return

    decision = outcome.finding_decision
    if decision is InitShapeDecision.PASS:
        remove(agent, _KIND)
        return

    if decision is InitShapeDecision.NUDGE:
        title = "init.json uses a compatibility or ignored configuration shape"
        next_step = outcome.next_step or (
            "Have the Agent edit the local init.json to canonical fields, then rerun the same reader."
        )
    elif decision is InitShapeDecision.BLOCKED:
        title = "init.json configuration shape is blocked by a conflict"
        next_step = outcome.next_step or "Resolve the conflicting fields explicitly, then rerun the same reader."
    else:
        title = "init.json configuration shape could not be classified"
        next_step = outcome.next_step or "Repair the source/reader input, then rerun the same reader."

    payload = outcome.to_payload()
    detail = (
        f"Local file: {outcome.file}. Effective outcome: {outcome.status.value}; "
        f"shape={outcome.shape_decision.value}; finding={decision.value}. "
        f"Compatibility mapping: {outcome.compatibility_paths or 'none'}. "
        f"Ignored raw paths: {outcome.ignored_paths or 'none'}. "
        f"Next: {next_step}"
    )
    if decision is InitShapeDecision.BLOCKED:
        detail += f" Failure stage: {outcome.stage or 'unknown'}; behavior: {outcome.behavior}."
    elif decision is InitShapeDecision.UNKNOWN:
        detail += f" Failure stage: {outcome.stage or 'unknown'}; behavior: {outcome.behavior}."

    upsert(
        agent,
        _KIND,
        {
            "title": title,
            "detail": detail,
            "source": "init-reader",
            "local_file": outcome.file,
            "reader_status": outcome.status.value,
            "shape_decision": outcome.shape_decision.value,
            "finding_decision": decision.value,
            "compatibility_paths": list(outcome.compatibility_paths),
            "conflict_paths": list(outcome.conflict_paths),
            "ignored_paths": list(outcome.ignored_paths),
            "effective_outcome": payload,
            "next_step": next_step,
            # The shared upsert adds the effective global values and doc route;
            # retaining this explicit marker makes the contract inspectable.
            "nudge_policy": "global LINGTAI_NUDGE_ENABLED / LINGTAI_NUDGE_REPEAT_INTERVAL",
        },
    )


__all__ = ["check"]
