"""Versioned migration Core for kernel-managed on-disk state.

Mirrors `tui/internal/globalmigrate`: append-only, forward-only version
registries for the on-disk shapes the kernel owns. Two domains share this
runner — preset-library migrations (run lazily from `lingtai.presets`) and
agent-workdir migrations (run before `init.json` validation).

This module is Core: it owns the version policy and forward-only sequence, not
the mechanism. Every read, write, enumeration, version file, archive, and audit
append goes through an injected :class:`MigrationWorkspacePort` (seven operation
families); the Core imports no ``os``/``pathlib`` and constructs no adapter.
Normative promises and structure live in the paired ``CONTRACT.md``/``ANATOMY.md``.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Callable, NamedTuple

from .agent_m001_init_procedures_override import migrate_init_procedures_override
from .agent_m002_mcp_launch_args_rewrite import migrate_mcp_launch_args_rewrite
from .agent_m003_init_prompt_contract import migrate_init_prompt_contract
from .m001_context_limit_relocation import migrate_context_limit_relocation
from .m002_description_object import migrate_description_object

log = logging.getLogger(__name__)

# Per-workspace migration-state filename. Underscore-internal so preset listing
# skips it; the adapter owns its on-disk placement, Core only names it.
_META_FILENAME = "_kernel_meta.json"


def meta_filename() -> str:
    """The filename `discover_presets` must skip when listing presets."""
    return _META_FILENAME


class MigrationDomain(Enum):
    """The two kernel-owned on-disk shapes the Core versions (a label, not a path)."""

    PRESET_LIBRARY = "preset"
    AGENT_WORKDIR = "agent"


class MigrationEntryKind(Enum):
    """Logical identity of a document a migration reads or replaces (never a path)."""

    PRESET_DOCUMENT = "preset_document"
    INIT_DOCUMENT = "init_document"
    MCP_REGISTRY = "mcp_registry"


class MigrationEntryRef(NamedTuple):
    """A logical document reference — kind plus an optional enumerated name."""

    kind: MigrationEntryKind
    name: str = ""


# Fixed references for the singleton agent-workdir documents (addressed by identity).
INIT_DOCUMENT_REF = MigrationEntryRef(MigrationEntryKind.INIT_DOCUMENT)
MCP_REGISTRY_REF = MigrationEntryRef(MigrationEntryKind.MCP_REGISTRY)


class MigrationWorkspaceState(NamedTuple):
    """Inspection snapshot: availability, opaque per-(domain, root) cache_key, persisted version (0 default)."""

    available: bool
    cache_key: str
    current_version: int


class MigrationArchiveKind(Enum):
    """Which retired document a migration preserves; the value is an audit label."""

    INIT_PROCEDURES = "procedures"
    INIT_SUBSTRATE = "substrate"


class MigrationArchiveResult(NamedTuple):
    """Audit-visible archive evidence: workspace-relative ``relative_path``, ``content_hash``, byte/char lengths."""

    relative_path: str
    content_hash: str
    byte_length: int
    char_length: int


class MigrationWorkspaceError(Exception):
    """Sole mechanism-boundary failure the Port raises (never a raw OS error);
    transforms record a truthful audit and abort, and the runner does not advance the version."""


class MigrationWorkspacePort(ABC):
    """Outbound Port bound at construction to one :class:`MigrationDomain` and root
    (operations take no location). Seven operation families; no generic FS/KV/path/temp surface."""

    @abstractmethod
    def inspect(self) -> MigrationWorkspaceState:
        """Return availability, an opaque cache key, and the persisted version."""

    @abstractmethod
    def enumerate_entries(self) -> tuple[MigrationEntryRef, ...]:
        """Return the domain's enumerable candidate documents (never paths)."""

    @abstractmethod
    def read_entry(self, ref: MigrationEntryRef) -> str | None:
        """Return one document's raw UTF-8 text, or None when it is absent."""

    @abstractmethod
    def atomic_replace_entry(self, ref: MigrationEntryRef, content: str) -> None:
        """Atomically replace one document's bytes with *content*."""

    @abstractmethod
    def store_version(self, version: int) -> None:
        """Persist the migration version counter for this workspace."""

    @abstractmethod
    def archive(self, kind: MigrationArchiveKind, content: str) -> MigrationArchiveResult:
        """Preserve retired *content*, returning audit-visible archive evidence."""

    @abstractmethod
    def append_audit(self, event_type: str, fields: dict) -> None:
        """Best-effort append of one pre-Agent audit event for this workspace."""


# Append-only registries. Per-process guard runs each (domain, root) at most
# once, keyed by the adapter-provided opaque cache_key.
_migrated: set[str] = set()


# Each entry: (version, name, function). Versions MUST be strictly-increasing and
# contiguous from 1 (validated at import); each transform takes the bound Port.
_PRESET_MIGRATIONS: tuple[tuple[int, str, Callable[[MigrationWorkspacePort], None]], ...] = (
    (1, "context_limit_relocation", migrate_context_limit_relocation),
    (2, "description_object", migrate_description_object),
)

