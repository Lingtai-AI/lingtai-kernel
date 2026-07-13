"""POSIX `MigrationWorkspacePort` adapter, bound to one `MigrationDomain`/root.

Owns the mechanism Core does not: availability, entry→path mapping, raw reads,
preset enumeration, PID-suffixed atomic replace (every replacement, incl. preset
m001/m002), `_kernel_meta.json` version files, `system/migrations/` archive +
SHA-256 evidence, and best-effort `logs/events.jsonl` audit. Composition roots
(`Agent`, CLI) construct and inject it; Core imports none of this.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path

from lingtai.kernel.migrate import (
    MigrationArchiveKind,
    MigrationArchiveResult,
    MigrationDomain,
    MigrationEntryKind,
    MigrationEntryRef,
    MigrationWorkspaceError,
    MigrationWorkspacePort,
    MigrationWorkspaceState,
    meta_filename,
)

log = logging.getLogger(__name__)

_META_FILENAME = meta_filename()
_PRESET_SUFFIXES = (".json", ".jsonc")
_INIT_FILENAME = "init.json"
_MCP_REGISTRY_FILENAME = "mcp_registry.jsonl"


class PosixMigrationWorkspaceAdapter(MigrationWorkspacePort):
    """Filesystem Port implementation, bound to one ``(domain, root)`` pair."""

    def __init__(self, domain: MigrationDomain, root: Path):
        self._domain = domain
        self._root = Path(root)

    def _path_for(self, ref: MigrationEntryRef) -> Path:
        if ref.kind is MigrationEntryKind.INIT_DOCUMENT:
            return self._root / _INIT_FILENAME
        if ref.kind is MigrationEntryKind.MCP_REGISTRY:
            return self._root / _MCP_REGISTRY_FILENAME
        if ref.kind is MigrationEntryKind.PRESET_DOCUMENT:
            return self._root / ref.name
        raise MigrationWorkspaceError(f"unsupported entry kind: {ref.kind!r}")

    def _meta_path(self) -> Path:
        if self._domain is MigrationDomain.AGENT_WORKDIR:
            return self._root / "system" / "migrations" / _META_FILENAME
        return self._root / _META_FILENAME

    def _migrations_dir(self) -> Path:
        return self._root / "system" / "migrations"

    def _available(self) -> bool:
        if self._domain is MigrationDomain.AGENT_WORKDIR:
            # No init.json → no-op; avoid meta for a half-created workdir (else a
            # later first boot would incorrectly skip init migrations).
            return (self._root / _INIT_FILENAME).is_file()
        return self._root.is_dir()

    def _cache_key(self) -> str:
        try:
            resolved = self._root.resolve()
        except OSError:
            resolved = self._root.absolute()
        return f"{self._domain.value}:{resolved}"

    def _read_version(self) -> int:
        meta_path = self._meta_path()
        try:
            raw = meta_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return 0
        except OSError as e:
            log.warning("kernel migrate: failed to read %s: %s", meta_path, e)
            return 0
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("kernel migrate: malformed %s: %s — treating as version 0", meta_path, e)
            return 0
        if not isinstance(data, dict):
            log.warning(
                "kernel migrate: malformed %s: expected object, got %s — treating as version 0",
                meta_path, type(data).__name__,
            )
            return 0
        v = data.get("version", 0)
        return v if isinstance(v, int) else 0

    def inspect(self) -> MigrationWorkspaceState:
        return MigrationWorkspaceState(
            available=self._available(),
            cache_key=self._cache_key(),
            current_version=self._read_version(),
        )

    def enumerate_entries(self) -> tuple[MigrationEntryRef, ...]:
        # Only the preset library enumerates; agent documents are addressed by identity.
        if self._domain is not MigrationDomain.PRESET_LIBRARY:
            return ()
        if not self._root.is_dir():
            return ()
        refs: list[MigrationEntryRef] = []
        for entry in sorted(self._root.iterdir()):
            if not entry.is_file():
                continue
            if entry.suffix not in _PRESET_SUFFIXES:
                continue
            if entry.name.startswith("_"):
                continue  # internal files like _kernel_meta.json
            refs.append(
                MigrationEntryRef(MigrationEntryKind.PRESET_DOCUMENT, entry.name)
            )
        return tuple(refs)

    def read_entry(self, ref: MigrationEntryRef) -> str | None:
        try:
            return self._path_for(ref).read_text(encoding="utf-8")
        except OSError:
            return None

    def atomic_replace_entry(self, ref: MigrationEntryRef, content: str) -> None:
        path = self._path_for(ref)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(str(tmp), str(path))
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise MigrationWorkspaceError(
                f"failed to replace {ref.kind.value}: {e}"
            ) from e

    def store_version(self, version: int) -> None:
        meta_path = self._meta_path()
        payload: dict[str, object] = {"version": version}
        if self._domain is MigrationDomain.AGENT_WORKDIR:
            payload["domain"] = self._domain.value
        tmp = meta_path.with_name(f"{meta_path.name}.{os.getpid()}.tmp")
        try:
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(str(tmp), str(meta_path))
        except OSError as e:
            # A failed version write is a mechanism failure, not a silent no-op:
            # surface it as MigrationWorkspaceError so the Core treats the step as
            # unsuccessful and never advances the persisted version past stale disk.
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise MigrationWorkspaceError(
                f"failed to persist migration version {version} to {meta_path}: {e}"
            ) from e

    def archive(self, kind: MigrationArchiveKind, content: str) -> MigrationArchiveResult:
        raw = content.encode("utf-8")
        content_hash = hashlib.sha256(raw).hexdigest()
        migrations_dir = self._migrations_dir()
        archive_path = migrations_dir / f"init-{kind.value}-{content_hash}.md"
        try:
            migrations_dir.mkdir(parents=True, exist_ok=True)
            archive_path.write_text(content, encoding="utf-8")
        except OSError as e:
            raise MigrationWorkspaceError(
                f"failed to archive {kind.value}: {e}"
            ) from e
        return MigrationArchiveResult(
            relative_path=archive_path.relative_to(self._root).as_posix(),
            content_hash=content_hash, byte_length=len(raw), char_length=len(content),
        )

    def append_audit(self, event_type: str, fields: dict) -> None:
        """Best-effort append of one pre-Agent event to the agent JSONL log
        (BaseAgent._log owns the schema; this writes the same minimal shape)."""
        try:
            agent_name = None
            try:
                loaded = json.loads((self._root / _INIT_FILENAME).read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("manifest"), dict):
                    agent_name = loaded["manifest"].get("agent_name")
            except Exception:
                pass
            log_dir = self._root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            event = {
                "type": event_type, "address": self._root.name,
                "agent_name": agent_name, "ts": time.time(), **fields,
            }
            with (log_dir / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except OSError:
            pass
