"""Deprecated compatibility wrapper for the renamed library capability.

The durable knowledge capability formerly lived at ``lingtai.core.codex`` and
registered the ``codex`` tool. It is now ``lingtai.core.library`` and registers
``library`` plus a temporary ``codex`` tool alias. Keep this module so imports
and direct capability setup through the old module path continue to work during
migration.
"""
from __future__ import annotations

from lingtai.core.library import (  # noqa: F401
    LibraryManager,
    LibraryManager as CodexManager,
    PROVIDERS,
    get_description,
    get_schema,
    setup,
)