_AGENT_MIGRATIONS: tuple[tuple[int, str, Callable[[MigrationWorkspacePort], None]], ...] = (
    (1, "init_procedures_override", migrate_init_procedures_override),
    (2, "mcp_launch_args_rewrite", migrate_mcp_launch_args_rewrite),
    (3, "init_prompt_contract", migrate_init_prompt_contract),
)

# Back-compat alias for tests/older callers that inspect the preset registry directly.
_MIGRATIONS = _PRESET_MIGRATIONS


def _validate_registry(
    migrations: tuple[tuple[int, str, Callable[[MigrationWorkspacePort], None]], ...] | None = None,
    *,
    domain: str = "kernel migrate",
) -> int:
    """Import-time registry sanity check; returns the highest version (the domain's
    current version). Raises RuntimeError on non-contiguity, mis-ordering, duplicate,
    or non-callable — programmer errors that must fail loudly. ``migrations`` defaults
    to ``_MIGRATIONS`` for older tests that monkeypatch that name.
    """
    if migrations is None:
        migrations = _MIGRATIONS
    if not migrations:
        return 0
    seen: set[int] = set()
    expected = 1
    for entry in migrations:
        if not (isinstance(entry, tuple) and len(entry) == 3):
            raise RuntimeError(
                f"{domain}: malformed entry {entry!r} — expected (version, name, function)"
            )
        version, name, fn = entry
        if not isinstance(version, int) or version <= 0:
            raise RuntimeError(
                f"{domain}: version must be a positive int, got {version!r} "
                f"(in {name!r})"
            )
        if version in seen:
            raise RuntimeError(f"{domain}: duplicate version {version} (in {name!r})")
        if version != expected:
            raise RuntimeError(
                f"{domain}: expected version {expected}, got {version} "
                f"(in {name!r}) — versions must be strictly increasing and contiguous"
            )
        if not callable(fn):
            raise RuntimeError(
                f"{domain}: function for version {version} ({name!r}) is not callable"
            )
        seen.add(version)
        expected += 1
    return migrations[-1][0]


CURRENT_VERSION: int = _validate_registry(_PRESET_MIGRATIONS, domain="kernel preset migrate registry")
AGENT_CURRENT_VERSION: int = _validate_registry(_AGENT_MIGRATIONS, domain="kernel agent migrate registry")


def _run_versioned_migrations(
    workspace: MigrationWorkspacePort,
    *,
    migrations: tuple[tuple[int, str, Callable[[MigrationWorkspacePort], None]], ...],
    current_version: int,
    domain_label: str,
    cache_when_unavailable: bool,
) -> None:
    """Run one registry against one workspace: ``store_version`` follows each
    successful ``fn``; a mid-run failure stops at the last success; forward-only."""
    state = workspace.inspect()
    key = state.cache_key
    if key in _migrated:
        return

    if not state.available:
        # Missing preset dir is cached; a no-init.json workdir is NOT, so it still
        # migrates if init.json appears later and writes no meta for a half-create.
        if cache_when_unavailable:
            _migrated.add(key)
        return

    current = state.current_version

    if current > current_version:
        log.warning(
            "kernel %s migrate: workspace reports version %d but this kernel only "
            "knows up to %d — honoring persisted version, running no migrations "
            "(likely a downgrade)",
            domain_label,
            current,
            current_version,
        )
        _migrated.add(key)
        return

    if current == current_version:
        _migrated.add(key)
        return

    for version, name, fn in migrations:
        if version <= current:
            continue
        # Transform + version persistence are one success unit: the abort path
        # returns before the process-cache add, so a failed store_version — now a
        # raised MigrationWorkspaceError, not a silent stale-disk no-op — leaves
        # the last durable version and does not cache the workspace, letting the
        # next launch retry this step (forward-only, no rollback).
        try:
            fn(workspace)
            workspace.store_version(version)
        except Exception as e:
            log.warning(
                "kernel %s migrate %d (%s) failed: %s — aborting run, will retry next launch",
                domain_label,
                version,
                name,
                e,
            )
            return
        current = version

    _migrated.add(key)


def run_migrations(workspace: MigrationWorkspacePort) -> None:
    """Run pending preset-library migrations against one bound workspace.

    Idempotent and process-cached by `cache_key`; a failure aborts with no
    partial advance and retries next start; a missing-directory workspace is a
    silent no-op.
    """
    _run_versioned_migrations(
        workspace,
        migrations=_PRESET_MIGRATIONS,
        current_version=CURRENT_VERSION,
        domain_label="preset",
        cache_when_unavailable=True,
    )


def run_agent_migrations(workspace: MigrationWorkspacePort) -> None:
    """Run pending agent-workdir migrations against one bound workspace.

    Call before reading/validating `init.json` so boot and refresh see the
    migrated shape. No `init.json` → unavailable, uncached no-op, so a
    half-created workdir never gets version meta and still migrates on first boot.
    """
    _run_versioned_migrations(
        workspace,
        migrations=_AGENT_MIGRATIONS,
        current_version=AGENT_CURRENT_VERSION,
        domain_label="agent",
        cache_when_unavailable=False,
    )


def reset_process_cache() -> None:
    """Test-only: clear the per-process migration guard (not public API)."""
    _migrated.clear()
