"""Composable agent capabilities — compatibility shim (SDK-01).

The capability registry seam now lives in :mod:`lingtai_sdk.capabilities`. This
package re-exports its public surface so that every historical importer —
``from lingtai.capabilities import setup_capability`` / ``_BUILTIN`` /
``_GROUPS`` / ``CORE_DEFAULTS`` / ``expand_groups`` / ``normalize_capabilities``
/ ``apply_core_defaults`` / ``get_all_providers`` — keeps working unchanged.

This is the package, not a plain module, because optional capability
sub-packages (``vision/``, ``web_search/``) and private helpers
(``_media_host.py``, ``_zhipu_mode.py``) still live alongside it under
``lingtai.capabilities`` and are not part of the SDK file-I/O slice.
"""
from __future__ import annotations

from lingtai_sdk.capabilities import (
    CORE_DEFAULTS,
    _BUILTIN,
    _GROUPS,
    apply_core_defaults,
    expand_groups,
    get_all_providers,
    normalize_capabilities,
    setup_capability,
)

__all__ = [
    "CORE_DEFAULTS",
    "_BUILTIN",
    "_GROUPS",
    "apply_core_defaults",
    "expand_groups",
    "get_all_providers",
    "normalize_capabilities",
    "setup_capability",
]
