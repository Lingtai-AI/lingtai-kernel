"""Kernel runtime/update nudges.

This check is deliberately read-only. It exists to surface mechanical runtime
facts through the shared ``nudge`` notification channel, not to mutate the
installation by itself.

Two related cases share one nudge ``kind``:

* local refresh available: the installed ``lingtai`` distribution on disk is
  newer than the currently running process, so a safe ``system.refresh`` may
  load code that is already present. The agent starts at
  ``https://lingtai.ai/skill.md`` to determine applicable release migrations
  before any authorized configuration write or refresh;
* package update available: a packaged, non-editable runtime is behind the
  latest published ``lingtai`` kernel package. This is checked at most once per
  UTC day per agent and starts at ``https://lingtai.ai/skill.md`` so the agent
  can determine applicable release migrations, obtain explicit
  human/config-owner authorization for every migration/config write and refresh,
  apply only authorized writes, validate, and refresh last. The nudge itself
  grants no authority.

Editable/source/dev installs are skipped for the package-update check: their
source of truth is the checkout, not the package index.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .prompts import NudgeFacts, NudgeSituation, SKILL_ROUTE, render_nudge_payload

_FAST_INTERVAL_SECONDS = 60.0
_REMOTE_TIMEOUT_SECONDS = 3.0
_PYPI_JSON_URL = "https://pypi.org/pypi/lingtai/json"
_STATE_FILE = Path(".notification") / ".nudge_state.json"
_KIND = "kernel_version"
# Backward-compatible module constant for callers that inspected the old hint.
_SKILL_HINT = SKILL_ROUTE


@dataclass(frozen=True)
class _RuntimeInfo:
    running_version: str
    installed_version: str
    dev_reason: str | None = None

    @property
    def dev_mode(self) -> bool:
        return self.dev_reason is not None


def check(agent) -> None:
    """Emit or clear the kernel-version nudge for ``agent``."""

    state = _state(agent)
    now = time.time()
    if now - float(state.get("last_probe_ts") or 0.0) < _FAST_INTERVAL_SECONDS:
        return
    state["last_probe_ts"] = now

    try:
        from . import remove, upsert

        info = _runtime_info()
    except Exception as e:  # pragma: no cover - defensive: nudge must be inert
        _log(agent, "kernel_version_probe_error", error=str(e)[:200])
        return

    if info.dev_mode:
        remove(agent, _KIND)
        _store_kernel_state(
            agent,
            {
                "last_skip_date": _today_utc(),
                "skip_reason": info.dev_reason,
                "checked_installed_version": info.installed_version,
                "last_error": None,
            },
        )
        return

    if info.installed_version != info.running_version:
        # Emit the local-refresh mismatch nudge at most once per UTC day for a
        # given version pair. The fast 60s probe keeps first emission prompt,
        # but once the agent dismisses the notification (deleting nudge.json)
        # we must not re-create it on the next tick: re-creating changes the
        # notification fingerprint and triggers an endless wake loop ("nudge
        # storm") that starves real work — including incoming email processing.
        # The daily cadence still lets a persistent mismatch resurface once the
        # next UTC day begins, matching the package-update path.
        today = _today_utc()
        mismatch_key = f"{info.running_version}->{info.installed_version}"
        persistent = _load_persistent_state(agent)
        kernel_state = persistent.setdefault(_KIND, {})
        if (
            kernel_state.get("emitted_for_mismatch") == mismatch_key
            and kernel_state.get("mismatch_emitted_date") == today
        ):
            return
        kernel_state["emitted_for_mismatch"] = mismatch_key
        kernel_state["mismatch_emitted_date"] = today
        _save_persistent_state(agent, persistent)

        upsert(
            agent,
            _KIND,
            render_nudge_payload(
                NudgeSituation.INSTALLED_RUNTIME_MISMATCH,
                NudgeFacts(
                    running=info.running_version,
                    installed=info.installed_version,
                    checked_at_date=today,
                ),
            ),
        )
        _log(
            agent,
            "nudge_emitted",
            kind=_KIND,
            running=info.running_version,
            installed=info.installed_version,
            source="installed-distribution",
        )
        return

    persistent = _load_persistent_state(agent)
    kernel_state = persistent.setdefault(_KIND, {})

    # Versions match — clear any stale mismatch tracking so a future
    # mismatch (e.g. another upgrade) will emit the nudge again.
    cleared_mismatch = kernel_state.pop("emitted_for_mismatch", None) is not None
    cleared_mismatch = kernel_state.pop("mismatch_emitted_date", None) is not None or cleared_mismatch
    if cleared_mismatch:
        from . import remove
        remove(agent, _KIND)
        _save_persistent_state(agent, persistent)

    today = _today_utc()
    if not _remote_check_due(kernel_state, info.installed_version, today):
        return

    try:
        latest = _fetch_latest_version()
    except Exception as e:
        kernel_state.update(
            {
                "last_remote_check_date": today,
                "checked_installed_version": info.installed_version,
                "last_error": str(e)[:200],
            }
        )
        _save_persistent_state(agent, persistent)
        _log(agent, "kernel_version_update_check_error", error=str(e)[:200])
        return

    kernel_state.update(
        {
            "last_remote_check_date": today,
            "checked_installed_version": info.installed_version,
            "latest_seen": latest,
            "last_error": None,
        }
    )

    if _is_newer(latest, info.installed_version):
        kernel_state["emitted_for_latest"] = latest
        _save_persistent_state(agent, persistent)
        upsert(
            agent,
            _KIND,
            render_nudge_payload(
                NudgeSituation.PACKAGE_UPDATE_AVAILABLE,
                NudgeFacts(
                    running=info.running_version,
                    installed=info.installed_version,
                    latest=latest,
                    checked_at_date=today,
                ),
            ),
        )
        _log(
            agent,
            "nudge_emitted",
            kind=_KIND,
            installed=info.installed_version,
            latest=latest,
            source="pypi-json",
        )
        return

    kernel_state["emitted_for_latest"] = None
    _save_persistent_state(agent, persistent)
    remove(agent, _KIND)


def _runtime_info() -> _RuntimeInfo:
    from importlib import metadata

    # The kernel must not import the lingtai wrapper. When the wrapper is
    # already loaded (the normal Agent path), read its in-memory __version__
    # and __file__ so we can distinguish the *running* code from the
    # *installed* distribution metadata on disk. This detects in-place upgrades
    # that happened after the current process started.
    import sys

    wrapper = sys.modules.get("lingtai")
    try:
        dist = metadata.distribution("lingtai")
        installed = str(dist.version)
    except metadata.PackageNotFoundError:
        return _RuntimeInfo(
            running_version=getattr(wrapper, "__version__", "unknown"),
            installed_version="unknown",
            dev_reason="no-installed-distribution",
        )

    if wrapper is not None:
        running = str(getattr(wrapper, "__version__", installed))
        module_file = str(getattr(wrapper, "__file__", ""))
    else:
        # Wrapper not loaded: fall back to distribution metadata. In this path
        # running and installed are identical, so only dev/editable detection
        # can produce a nudge; no runtime-upgrade nudge is possible.
        running = installed
        module_file = str(dist.locate_file("lingtai/__init__.py"))

    return _RuntimeInfo(
        running_version=running,
        installed_version=installed,
        dev_reason=_dev_install_reason(dist, module_file, running, installed),
    )


def _dev_install_reason(dist: Any, module_file: str, running: str, installed: str) -> str | None:
    if _direct_url_is_editable(dist):
        return "editable-install"
    if _looks_like_dev_version(running) or _looks_like_dev_version(installed):
        return "dev-version"
    if _module_from_source_checkout(module_file):
        return "source-checkout"
    return None


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


def _module_from_source_checkout(module_file: str) -> bool:
    if not module_file:
        return False
    try:
        path = Path(module_file).resolve()
    except Exception:
        return False
    if any(part in {"site-packages", "dist-packages"} for part in path.parts):
        return False
    return any((parent / ".git").exists() and (parent / "pyproject.toml").exists() for parent in path.parents)


def _remote_check_due(kernel_state: dict[str, Any], installed_version: str, today: str) -> bool:
    return (
        kernel_state.get("last_remote_check_date") != today
        or kernel_state.get("checked_installed_version") != installed_version
    )


def _fetch_latest_version() -> str:
    req = urllib.request.Request(
        _PYPI_JSON_URL,
        headers={"User-Agent": "lingtai-kernel-nudge/1"},
    )
    with urllib.request.urlopen(req, timeout=_REMOTE_TIMEOUT_SECONDS) as resp:
        raw = resp.read()
    data = json.loads(raw.decode("utf-8"))
    latest = str(data.get("info", {}).get("version") or "")
    if not latest:
        raise RuntimeError("PyPI response did not include info.version")
    return latest


def _is_newer(candidate: str, current: str) -> bool:
    if not candidate or candidate == current:
        return False
    try:
        from packaging.version import Version

        return Version(candidate) > Version(current)
    except Exception:
        return _version_tuple(candidate) > _version_tuple(current)


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version or ""))


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _persistent_path(agent) -> Path:
    return Path(agent._working_dir) / _STATE_FILE


def _load_persistent_state(agent) -> dict[str, Any]:
    path = _persistent_path(agent)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_persistent_state(agent, state: dict[str, Any]) -> None:
    path = _persistent_path(agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def _store_kernel_state(agent, fields: dict[str, Any]) -> None:
    persistent = _load_persistent_state(agent)
    kernel_state = persistent.setdefault(_KIND, {})
    if all(kernel_state.get(k) == v for k, v in fields.items()):
        return
    kernel_state.update(fields)
    _save_persistent_state(agent, persistent)


def _state(agent) -> dict:
    state = getattr(agent, "_nudge_kernel_version_state", None)
    if not isinstance(state, dict):
        state = {}
        setattr(agent, "_nudge_kernel_version_state", state)
    return state


def _log(agent, event: str, **fields: Any) -> None:
    try:
        agent._log(event, **fields)
    except Exception:
        pass
