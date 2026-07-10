"""Tool locale catalog — en / zh / wen tables for runtime manager prose.

Ownership: these catalogs hold the *human-facing manager prose* that concrete
tools resolve through ``lingtai.kernel.i18n.t(lang, key)`` — runtime
preambles, prompts, and system messages such as ``soul.system_prompt``,
``psyche.context_forget_summary``, ``knowledge.preamble``, and
``email.unread_digest``.

They do **not** own model-facing schema or description text. Tool descriptions
and schema-field text were moved out of these catalogs: they now live in the
tool packages' canonical English source code and per-package glossary
resources (``glossary-{en,zh,wen}.md``). The kernel reasoning-description key
(``tool.reasoning_description``) is a language-independent constant in
``lingtai/kernel/base_agent/tools.py``; it no longer resides in any catalog.

Before consolidation the manager prose was split across
``lingtai/kernel/i18n/*.json`` (the five intrinsics: ``email.*``, ``psyche.*``,
``soul.*``, ``system_tool.*``, ``notification_tool.*``) and
``lingtai/i18n/*.json`` (the wrapper tools: ``read.*``, ``write.*``, ``edit.*``,
``glob.*``, ``grep.*``, ``bash.*``, ``daemon.*``, ``avatar.*``, ``knowledge.*``,
``skills.*``, ``vision.*``, ``web_search.*``, and the shared
``tool.summary_option``). They now live here, owned by ``tools``.

Registration: on import (triggered by importing :mod:`tools.registry`), every
key from every locale table is pushed into the kernel i18n cache via
``lingtai.kernel.i18n.register_strings`` so that ``t(lang, key)`` call sites in
tool code resolve unchanged. This is additive and order-independent with the
kernel's own on-disk load.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).parent
_LOCALES = ("en", "zh", "wen")


def _load(lang: str) -> dict[str, str]:
    path = _DIR / f"{lang}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _register_all() -> None:
    """Push every tool string into the kernel i18n cache."""
    from lingtai.kernel.i18n import register_strings

    for lang in _LOCALES:
        table = _load(lang)
        if table:
            register_strings(lang, table)


_register_all()
