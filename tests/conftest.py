"""Shared pytest fixtures for LingTai kernel tests."""

from __future__ import annotations

import pytest

from ._agent_dir_helpers import make_agent_dir as _make_agent_dir
from ._daemon_manager_reaper import (
    daemon_manager_state_present,
    find_daemon_managers,
    reap_daemon_managers,
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


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    """Stop any resident daemon manager this test started under its ``tmp_path``.

    A real ``emanate`` through the default ``manager_pool_size`` spawns the
    POSIX central manager in its own session, and it stays resident by design
    (production managers outlive their agent). Without this teardown every
    such test leaves one live manager behind for as long as the machine runs.
    It runs after every fixture of the item is torn down, so a test's
    ``monkeypatch`` of ``subprocess``/``os`` is already undone. The
    ``manager.lock`` gate keeps the process-table scan off tests that never
    reached the manager spawn path.
    """
    tmp_path = (getattr(item, "funcargs", None) or {}).get("tmp_path")
    try:
        return (yield)
    finally:
        if tmp_path is not None and (
            not tmp_path.exists() or daemon_manager_state_present(tmp_path)
        ):
            reap_daemon_managers(find_daemon_managers(under=[tmp_path]))


def _session_basetemp(config):
    """Return this session's pytest basetemp, or ``None`` if never created."""
    factory = getattr(config, "_tmp_path_factory", None)
    if factory is None:
        return None
    try:
        return factory._basetemp
    except AttributeError:  # pragma: no cover - future pytest layout
        return factory.getbasetemp()


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Session safety net for daemon managers the per-test teardown missed.

    Runs on pass, failure, ``-x``, and Ctrl-C alike. It reaps managers whose
    agent directory lies under this session's basetemp (covering
    ``tmp_path_factory`` directories and managers spawned by child CLI
    processes) and managers left by an earlier pytest session whose ``.lock``
    proves that session's process was killed before it could clean up.
    """
    basetemp = _session_basetemp(session.config)
    roots = [basetemp] if basetemp is not None else []
    reap_daemon_managers(find_daemon_managers(under=roots, dead_pytest_sessions=True))


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
