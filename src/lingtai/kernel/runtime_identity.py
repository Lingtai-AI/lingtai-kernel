"""Compact kernel runtime identity for durable event logs.

The values here are intentionally cheap and local: they let a later reader of
``logs/events.jsonl`` tell which LingTai kernel/runtime produced an event without
contacting PyPI or trusting out-of-band process state.
"""
from __future__ import annotations

import json
import re
from importlib import metadata
from pathlib import Path
from typing import Any

from .snapshot import SourceRevisionPort

_KERNEL_PACKAGE = "lingtai"
_RUNTIME_IDENTITY_CACHE: dict[str, Any] | None = None


def runtime_identity_event_fields(
    source_revision_port: SourceRevisionPort,
) -> dict[str, Any]:
    """Return fields stamped onto each agent event-log row."""

    identity = runtime_identity(source_revision_port)
    return {
        "kernel_version": identity["version"],
        "kernel_runtime_stamp": identity["stamp"],
        "kernel_runtime": identity,
    }


def runtime_identity(source_revision_port: SourceRevisionPort) -> dict[str, Any]:
    """Return a process-cached, JSON-serializable identity for this runtime.

    Release/package installs are identified by package version. Editable/source
    checkouts also carry a git commit/dirty flag when available; if git is not
    available, the stamp falls back to the helper module's mtime.
    """

    global _RUNTIME_IDENTITY_CACHE
    if _RUNTIME_IDENTITY_CACHE is not None:
        return _RUNTIME_IDENTITY_CACHE

    module_path = Path(__file__).resolve()
    source_root = _source_root(module_path)
    dist = _distribution()

    installed_version = str(dist.version) if dist is not None else None
    pyproject_version = _pyproject_version(source_root)

    source = _source_kind(dist, module_path, installed_version or pyproject_version or "unknown")
    mode = "dev" if source != "package" else "package"
    # Editable/source checkouts can have stale installed metadata after a local
    # fast-forward. In dev mode, prefer the checkout's pyproject version while
    # retaining installed_version as diagnostic context.
    if mode == "dev" and pyproject_version:
        version = pyproject_version
    else:
        version = installed_version or pyproject_version or "unknown"
    git_commit, git_dirty = _git_state(source_root, source_revision_port)

    if git_commit:
        stamp = f"{version}+git.{git_commit[:12]}"
        if git_dirty:
            stamp += ".dirty"
    elif mode == "dev":
        try:
            stamp = f"{version}+source.{int(module_path.stat().st_mtime)}"
        except OSError:
            stamp = f"{version}+source"
    else:
        stamp = version

    identity: dict[str, Any] = {
        "version": version,
        "stamp": stamp,
        "mode": mode,
        "source": source,
    }
    if installed_version is not None:
        identity["installed_version"] = installed_version
    if git_commit is not None:
        identity["git_commit"] = git_commit
    if git_dirty is not None:
        identity["git_dirty"] = git_dirty
    _RUNTIME_IDENTITY_CACHE = identity
    return identity


def _clear_runtime_identity_cache() -> None:
    global _RUNTIME_IDENTITY_CACHE
    _RUNTIME_IDENTITY_CACHE = None


# Preserve the established test/diagnostic cache-reset surface.
runtime_identity.cache_clear = _clear_runtime_identity_cache  # type: ignore[attr-defined]


def _distribution():
    try:
        return metadata.distribution(_KERNEL_PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def _source_kind(dist: Any, module_path: Path, version: str) -> str:
    if dist is None:
        return "source-checkout"
    if _direct_url_is_editable(dist):
        return "editable"
    if _looks_like_dev_version(version):
        return "dev-version"
    if _module_from_source_checkout(module_path):
        return "source-checkout"
    return "package"


def _direct_url_is_editable(dist: Any) -> bool:
    try:
        raw = dist.read_text("direct_url.json")
    except Exception:
        return False
    if not raw:
        return False
    try:
        data = json.loads(raw)
    except Exception:
        return False
    return bool(data.get("dir_info", {}).get("editable"))


def _looks_like_dev_version(version: str) -> bool:
    v = (version or "").lower()
    return ".dev" in v or "+" in v or "editable" in v


def _module_from_source_checkout(module_path: Path) -> bool:
    if any(part in {"site-packages", "dist-packages"} for part in module_path.parts):
        return False
    return _source_root(module_path) is not None


def _source_root(path: Path) -> Path | None:
    for parent in path.parents:
        if (parent / "pyproject.toml").exists() and (parent / ".git").exists():
            return parent
    return None


def _pyproject_version(source_root: Path | None) -> str | None:
    if source_root is None:
        return None
    try:
        text = (source_root / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'(?m)^version\s*=\s*["\']([^"\']+)["\']', text)
    return match.group(1) if match else None


def _git_state(
    source_root: Path | None,
    source_revision_port: SourceRevisionPort,
) -> tuple[str | None, bool | None]:
    if source_root is None:
        return None, None
    commit = source_revision_port.current_revision(12, 0.5)
    if not commit:
        return None, None
    return commit, source_revision_port.is_dirty(0.5)
