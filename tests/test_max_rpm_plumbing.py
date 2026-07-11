"""End-to-end plumbing test: init.json max_rpm reaches the adapter's gate.

Verifies:
1. AgentConfig has the field with default 0 (no gating).
2. init_schema accepts max_rpm without warning.
3. cli._build_agent passes max_rpm into LLMService.provider_defaults.
4. LLMService threads max_rpm into the adapter via factory kwargs.
5. Every adapter, after construction, exposes a working _gate when max_rpm > 0.
6. _wrap_with_gate wraps sessions when a gate is configured.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from lingtai.kernel.config import AgentConfig
from lingtai.llm.service import LLMService
from lingtai.llm.base import LLMAdapter, _GatedSession
from lingtai.llm.api_gate import APICallGate


def test_agent_config_default_is_60():
    """Default max_rpm = 60 — conservative cap that fixes the network
    cascade scenario (multiple agents simultaneously hitting one provider)
    while being well under any paid-tier provider's actual limit. Solo
    agents on high-tier providers can bump it via init.json."""
    assert AgentConfig().max_rpm == 60


def test_agent_config_overridable():
    assert AgentConfig(max_rpm=120).max_rpm == 120
    assert AgentConfig(max_rpm=0).max_rpm == 0  # 0 disables gating


def test_llm_service_threads_max_rpm_via_provider_defaults():
    # Build a service with provider_defaults set the way cli.py / agent.py do
    svc = LLMService(
        provider="openai",
        model="gpt-4",
        api_key="sk-test",
        provider_defaults={"openai": {"max_rpm": 30}},
    )
    adapter = svc.get_adapter("openai")
    # OpenAIAdapter._setup_gate must have created a gate
    assert adapter._gate is not None
    assert isinstance(adapter._gate, APICallGate)
    adapter._gate.shutdown()


def test_llm_service_no_gate_when_max_rpm_zero():
    svc = LLMService(
        provider="openai",
        model="gpt-4",
        api_key="sk-test",
        # No provider_defaults: defaults.get("max_rpm", 0) returns 0
    )
    adapter = svc.get_adapter("openai")
    assert adapter._gate is None


def test_wrap_with_gate_returns_proxy_when_gate_present():
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    a._setup_gate(60)
    inner = MagicMock(spec=["interface", "send", "send_stream"])
    wrapped = a._wrap_with_gate(inner)
    assert isinstance(wrapped, _GatedSession)
    a._gate.shutdown()



def test_gated_session_adapter_comment_delegates_to_inner():
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    a._setup_gate(60)
    inner = MagicMock()
    inner.adapter_comment.return_value = {
        "adapter": "dummy",
        "summary": "inner note",
    }
    wrapped = a._wrap_with_gate(inner)

    assert wrapped.adapter_comment() == {
        "adapter": "dummy",
        "summary": "inner note",
    }
    a._gate.shutdown()

def test_gated_session_history_summarized_delegates_to_inner():
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    a._setup_gate(60)
    inner = MagicMock()
    wrapped = a._wrap_with_gate(inner)

    wrapped.on_history_summarized(["call_1"])

    inner.on_history_summarized.assert_called_once_with(["call_1"])
    a._gate.shutdown()


def test_gated_session_notification_dismissed_delegates_to_inner():
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    a._setup_gate(60)
    inner = MagicMock()
    wrapped = a._wrap_with_gate(inner)

    wrapped.on_notification_dismissed("system")

    inner.on_notification_dismissed.assert_called_once_with("system")
    a._gate.shutdown()


def test_wrap_with_gate_returns_inner_when_no_gate():
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    inner = MagicMock(spec=["interface"])
    wrapped = a._wrap_with_gate(inner)
    assert wrapped is inner


def test_gated_session_routes_send_through_gate():
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    a._setup_gate(60)
    inner = MagicMock()
    inner.pre_request_hook = None
    inner.session_id = "inner-sess"
    # Model a real adapter: read the (inner) hook and fire it once at send.
    inner.send.side_effect = lambda m: (
        inner.pre_request_hook(inner.interface), "response")[1]
    wrapped = a._wrap_with_gate(inner)

    fired = []
    wrapped.pre_request_hook = lambda iface: fired.append(iface)
    # The named slot delegates to the inner (the object whose send reads it),
    # never landing in the proxy __dict__.
    assert wrapped.pre_request_hook is inner.pre_request_hook
    assert "pre_request_hook" not in wrapped.__dict__
    # Unrelated metadata writes stay proxy-local; inner identity is untouched.
    wrapped.session_id = "proxy-sess"

    result = wrapped.send("hi")  # drive the real gate
    assert result == "response"
    inner.send.assert_called_once_with("hi")
    assert fired == [inner.interface]
    assert wrapped.session_id == "proxy-sess"
    assert inner.session_id == "inner-sess"
    a._gate.shutdown()


def test_gated_session_attribute_passthrough():
    """Read-only attribute access falls through to inner."""
    class _StubAdapter(LLMAdapter):
        def create_chat(self, *a, **kw): pass
        def generate(self, *a, **kw): pass
        def make_tool_result_message(self, *a, **kw): pass
        def is_quota_error(self, exc): return False

    a = _StubAdapter()
    a._setup_gate(60)
    inner = MagicMock()
    inner.session_id = "sess-123"
    inner.total_usage.return_value = {"input_tokens": 100}
    wrapped = a._wrap_with_gate(inner)
    assert wrapped.session_id == "sess-123"  # via __getattr__
    assert wrapped.total_usage()["input_tokens"] == 100  # method passthrough
    a._gate.shutdown()
