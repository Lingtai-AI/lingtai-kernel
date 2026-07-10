"""Tool string catalog — en / zh / wen tables for every built-in tool.

Ownership: these are the strings the concrete tools resolve through
``lingtai_kernel.i18n.t(lang, key)`` (tool descriptions, schema field text,
manager prose). Before consolidation they were split across
``lingtai_kernel/i18n/*.json`` (the five intrinsics: ``email.*``, ``psyche.*``,
``soul.*``, ``system_tool.*``, ``notification_tool.*``) and
``lingtai/i18n/*.json`` (the wrapper tools: ``read.*``, ``write.*``, ``edit.*``,
``glob.*``, ``grep.*``, ``bash.*``, ``daemon.*``, ``avatar.*``, ``knowledge.*``,
``skills.*``, ``vision.*``, ``web_search.*``, and the shared
``tool.summary_option``). They now live here, owned by ``tools``.

Registration: on import (triggered by importing :mod:`tools.registry`), every
key from every locale table is pushed into the kernel i18n cache via
``lingtai_kernel.i18n.register_strings`` so that ``t(lang, key)`` call sites in
tool code resolve unchanged. This is additive and order-independent with the
kernel's own on-disk load: ``register_strings`` merges on top of whatever the
kernel loaded, and the kernel loads any missing keys on top of what we
registered. Machinery strings the kernel still owns (e.g.
``tool.reasoning_description``) stay in ``lingtai_kernel/i18n/*.json``.
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
    from lingtai_kernel.i18n import register_strings

    for lang in _LOCALES:
        table = _load(lang)
        if table:
            register_strings(lang, table)


_register_all()
