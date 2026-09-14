"""LingTai domain — private owner of the agent's 灵台 (character) composition.

This package registers **no** model-facing tool. The former public ``lingtai``
root was removed as a clean break: the one public root for this domain is now
``psyche``, whose read-only ``psyche(action='lingtai')`` returns
``lingtai-manual``. There is no alias or compatibility wrapper for the old root.

``_lingtai_load`` remains the private canonical composer of the protected
``character`` prompt section from ``system/lingtai.md``.
``Agent._reload_prompt_sections`` calls it inside the one full-context
reconstruction path shared by active ``context.rebuild``, refresh, and molt,
after materializing any configured ``lingtai`` / resolved ``lingtai_file`` value
into that durable file.

Durable identity mutation happens through ``shell`` (a verified full rewrite or
exact replacement); nothing hot-loads the prompt.
"""
from __future__ import annotations

from ._lingtai import _lingtai_load  # noqa: F401

__all__ = ["_lingtai_load"]
