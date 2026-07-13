"""Shared helpers for the migration-workspace tests.

Two jobs:

1. **Composition wrappers** — the Core migration runner and the preset library
   functions now require an injected workspace/runner/loader with *no*
   production default. These thin wrappers build the production POSIX adapter
   (or reuse the wrapper preset-loader) so the many existing behavior tests keep
   passing exact positional call sites without each one re-wiring composition.
2. **A fake `MigrationWorkspacePort`** — an in-memory implementation used by the
   seven-family conformance suite so the production adapter and a substitute are
   proven against the same Port contract.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from lingtai.adapters.posix.migration_workspace import PosixMigrationWorkspaceAdapter
from lingtai.kernel.migrate import (
    MigrationArchiveKind,
    MigrationArchiveResult,
    MigrationDomain,
    MigrationEntryKind,
    MigrationEntryRef,
    MigrationWorkspacePort,
    MigrationWorkspaceState,
    meta_filename,
    run_agent_migrations as _core_run_agent_migrations,
    run_migrations as _core_run_migrations,
)

META_FILENAME = meta_filename()

# Agent-domain version-meta location relative to the workdir. Kept here (not in
# Core, which must not expose a Path) so tests can locate the on-disk file with
# the historical ``tmp_path / agent_meta_relative_path()`` pattern.
_AGENT_META_REL = Path("system") / "migrations" / META_FILENAME


def agent_meta_relative_path() -> Path:
    """Relative path of the agent-workdir version-meta file (test convenience)."""
    return _AGENT_META_REL


def agent_meta_path(workdir: Path | str) -> Path:
    """Absolute path of the agent-workdir version-meta file under *workdir*."""
    return Path(workdir) / _AGENT_META_REL


# ---------------------------------------------------------------------------
# Production adapter constructors + Core-runner wrappers
# ---------------------------------------------------------------------------


def preset_workspace(path: Path | str) -> PosixMigrationWorkspaceAdapter:
    """Production preset-library workspace bound to *path*."""
    return PosixMigrationWorkspaceAdapter(MigrationDomain.PRESET_LIBRARY, Path(path))


def agent_workspace(path: Path | str) -> PosixMigrationWorkspaceAdapter:
    """Production agent-workdir workspace bound to *path*."""
    return PosixMigrationWorkspaceAdapter(MigrationDomain.AGENT_WORKDIR, Path(path))


def run_migrations(path: Path | str) -> None:
    """Run preset-library migrations against a directory via the POSIX adapter."""
    _core_run_migrations(preset_workspace(path))


def run_agent_migrations(path: Path | str) -> None:
    """Run agent-workdir migrations against a workdir via the POSIX adapter."""
    _core_run_agent_migrations(agent_workspace(path))


def _preset_library_runner(directory: Path) -> None:
    _core_run_migrations(preset_workspace(directory))


def load_preset(name: str, working_dir: Path | None = None) -> dict:
    """Core ``load_preset`` wired to the POSIX preset-library migration runner."""
    from lingtai.kernel.presets import load_preset as _core

    return _core(name, working_dir=working_dir, run_migrations=_preset_library_runner)


def discover_presets_in_dirs(dirs) -> dict:
    """Core ``discover_presets_in_dirs`` wired to the POSIX runner."""
    from lingtai.kernel.presets import discover_presets_in_dirs as _core

    return _core(dirs, run_migrations=_preset_library_runner)


discover_presets = discover_presets_in_dirs


def materialize_active_preset(data, working_dir, core_defaults=None) -> None:
    """Core ``materialize_active_preset`` wired to the POSIX preset-loader."""
    from lingtai.kernel.presets import materialize_active_preset as _core

    return _core(data, working_dir, core_defaults=core_defaults, load_preset=load_preset)


# ---------------------------------------------------------------------------
# Fake workspace (conformance substitute)
# ---------------------------------------------------------------------------

# Fixed filenames the adapter maps the singleton agent documents to; the fake
# keys its in-memory store by the same names so seeding is backend-uniform.
_INIT_FILENAME = "init.json"
_MCP_REGISTRY_FILENAME = "mcp_registry.jsonl"
_PRESET_SUFFIXES = (".json", ".jsonc")


class FakeMigrationWorkspace(MigrationWorkspacePort):
    """In-memory `MigrationWorkspacePort` for seven-family conformance.

    Reproduces the observable Port semantics — candidate enumeration, raw text
    reads, atomic replace, version persistence, and archive SHA/relative-path
    evidence — without any filesystem, so the production adapter and this
    substitute can be asserted against the same contract.
    """

    def __init__(
        self,
        domain: MigrationDomain,
        *,
        entries: dict[str, str] | None = None,
        version: int = 0,
        available: bool | None = None,
        cache_key: str | None = None,
    ):
        self._domain = domain
        self._entries: dict[str, str] = dict(entries or {})
        self._version = version
        self._available = available
        self._cache_key = cache_key or f"fake:{domain.value}:{id(self)}"
        self.archives: list[tuple[MigrationArchiveKind, str, MigrationArchiveResult]] = []
        self.audit_events: list[tuple[str, dict]] = []
        self.replacements: list[tuple[MigrationEntryRef, str]] = []

    def _key(self, ref: MigrationEntryRef) -> str:
        if ref.kind is MigrationEntryKind.INIT_DOCUMENT:
            return _INIT_FILENAME
        if ref.kind is MigrationEntryKind.MCP_REGISTRY:
            return _MCP_REGISTRY_FILENAME
        return ref.name

    def _availability(self) -> bool:
        if self._available is not None:
            return self._available
        if self._domain is MigrationDomain.AGENT_WORKDIR:
            return _INIT_FILENAME in self._entries
        return True

    def inspect(self) -> MigrationWorkspaceState:
        return MigrationWorkspaceState(
            available=self._availability(),
            cache_key=self._cache_key,
            current_version=self._version,
        )

    def enumerate_entries(self) -> tuple[MigrationEntryRef, ...]:
        if self._domain is not MigrationDomain.PRESET_LIBRARY:
            return ()
        refs: list[MigrationEntryRef] = []
        for name in sorted(self._entries):
            if name.startswith("_"):
                continue
            if not name.endswith(_PRESET_SUFFIXES):
                continue
            refs.append(MigrationEntryRef(MigrationEntryKind.PRESET_DOCUMENT, name))
        return tuple(refs)

    def read_entry(self, ref: MigrationEntryRef) -> str | None:
        return self._entries.get(self._key(ref))

    def atomic_replace_entry(self, ref: MigrationEntryRef, content: str) -> None:
        self._entries[self._key(ref)] = content
        self.replacements.append((ref, content))

    def store_version(self, version: int) -> None:
        self._version = version

    def archive(self, kind: MigrationArchiveKind, content: str) -> MigrationArchiveResult:
        raw = content.encode("utf-8")
        content_hash = hashlib.sha256(raw).hexdigest()
        result = MigrationArchiveResult(
            relative_path=f"system/migrations/init-{kind.value}-{content_hash}.md",
            content_hash=content_hash,
            byte_length=len(raw),
            char_length=len(content),
        )
        self.archives.append((kind, content, result))
        return result

    def append_audit(self, event_type: str, fields: dict) -> None:
        self.audit_events.append((event_type, dict(fields)))
