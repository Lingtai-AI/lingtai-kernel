"""Stage 14 — ``NativeRuntimeSession`` lifecycle & boot-failure hardening.

These tests pin the failure-path contract of the native runtime session:

- ``start()`` rolls back to a safe ``PENDING``/no-agent state and raises
  :class:`NativeRuntimeStartError` (chaining the original) when the agent
  factory, agent construction, or ``agent.start()`` fails — emitting a *fatal*
  ERROR event whose payload never leaks ``api_key`` or other secrets.
- ``send()`` translates an underlying ``agent.send()`` raise into a *non-fatal*
  ERROR event instead of propagating, leaving the session ACTIVE.
- ``stop()`` that cannot cleanly join the agent's loop thread emits a
  *non-fatal* ERROR event (dirty join) but still transitions to STOPPED.

As with the rest of the native suite, no real model / API key / agent process
is booted — a fake factory is injected.
"""
from __future__ import annotations

import threading

import pytest

from lingtai_sdk import native
from lingtai_sdk import runtime as rt
from lingtai_sdk.errors import (
    LingTaiSDKError,
    NativeRuntimeConfigurationError,
    NativeRuntimeStartError,
)


# --------------------------------------------------------------------------
# Fake agents — each variant fails at a specific lifecycle point.
# --------------------------------------------------------------------------
class _GoodAgent:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.sent: list[tuple] = []

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True

    def send(self, content, sender: str = "user") -> None:
        self.sent.append((content, sender))


class _StartRaisesAgent(_GoodAgent):
    def start(self) -> None:
        raise RuntimeError("boom: agent failed to boot")


class _SendRaisesAgent(_GoodAgent):
    def send(self, content, sender: str = "user") -> None:
        raise RuntimeError("boom: enqueue failed")


def _opts(tmp_path, **extra):
    return rt.RuntimeOptions(working_dir=tmp_path, **extra)


# --------------------------------------------------------------------------
# Errors module / root export
# --------------------------------------------------------------------------
def test_start_error_is_sdk_error_subclass():
    assert issubclass(NativeRuntimeStartError, LingTaiSDKError)
    # Sibling, not a subclass, of the pre-build configuration error.
    assert not issubclass(NativeRuntimeStartError, NativeRuntimeConfigurationError)


def test_start_error_exported_from_root():
    import lingtai_sdk

    assert lingtai_sdk.NativeRuntimeStartError is NativeRuntimeStartError
    assert "NativeRuntimeStartError" in lingtai_sdk.__all__


# --------------------------------------------------------------------------
# start() rollback + fatal error event
# --------------------------------------------------------------------------
def test_start_factory_failure_rolls_back_and_raises(tmp_path):
    def _factory(**kwargs):
        raise RuntimeError("factory exploded")

    rtm = native.NativeRuntime(agent_factory=_factory)
    session = rtm.create_session(_opts(tmp_path))
    with pytest.raises(NativeRuntimeStartError) as ei:
        session.start()
    # Original is chained for diagnosis.
    assert isinstance(ei.value.__cause__, RuntimeError)
    # Rolled back to a safe, restartable state.
    assert session.state is rt.RuntimeState.PENDING
    assert session.agent is None
    # A fatal ERROR event was emitted.
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert len(errs) == 1
    assert errs[0].data["fatal"] is True


def test_start_agent_start_failure_rolls_back_and_raises(tmp_path):
    rtm = native.NativeRuntime(agent_factory=lambda **kw: _StartRaisesAgent(**kw))
    session = rtm.create_session(_opts(tmp_path))
    with pytest.raises(NativeRuntimeStartError):
        session.start()
    assert session.state is rt.RuntimeState.PENDING
    assert session.agent is None
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert len(errs) == 1
    assert errs[0].data["fatal"] is True


