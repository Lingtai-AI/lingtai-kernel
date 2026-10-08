"""Pad domain — private owner of the prompt sketchboard and its pinned refs.

This package registers **no** model-facing tool. The former public ``pad`` root
and its ``append`` action were removed as a clean break: the one public root for
this domain is now ``psyche``, whose read-only ``psyche(action='pad')``
returns ``pad-manual``. There is no alias or compatibility wrapper for the old
root or for ``pad.append``.

What remains here is the domain's private ownership, unchanged:

- ``_pad_load`` is the canonical composer of the ``pad`` prompt section from
  ``system/pad.md`` plus the pinned read-only references persisted in
  ``system/pad_append.json``. ``Agent._reload_prompt_sections`` calls it inside
  the one full-context reconstruction path shared by active
  ``context.rebuild``, refresh, and molt.
- ``system/pad_append.json`` and its composition are preserved. Removing the
  public ``append`` action removed a way to *write* that list from the model
  surface, not the list itself, its on-disk source, or its rendering: an
  existing pinned reference set keeps composing into the prompt exactly as
  before.

Durable mutation of the Pad body happens through ``shell`` (a verified whole
rewrite or exact replacement); nothing hot-loads the prompt. Changes become
visible through one explicit ``context.rebuild`` or passive refresh/molt
reconstruction.
"""
from __future__ import annotations

from ._pad import _pad_load  # noqa: F401

__all__ = ["_pad_load"]
