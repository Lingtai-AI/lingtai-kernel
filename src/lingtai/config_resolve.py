"""Compatibility shim — config-resolution helpers moved into the kernel.

The implementation now lives at ``lingtai_kernel.config_resolve``. New code
should import from there directly. This shim re-exports the public surface
so older wrapper-side callers keep working unchanged.
"""
from __future__ import annotations

from lingtai_kernel.config_resolve import *  # noqa: F401,F403
from lingtai_kernel.config_resolve import (  # noqa: F401
    load_jsonc,
    resolve_env,
    load_env_file,
    resolve_file,
    resolve_paths,
    # Private helpers — re-exported so tests that import them via the
    # legacy ``lingtai.config_resolve`` module path keep working.
    _resolve_env_fields,
    _resolve_file_fields,
    _resolve_capabilities,
)
