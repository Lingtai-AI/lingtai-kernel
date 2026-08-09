"""Telegram Task Card loaded-vs-installed runtime identity (issue #987).

The non-deletable Task Card self-heal (PR #984) can only run after the live
Telegram manager process has loaded the repair code. If the on-disk checkout
is updated underneath a long-lived manager process, the installed source
contains the repair while the loaded runtime predates it: the old card stays
frozen, ``/taskcard`` still reports a healthy ``enabled`` setting, and nothing
says that one refresh is required.

This module captures the source identity of the Task Card runtime the current
*process* imported (a non-secret SHA-256 prefix over the curated Task Card
source files) and compares it with the same files as they exist on disk at
query time. A deterministic ``refresh required`` hint is returned only when
loaded != installed, so the plain settings response never implies resident
health it cannot prove.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# Curated files whose loaded-vs-installed mismatch changes resident Task Card
# lifecycle behavior (mirrors the kernel's ``_FP_KEY_FILES`` approach in
# ``lingtai.kernel.base_agent.lifecycle``). The kernel fingerprint covers only
# ``lingtai.kernel`` sources; the Telegram manager lives outside it, so the
# exact #987 drift (``manager.py`` / ``task_card`` gaining the repair under a
# live process) is invisible to that fingerprint and needs its own identity.
_TASK_CARD_SOURCE_FILES: tuple[str, ...] = (
    "manager.py",
    "task_card/__init__.py",
    "task_card/controller.py",
    "task_card/interface.py",
    "task_card/resident.py",
)

#: Bounded, actionable hint surfaced when the loaded runtime predates the
#: installed checkout. One refresh is exactly the remediation the live manager
#: needs; nothing here toggles Task Card or deletes/re-sends anything.
DRIFT_HINT = (
    "\u26a0\ufe0f Task Card runtime drift: this process predates the installed "
    "checkout \u2014 one /refresh is required to apply the installed Task Card "
    "self-heal."
)


def _task_card_source_dir() -> Path:
    """The telegram package directory this module was imported from."""
    return Path(__file__).resolve().parent


def _digest_source_files(root: Path) -> str | None:
    """SHA-256 prefix (12 hex) of the curated Task Card source files.

    Returns ``None`` only when the source directory itself cannot be read, so
    the caller stays silent rather than misreporting drift. Missing individual
    files hash a ``\x00`` marker (kernel convention) so removals still change
    the digest.
    """
    hasher = hashlib.sha256()
    for rel in _TASK_CARD_SOURCE_FILES:
        try:
            hasher.update((root / rel).read_bytes())
        except OSError:
            hasher.update(b"\x00")
    return hasher.hexdigest()[:12]


# Loaded identity: computed once at import time by this process. If the disk is
# updated underneath a running process, this constant still describes the code
# that actually executed.
LOADED_TASK_CARD_SOURCE_DIGEST: str | None = _digest_source_files(
    _task_card_source_dir()
)


def installed_task_card_source_digest() -> str | None:
    """Re-read the same curated files from disk now (the installed runtime)."""
    return _digest_source_files(_task_card_source_dir())


def task_card_drift_hint() -> str | None:
    """Return :data:`DRIFT_HINT` when the loaded runtime predates installed source.

    Returns ``None`` when the digests match or either side is unprovable, i.e.
    absence of the hint means the loaded Task Card runtime matches the
    installed checkout (the healthy state).
    """
    loaded = LOADED_TASK_CARD_SOURCE_DIGEST
    installed = installed_task_card_source_digest()
    if loaded is None or installed is None:
        return None
    if loaded == installed:
        return None
    return DRIFT_HINT
