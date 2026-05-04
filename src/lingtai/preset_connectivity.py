"""Compatibility shim — preset connectivity moved into the kernel.

The implementation now lives at ``lingtai_kernel.preset_connectivity``. New
code should import from there directly. This shim re-exports the public
surface so older wrapper-side callers keep working unchanged.
"""
from __future__ import annotations

from lingtai_kernel.preset_connectivity import *  # noqa: F401,F403
from lingtai_kernel.preset_connectivity import (  # noqa: F401
    check_connectivity,
    check_many,
    # Private helpers — re-exported so tests that monkey-patch them via the
    # legacy ``lingtai.preset_connectivity`` module path keep working.
    _probe_host,
    _resolve_url,
)
