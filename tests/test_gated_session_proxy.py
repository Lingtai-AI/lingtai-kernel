"""Regression tests for _GatedSession write-forwarding (#724).

_GatedSession forwards attribute *reads* to the inner adapter session via
__getattr__. Before #724 it had no __setattr__, so attribute *writes* landed
on the proxy's own __dict__ and never reached the inner session. Since the
default max_rpm is 60 (0 disables), essentially every production session is
gated, which silently swallowed two load-bearing writes:

  * BaseAgent._install_pre_request_hook assigns ``pre_request_hook``, which
    every adapter fires as ``self.pre_request_hook`` from inside its
    ``send``/``send_stream`` — on the inner session, where it stayed None.
  * LLMService.create_session assigns ``session_id`` / ``_agent_type`` /
    ``_tracked``, which ChatSession.get_state reads on the inner ``self`` —
    so get_state reported session_id="" / tracked=True for every gated
    session, even inverting an untracked session's ``tracked`` flag.

These tests build the proxy exactly as production does (via
LLMAdapter._wrap_with_gate) and assert writes reach the inner session. The
hook-fire and get_state tests provably fail on pre-fix main.
"""
from __future__ import annotations

from lingtai_kernel.llm.base import ChatSession
from lingtai.llm.base import LLMAdapter, _GatedSession


class _FakeInterface:
    """Minimal ChatInterface stand-in for get_state()."""

    entries: list = []

    def to_dict(self):
        return []


class _FakeSession(ChatSession):
    """Concrete ChatSession that mirrors the real inner-self read pattern.

    ``send`` fires ``self.pre_request_hook`` the way every real adapter's
    send()/send_stream() does (e.g. anthropic/adapter.py:389-390), so the
    proxy's write-forwarding is exercised end-to-end rather than mocked.
    """

    def __init__(self):
        self._iface = _FakeInterface()
        self.hook_fire_count = 0

    @property
    def interface(self):
        return self._iface

    def send(self, message):
        # Mirror the real adapter contract: read the hook on the inner self.
        if self.pre_request_hook is not None:
            self.pre_request_hook(self.interface)
            self.hook_fire_count += 1
        return "ok"

    def send_stream(self, message, on_chunk=None):
        return self.send(message)


class _StubAdapter(LLMAdapter):
    def create_chat(self, *a, **kw):
        pass

    def generate(self, *a, **kw):
        pass

    def make_tool_result_message(self, *a, **kw):
        pass

    def is_quota_error(self, exc):
        return False


def _make_gated():
    """Build a gated proxy exactly the way production does (via the adapter)."""
    adapter = _StubAdapter()
    adapter._setup_gate(60)  # default cap; any >0 configures a gate
    inner = _FakeSession()
    proxy = adapter._wrap_with_gate(inner)
    assert isinstance(proxy, _GatedSession)
    return adapter, proxy, inner


def test_setattr_forwards_to_inner_session():
    """A write through the proxy lands on the inner session, not the proxy."""
    adapter, proxy, inner = _make_gated()
    try:
        def hook(_iface):
            return None

        proxy.pre_request_hook = hook
        assert inner.pre_request_hook is hook
        # The write must NOT shadow on the proxy's own __dict__.
        assert "pre_request_hook" not in object.__getattribute__(proxy, "__dict__")
    finally:
        adapter._gate.shutdown()


def test_proxy_slots_stay_on_proxy():
    """Only _inner/_gate live on the proxy; nothing else leaks there."""
    adapter, proxy, inner = _make_gated()
    try:
        assert set(object.__getattribute__(proxy, "__dict__")) == {"_inner", "_gate"}
        # Reassigning a proxy slot must not touch the inner session.
        new_gate = adapter._gate
        proxy._gate = new_gate
        assert object.__getattribute__(proxy, "__dict__")["_gate"] is new_gate
    finally:
        adapter._gate.shutdown()


def test_pre_request_hook_fires_inside_gated_send():
    """The hook installed through the proxy fires from the inner send().

    Fails on pre-fix main: the write landed on the proxy, so the inner
    self.pre_request_hook stayed None and the hook never fired.
    """
    adapter, proxy, inner = _make_gated()
    try:
        fired = {"n": 0}

        def hook(_iface):
            fired["n"] += 1

        proxy.pre_request_hook = hook
        assert proxy.send("hi") == "ok"
        assert fired["n"] == 1
        assert inner.hook_fire_count == 1
    finally:
        adapter._gate.shutdown()


def test_get_state_reflects_service_assigned_identity():
    """get_state() reports the identity the service stamped through the proxy.

    Fails on pre-fix main: the writes stayed on the proxy, so get_state()
    (executing with the inner as self via __getattr__) returned the class
    defaults session_id="" / agent_type="".
    """
    adapter, proxy, inner = _make_gated()
    try:
        proxy.session_id = "st_abc123def456"
        proxy._agent_type = "worker"
        proxy._tracked = True

        state = proxy.get_state()
        assert state["session_id"] == "st_abc123def456"
        assert state["metadata"]["agent_type"] == "worker"
        assert state["metadata"]["tracked"] is True
    finally:
        adapter._gate.shutdown()


def test_untracked_session_reports_untracked():
    """An untracked session written through the proxy reports tracked=False.

    Fails on pre-fix main: _tracked=False stayed on the proxy while the
    inner default True was reported, inverting the flag.
    """
    adapter, proxy, inner = _make_gated()
    try:
        proxy.session_id = ""
        proxy._tracked = False
        assert proxy.get_state()["metadata"]["tracked"] is False
    finally:
        adapter._gate.shutdown()


def test_delattr_forwards_to_inner_session():
    """del through the proxy removes the instance attribute from the inner."""
    adapter, proxy, inner = _make_gated()
    try:
        proxy.session_id = "st_temp"
        assert inner.session_id == "st_temp"
        del proxy.session_id
        # Instance attribute gone; reads now fall back to the class default.
        assert "session_id" not in inner.__dict__
        assert proxy.session_id == ""  # ChatSession class default
    finally:
        adapter._gate.shutdown()
