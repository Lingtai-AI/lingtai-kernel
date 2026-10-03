"""Shared pytest fixtures for LingTai kernel tests."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from ._agent_dir_helpers import make_agent_dir as _make_agent_dir


# The developer's real ``claude`` binary (resolved once, from the PATH the test
# session started with). Tests must never exec it: they use a fake cli_path, a
# stub script on PATH, or a recording process port.
_REAL_CLAUDE = shutil.which("claude")
_REAL_CLAUDE_BINARIES = frozenset(
    {os.path.realpath(_REAL_CLAUDE)} if _REAL_CLAUDE else ()
)
_CLAUDE_EXEC_GUARD_MESSAGE = (
    "LingTai test guard: a test tried to exec the real `claude` CLI; use a "
    "fake cli_path, a stub script on PATH, or a recording process port"
)


@pytest.fixture
def make_agent_dir():
    """Factory fixture: create a minimal agent working dir.

    Returns the :func:`tests._agent_dir_helpers.make_agent_dir` callable so a
    single test can build several agent dirs with different shapes (heartbeat,
    human, mailbox, …).
    """
    return _make_agent_dir


@pytest.fixture(autouse=True)
def _isolate_llm_adapter_registry():
    """Keep provider registration mutations local to each test."""

    from lingtai.llm.service import LLMService

    snapshot = dict(LLMService._adapter_registry)
    yield
    LLMService._adapter_registry.clear()
    LLMService._adapter_registry.update(snapshot)


@pytest.fixture(scope="session")
def _claude_exec_tripwire_dir(tmp_path_factory):
    """A PATH shim named ``claude`` that records every exec attempt and fails.

    Installed only when a real ``claude`` is reachable (developer machines),
    so a machine without Claude Code keeps the ordinary "not on PATH" path.
    Child processes inherit PATH, so detached daemon children are covered too.
    """
    if not _REAL_CLAUDE_BINARIES or os.name == "nt":
        return None
    directory = tmp_path_factory.mktemp("claude-exec-tripwire")
    attempts = directory / "attempts.log"
    shim = directory / "claude"
    shim.write_text(
        "#!/bin/sh\n"
        f"echo \"claude $*\" >> '{attempts}'\n"
        f"echo '{_CLAUDE_EXEC_GUARD_MESSAGE}' >&2\n"
        "exit 97\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return directory


@pytest.fixture(autouse=True)
def _isolate_claude_cli_auth_probe(monkeypatch, _claude_exec_tripwire_dir):
    """Never ask, or exec, this machine's real ``claude`` CLI.

    ``claude auth status`` reads (and may touch) the developer's real Claude
    config. The claude-code adapter, the daemon Claude backends, and the preset
    connectivity check all reach it only through
    ``preset_connectivity.claude_cli_login_status``, so this guard answers
    "unknown" (proceed) there; a test that needs a verdict patches the same
    seam, and the implementation's own tests import the real function at module
    import time and point it at a stub CLI.

    It also fails the test loudly if anything would exec the real binary: a
    ``claude`` shim is put first on PATH (recording attempts, exit 97), and
    ``subprocess.Popen`` refuses an absolute path or explicit child PATH that
    resolves to the real binary.
    """

    from lingtai.kernel import preset_connectivity

    monkeypatch.setattr(
        preset_connectivity,
        "claude_cli_login_status",
        lambda *args, **kwargs: preset_connectivity.CLAUDE_LOGIN_UNKNOWN,
    )
    if not _REAL_CLAUDE_BINARIES:
        yield
        return

    violations: list[str] = []
    attempts_log = None
    before = 0
    if _claude_exec_tripwire_dir is not None:
        # Insert the shim immediately before the PATH entry that holds the
        # real binary, so every other PATH ordering a test asserts is intact.
        entries = os.environ.get("PATH", "").split(os.pathsep)
        for index, entry in enumerate(entries):
            found = shutil.which("claude", path=entry) if entry else None
            if found and os.path.realpath(found) in _REAL_CLAUDE_BINARIES:
                entries.insert(index, str(_claude_exec_tripwire_dir))
                monkeypatch.setenv("PATH", os.pathsep.join(entries))
                break
        attempts_log = _claude_exec_tripwire_dir / "attempts.log"
        before = attempts_log.stat().st_size if attempts_log.exists() else 0

    original = subprocess.Popen._execute_child

    def guarded(self, args, executable, *rest, **kwargs):
        program = executable
        if program is None:
            if isinstance(args, (str, bytes, os.PathLike)):
                program = args
            elif args:
                program = args[0]
        if program is not None:
            name = os.fsdecode(program)
            child_env = rest[4] if len(rest) > 4 else kwargs.get("env")
            search_path = (
                child_env.get("PATH") if isinstance(child_env, dict) else None
            )
            resolved = name if os.path.isabs(name) else shutil.which(
                name, path=search_path
            )
            if resolved and os.path.realpath(resolved) in _REAL_CLAUDE_BINARIES:
                violations.append(name)
                raise RuntimeError(_CLAUDE_EXEC_GUARD_MESSAGE)
        return original(self, args, executable, *rest, **kwargs)

    monkeypatch.setattr(subprocess.Popen, "_execute_child", guarded)
    yield
    if attempts_log is not None and attempts_log.exists():
        with attempts_log.open("rb") as stream:
            stream.seek(before)
            new_attempts = stream.read().decode("utf-8", "replace").strip()
        if new_attempts:
            violations.append(new_attempts)
    if violations:
        pytest.fail(f"{_CLAUDE_EXEC_GUARD_MESSAGE}: {violations!r}")


@pytest.fixture(autouse=True)
def _isolate_runtime_identity_cache():
    """Keep process-cached runtime identity local to each test."""

    from lingtai.kernel.runtime_identity import runtime_identity

    runtime_identity.cache_clear()
    yield
    runtime_identity.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_cache_miss_budget_env(monkeypatch):
    """Keep the suite hermetic w.r.t. the live cache-miss budget env override.

    The System-owned resolver reads ``LINGTAI_CACHE_MISS_BUDGET`` live, so an
    ambient value (an operator's shell, or an agent's ``env_file``) would
    otherwise leak into budget assertions. Tests that exercise the override opt
    back in with ``monkeypatch.setenv``.
    """

    monkeypatch.delenv("LINGTAI_CACHE_MISS_BUDGET", raising=False)


@pytest.fixture(autouse=True)
def _isolate_notification_dismiss_guards():
    """Keep generic notification-dismiss guard registration test-local."""

    from lingtai.kernel.notifications import _GENERIC_DISMISS_GUARDED

    snapshot = dict(_GENERIC_DISMISS_GUARDED)
    yield
    _GENERIC_DISMISS_GUARDED.clear()
    _GENERIC_DISMISS_GUARDED.update(snapshot)


@pytest.fixture(autouse=True)
def _isolate_notification_hook_registry():
    """Keep the module-level hook registry mirror test-local.

    Hook add/drop/edit actions and ``sync_hook_registry`` mutate the
    process-global ``_REGISTERED_HOOK_CHANNELS`` / ``_HOOK_REGISTRY_SEEDED`` /
    ``_BLOCKED_CHANNEL_WARNED`` state (mirrored from each agent's
    ``.notification/hooks.json``).  Without isolation a hook registered by one
    test would keep its channel allowlisted (and the warn-and-flag dedupe
    marker set) for every later test in the same process.
    """

    from lingtai.kernel.notifications import (
        _BLOCKED_CHANNEL_WARNED,
        _HOOK_REGISTRY_SEEDED,
        _HOOK_REGISTRY_STAT,
        _REGISTERED_HOOK_CHANNELS,
        _invalidate_allow_predicates,
    )

    snapshot_channels = {
        key: set(channels) for key, channels in _REGISTERED_HOOK_CHANNELS.items()
    }
    snapshot_seeded = set(_HOOK_REGISTRY_SEEDED)
    snapshot_stat = dict(_HOOK_REGISTRY_STAT)
    snapshot_warned = {
        key: set(channels) for key, channels in _BLOCKED_CHANNEL_WARNED.items()
    }
    yield
    _REGISTERED_HOOK_CHANNELS.clear()
    _REGISTERED_HOOK_CHANNELS.update(
        {key: set(channels) for key, channels in snapshot_channels.items()}
    )
    _HOOK_REGISTRY_SEEDED.clear()
    _HOOK_REGISTRY_SEEDED.update(snapshot_seeded)
    _HOOK_REGISTRY_STAT.clear()
    _HOOK_REGISTRY_STAT.update(snapshot_stat)
    _BLOCKED_CHANNEL_WARNED.clear()
    _BLOCKED_CHANNEL_WARNED.update(
        {key: set(channels) for key, channels in snapshot_warned.items()}
    )
    _invalidate_allow_predicates()


@pytest.fixture(autouse=True)
def _reset_package_logging():
    """Keep stdlib-logging handler state from leaking across tests.

    ``setup_logging``/``get_logger`` configure a process-global "lingtai"
    logger; without a reset, a FileHandler attached by one test would stay
    attached for every subsequent test in the run (writing to a stale path).
    """

    from lingtai.kernel.logging import reset_logging

    reset_logging()
    yield
    reset_logging()
