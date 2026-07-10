"""Kernel i18n — language-aware string tables.

Usage: t(lang, key, **kwargs)
  lang: language code ("en", "zh", "wen")
  key: dotted string ID ("system.current_time")
  kwargs: template substitutions

The kernel ships en.json (English), zh.json (中文), and wen.json (文言).
Additional languages can be registered by the lingtai package via
register_strings(). Unknown language falls back to English. Unknown
key returns the key itself.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIR = Path(__file__).parent
_CACHE: dict[str, dict[str, str]] = {}
# Languages whose on-disk kernel catalog has already been merged into _CACHE.
# Tracked separately from _CACHE membership so that a language pre-populated by
# ``register_strings`` (e.g. the tools string catalog registering on import,
# before any kernel t() call) does NOT suppress the disk load of the kernel's
# own machinery strings. Cache-presence is not "fully loaded"; disk-loaded is.
_DISK_LOADED: set[str] = set()


def _load(lang: str) -> dict[str, str]:
    """Load the kernel catalog for *lang* (once) and return the merged table.

    The disk file is merged under any already-registered keys — registered
    strings (translations/tool catalogs injected via ``register_strings``) win
    over the kernel's on-disk defaults for the same key, matching the prior
    "register overrides disk" semantics. Returns the (possibly empty) table.
    """
    table = _CACHE.setdefault(lang, {})
    if lang not in _DISK_LOADED:
        _DISK_LOADED.add(lang)
        path = _DIR / f"{lang}.json"
        if path.is_file():
            disk = json.loads(path.read_text(encoding="utf-8"))
            # Registered keys take precedence: only fill in keys not already set.
            for k, v in disk.items():
                table.setdefault(k, v)
    return table


def register_strings(lang: str, strings: dict[str, str]) -> None:
    """Register (or extend) a language's string table.

    Called by the tools string catalog and by lingtai to inject strings (tool
    catalogs, non-English translations) into the kernel's cache so that
    kernel-level ``t()`` calls resolve them. Merges into any existing entries
    for the language; registered strings override on-disk kernel defaults for
    the same key.
    """
    table = _CACHE.setdefault(lang, {})
    table.update(strings)


def t(lang: str, key: str, **kwargs) -> str:
    """Translate a key. Falls back to English, then returns the key itself.

    Extra kwargs not referenced in the template are silently ignored
    (needed because en/zh templates may use different subsets of kwargs).
    """
    from collections import defaultdict

    table = _load(lang)
    value = table.get(key)
    if value is None and lang != "en":
        value = _load("en").get(key)
    if value is None:
        return key
    if kwargs:
        return value.format_map(defaultdict(str, kwargs))
    return value
