"""WorkingDir — agent working-directory layout and manifest.

The exclusive working-directory lock is no longer owned here. It moved to the
Core-owned ``lingtai.kernel.workdir_lease.WorkdirLeasePort`` and its production
``PosixWorkdirLeaseAdapter``; ``WorkdirLayout.agent_lock`` still names the
``.agent.lock`` path the adapter and read-only observers use.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK_FILE = ".agent.lock"
_MANIFEST_FILE = ".agent.json"
_MANIFEST_CORRUPT_FILE = ".agent.json.corrupt"
_HEARTBEAT_FILE = ".agent.heartbeat"
_STATUS_FILE = ".status.json"
_INIT_FILE = "init.json"
_SYSTEM_DIR = "system"
_LOGS_DIR = "logs"
_HISTORY_DIR = "history"
_NOTIFICATION_DIR = ".notification"

_RESOLVED_MANIFEST_FILE = "manifest.resolved.json"
_RESOLVED_MANIFEST_SCHEMA = "lingtai.manifest.resolved/v1"


@dataclass(frozen=True)
class WorkdirLayout:
    """Names the kernel-owned paths inside an agent working directory.

    Deliberately dumb: it only *names* paths from a root. No validation, no
    file creation, no policy — those stay in the owning modules
    (agent-presence adapters/policy, ``notifications.validate_allowed_channel``, …).
    The point is a single discoverable surface for the agent workdir
    filesystem protocol (``.agent.json``, ``.agent.heartbeat``,
    ``.notification/<channel>.json``, ``tmp/tool-results/``, …) so the same
    relative names are not retyped across runtime modules and tests.

    Internal by convention: import from ``lingtai.kernel.workdir``; not
    re-exported from ``lingtai.kernel.__init__``.
    """

    root: Path

    @property
    def agent_lock(self) -> Path:
        return self.root / _LOCK_FILE

    @property
    def agent_manifest(self) -> Path:
        return self.root / _MANIFEST_FILE

    @property
    def agent_manifest_corrupt(self) -> Path:
        return self.root / _MANIFEST_CORRUPT_FILE

    @property
    def heartbeat(self) -> Path:
        return self.root / _HEARTBEAT_FILE

    @property
    def status_json(self) -> Path:
        return self.root / _STATUS_FILE

    @property
    def init_json(self) -> Path:
        return self.root / _INIT_FILE

    @property
    def system_dir(self) -> Path:
        return self.root / _SYSTEM_DIR

    @property
    def logs_dir(self) -> Path:
        return self.root / _LOGS_DIR

    @property
    def history_dir(self) -> Path:
        return self.root / _HISTORY_DIR

    @property
    def chat_history(self) -> Path:
        return self.history_dir / "chat_history.jsonl"

    @property
    def notification_dir(self) -> Path:
        return self.root / _NOTIFICATION_DIR

    @property
    def tool_results_dir(self) -> Path:
        return self.root / "tmp" / "tool-results"

    @property
    def resolved_manifest(self) -> Path:
        return self.system_dir / _RESOLVED_MANIFEST_FILE

    @property
    def resolved_manifest_tmp(self) -> Path:
        return self.system_dir / (_RESOLVED_MANIFEST_FILE + ".tmp")

    def notification_file(self, channel: str) -> Path:
        """Path to a ``.notification/<channel>.json`` file (no validation)."""
        return self.notification_dir / f"{channel}.json"

    def system_file(self, name: str) -> Path:
        """Path to a file inside the ``system/`` directory."""
        return self.system_dir / name


def workdir_layout(path: Path | str) -> WorkdirLayout:
    """Return the :class:`WorkdirLayout` rooted at *path*.

    A ``str`` is coerced to ``Path`` so construction-bound filesystem adapters
    keep accepting string roots; anything else — a real ``Path`` or a duck-typed stand-in such as a
    test ``MagicMock`` — is stored as-is so the ``root / name`` joins stay on
    whatever object the caller passed. This mirrors the pre-helper behavior
    where producers used ``workdir / "…"`` directly without coercion.
    """
    root = Path(path) if isinstance(path, str) else path
    return WorkdirLayout(root)

# Key names that carry (or point at) secret material — dropped recursively
# before the resolved manifest is published. `*_env` names are included to
# stay consistent with the `.agent.json` `_SENSITIVE_KEYS` hygiene. The token
# alternative is anchored on `_`/edges so plural "tokens" fields
# (e.g. `max_tokens`) survive.
_SECRET_KEY_RE = re.compile(
    r"(^|_)(api_?key|secret|secrets|password|passwd|credential|credentials"
    r"|private_key|access_key|token)(_|$)",
    re.IGNORECASE,
)


def _is_secret_key(key: Any) -> bool:
    """Return whether a mapping key likely names secret material.

    Handles snake/kebab case through ``_SECRET_KEY_RE`` and common compact or
    camelCase spellings such as ``apiKey``, ``appSecret``, and ``botToken``
    without treating ordinary words like ``secretary`` or ``max_tokens`` as
    secrets.
    """
    raw = str(key)
    normalized = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    if _SECRET_KEY_RE.search(normalized):
        return True
    compact = re.sub(r"[^a-z0-9]+", "", raw.lower())
    if compact in {"apikey", "password", "passwd", "credential", "credentials", "privatekey", "accesskey"}:
        return True
    return compact.endswith("secret") or compact.endswith("token")


def _redact_secrets(value: Any) -> Any:
    """Return a deep copy of *value* with secret-bearing keys removed.

    Recurses through dicts and lists; any dict key matching
    ``_SECRET_KEY_RE`` is dropped entirely (value and all). Non-container
    leaves are returned as-is.
    """
    if isinstance(value, dict):
        return {
            k: _redact_secrets(v)
            for k, v in value.items()
            if not _is_secret_key(k)
        }
    if isinstance(value, list):
        return [_redact_secrets(v) for v in value]
    return value


def write_resolved_manifest(working_dir: Path | str, data: dict) -> Path | None:
    """Publish the kernel-resolved manifest as a derived runtime artifact.

    Writes ``<working_dir>/system/manifest.resolved.json`` from fully-resolved
    init data (after preset materialization, validation, and path resolution).
    init.json stays user-owned input; this artifact is regenerated on every
    boot/refresh, safe to delete, and is what TUI/portal consumers should read
    instead of re-implementing the preset merge over the raw snapshot
    (issue #259).

    Secrets (api_key/password/token-like fields) are removed recursively.
    The write is atomic (``.tmp`` → ``os.replace``) and best-effort: returns
    the artifact path on success, None when *data* has no manifest or the
    write failed.
    """
    manifest = data.get("manifest") if isinstance(data, dict) else None
    if not isinstance(manifest, dict):
        return None

    artifact: dict[str, Any] = {
        "schema": _RESOLVED_MANIFEST_SCHEMA,
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "kernel",
        "manifest": _redact_secrets(manifest),
    }
    preset = manifest.get("preset")
    if isinstance(preset, dict):
        artifact["preset"] = _redact_secrets(preset)

    try:
        layout = workdir_layout(working_dir)
        layout.system_dir.mkdir(parents=True, exist_ok=True)
        target = layout.resolved_manifest
        tmp = layout.resolved_manifest_tmp
        tmp.write_text(
            json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp), str(target))
        return target
    except (OSError, TypeError, ValueError):
        return None


class WorkingDir:
    """Manages an agent's working-directory layout and manifest.

    Lock authority was removed: the exclusive working-directory lease now lives
    behind ``WorkdirLeasePort`` and its ``PosixWorkdirLeaseAdapter``. This class
    still names the ``.agent.lock`` path via ``WorkdirLayout`` for the adapter and
    read-only observers, but no longer acquires or releases it.
    """

    def __init__(self, working_dir: Path | str) -> None:
        self._path = Path(working_dir)
        self._path.mkdir(parents=True, exist_ok=True)
        self._layout = workdir_layout(self._path)

    @property
    def path(self) -> Path:
        return self._path

    # --- Manifest ---

    def read_manifest(self) -> str:
        """Read the covenant from the manifest file. Returns empty string if missing."""
        path = self._layout.agent_manifest
        if not path.is_file():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data.get("covenant", "")
        except (json.JSONDecodeError, OSError):
            corrupt = self._layout.agent_manifest_corrupt
            try:
                path.rename(corrupt)
            except OSError:
                pass
            return ""

    def read_full_manifest(self) -> dict:
        """Read entire .agent.json as dict. Returns empty dict if missing or corrupt."""
        path = self._layout.agent_manifest
        if not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def write_manifest(self, manifest: dict) -> None:
        # Atomic temp-file + os.replace, UTF-8 preserved, no trailing newline.
        # Routed through the shared helper (issue #510); on-disk format is
        # byte-identical to the previous inline implementation.
        from ._fsutil import atomic_write_json

        atomic_write_json(self._layout.agent_manifest, manifest)
