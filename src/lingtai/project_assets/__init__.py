"""Packaged assets the kernel writes into a *newly created* project.

Distinct from ``lingtai.prompts``: those are the kernel's own resident prompt
sources, rendered on every boot.  What lives here is operator-surface content
the kernel stamps once, at project-creation time, and never re-renders — the
agent (or the operator) owns the on-disk copy from that moment on.

Today that is exactly one asset family:

``covenant/<lang>/covenant.md``
    The Lingtai Covenant, one body per supported language (``en``/``zh``/
    ``wen``).  Ported byte-for-byte from the Go TUI's embedded
    ``tui/internal/preset/covenant/<lang>/covenant.md`` (repo
    ``Lingtai-AI/lingtai`` @ 7286da49) when project-creation semantics moved
    into the kernel.  ``lingtai.kernel.project_create`` writes the selected
    body to ``<agent_dir>/system/covenant.md`` — the mirror path
    ``prompts/covenant/covenant.yaml`` already declares.

This does *not* make the covenant a packaged prompt body: ``covenant.yaml``
still says the kernel ships no ``prompts/covenant/covenant.md`` and that the
content arrives from ``init_recipe_or_operator_surface``.  The project-create
CLI *is* one such operator surface; it seeds the mirror and then steps out of
the way.
"""
from __future__ import annotations

__all__: list[str] = []
