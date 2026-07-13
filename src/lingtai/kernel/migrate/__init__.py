"""Kernel-managed on-disk migrations — the Core package.

Per-machine analogue of `tui/internal/globalmigrate`: versioned, append-only,
forward-only migrations over kernel-owned on-disk shapes. This package owns the
domain policy (registries, current-version derivation, forward-only sequence,
success-by-success durability) and the technology-neutral
:class:`MigrationWorkspacePort` — re-exported here with its value objects —
through which every read, write, enumeration, version file, archive, and audit
append flows. It imports no ``os``/``pathlib`` and constructs no adapter; the
outside `PosixMigrationWorkspaceAdapter` owns all mechanism and composition roots
inject it. Two domains share it: preset-library and agent-workdir (incl.
`init.json`). Migrations live in `m<NNN>_<name>.py` / `agent_m<NNN>_<name>.py`;
see the paired ``CONTRACT.md`` / ``ANATOMY.md``.
"""
from __future__ import annotations

from .migrate import (
    AGENT_CURRENT_VERSION,
    CURRENT_VERSION,
    INIT_DOCUMENT_REF,
    MCP_REGISTRY_REF,
    MigrationArchiveKind,
    MigrationArchiveResult,
    MigrationDomain,
    MigrationEntryKind,
    MigrationEntryRef,
    MigrationWorkspaceError,
    MigrationWorkspacePort,
    MigrationWorkspaceState,
    meta_filename,
    run_agent_migrations,
    run_migrations,
)

__all__ = [
    "AGENT_CURRENT_VERSION",
    "CURRENT_VERSION",
    "INIT_DOCUMENT_REF",
    "MCP_REGISTRY_REF",
    "MigrationArchiveKind",
    "MigrationArchiveResult",
    "MigrationDomain",
    "MigrationEntryKind",
    "MigrationEntryRef",
    "MigrationWorkspaceError",
    "MigrationWorkspacePort",
    "MigrationWorkspaceState",
    "meta_filename",
    "run_agent_migrations",
    "run_migrations",
]