def test_start_failure_does_not_leak_api_key(tmp_path):
    secret = "sk-super-secret-key-1234567890"

    def _factory(**kwargs):
        raise RuntimeError(f"upstream error with {secret} embedded")

    rtm = native.NativeRuntime(agent_factory=_factory)
    session = rtm.create_session(
        _opts(tmp_path, provider="anthropic", model="m", api_key=secret)
    )
    with pytest.raises(NativeRuntimeStartError) as ei:
        session.start()
    # Neither the raised SDK error's message nor the emitted event echoes the
    # secret, even though the chained cause's message contains it.
    assert secret not in str(ei.value)
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert errs and all(secret not in repr(e.data) for e in errs)


def test_start_failure_is_restartable(tmp_path):
    """After a rolled-back failure, a subsequent successful start() works."""
    calls = {"n": 0}

    def _factory(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first boot fails")
        return _GoodAgent(**kwargs)

    rtm = native.NativeRuntime(agent_factory=_factory)
    session = rtm.create_session(_opts(tmp_path))
    with pytest.raises(NativeRuntimeStartError):
        session.start()
    # Retry succeeds because state rolled back to PENDING.
    session.start()
    assert session.state is rt.RuntimeState.ACTIVE
    assert isinstance(session.agent, _GoodAgent)


def test_configuration_error_still_raised_pre_build(tmp_path):
    """Default factory with missing provider/model keeps the old error type."""
    rtm = native.NativeRuntime()  # default (service-building) factory
    session = rtm.create_session(_opts(tmp_path))  # no provider/model
    with pytest.raises(NativeRuntimeConfigurationError):
        session.start()
    assert session.state is rt.RuntimeState.PENDING
    assert session.agent is None


# --------------------------------------------------------------------------
# send() failure -> non-fatal error event, stays active
# --------------------------------------------------------------------------
def test_send_failure_emits_non_fatal_error_and_stays_active(tmp_path):
    rtm = native.NativeRuntime(agent_factory=lambda **kw: _SendRaisesAgent(**kw))
    session = rtm.create_session(_opts(tmp_path))
    session.start()
    # Does not propagate.
    session.send("hello")
    assert session.state is rt.RuntimeState.ACTIVE
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert len(errs) == 1
    assert errs[0].data["fatal"] is False
    # No NOTIFICATION queued event for the failed send.
    notes = [e for e in session.events() if e.kind is rt.EventKind.NOTIFICATION]
    assert notes == []


def test_send_not_active_still_emits_non_fatal_error(tmp_path):
    """Pre-existing not-active behavior is unchanged."""
    rtm = native.NativeRuntime(agent_factory=lambda **kw: _GoodAgent(**kw))
    session = rtm.create_session(_opts(tmp_path))
    session.send("too early")
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert len(errs) == 1
    assert errs[0].data["fatal"] is False


# --------------------------------------------------------------------------
# Dirty stop -> non-fatal error event, still STOPPED
# --------------------------------------------------------------------------
class _DirtyStopAgent(_GoodAgent):
    """Its loop thread stays alive after stop() — a dirty join."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._stop_evt = threading.Event()
        self._thread = threading.Thread(target=self._stop_evt.wait, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.stopped = True  # does NOT release the thread — stays alive

    def release(self) -> None:
        self._stop_evt.set()
        self._thread.join(timeout=2.0)


def test_dirty_stop_emits_non_fatal_error_but_becomes_stopped(tmp_path):
    rtm = native.NativeRuntime(agent_factory=lambda **kw: _DirtyStopAgent(**kw))
    session = rtm.create_session(_opts(tmp_path))
    session.start()
    agent = session.agent
    try:
        session.stop(timeout=0.01)
        assert session.state is rt.RuntimeState.STOPPED
        errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
        assert len(errs) == 1
        assert errs[0].data["fatal"] is False
    finally:
        agent.release()


def test_clean_stop_emits_no_error(tmp_path):
    rtm = native.NativeRuntime(agent_factory=lambda **kw: _GoodAgent(**kw))
    session = rtm.create_session(_opts(tmp_path))
    session.start()
    session.stop()
    assert session.state is rt.RuntimeState.STOPPED
    errs = [e for e in session.events() if e.kind is rt.EventKind.ERROR]
    assert errs == []
