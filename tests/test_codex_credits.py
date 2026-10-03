"""Default-off Codex paid-credit admission; all requests use fake services."""

import httpx
import openai
import pytest

from lingtai.auth.codex_account_source import NoCandidateError
from lingtai.kernel.llm.interface import ChatInterface
from lingtai.llm.openai.codex_usage import (
    CODEX_USAGE_URL,
    CodexUsageAuthError,
    included_usage_allowed,
    read_included_usage_allowed,
)
from lingtai.llm.service import build_provider_defaults_from_manifest_llm, LLMService
from lingtai.llm._register import register_all_adapters
from .test_codex_native_multiaccount import (
    _adapter, _RecordingSource, _RefreshingManager, _Responses, _success_events,
)


@pytest.mark.parametrize("limit,expected", [
    ({"allowed": True}, True),
    ({"allowed": False}, False),
    ({"allowed": True, "limit_reached": True}, False),
    ({"allowed": True, "primary_window": {"used_percent": 100}}, False),
    ({"allowed": True, "secondary_window": {"used_percent": 100}}, False),
    ({"allowed": True, "primary_window": {"used_percent": 99.9}}, True),
    ({"allowed": "true"}, None),
    ({"allowed": True, "primary_window": {"used_percent": True}}, None),
    ({"allowed": True, "primary_window": {"used_percent": float("nan")}}, None),
    ({"allowed": True, "primary_window": "invalid"}, None),
    ({}, None),
    (None, None),
])
def test_usage_decision_does_not_count_credits_as_allowance(limit, expected):
    payload = {"rate_limit": limit, "credits": {"has_credits": True, "balance": "62500"}}
    assert included_usage_allowed(payload) is expected


@pytest.mark.parametrize("status,payload,expected", [
    (200, {"rate_limit": {"allowed": True}}, True),
    (200, {"rate_limit": {"allowed": False}, "credits": {"has_credits": True}}, False),
    (200, {"credits": {"unlimited": True}}, None),
    (503, {}, None),
])
def test_usage_reader_uses_exact_bound_account_and_no_retry(status, payload, expected):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=payload)

    with openai.OpenAI(
        api_key="bound-token", base_url="https://inference-proxy.test/codex",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        assert read_included_usage_allowed(client=client, headers={
            "ChatGPT-Account-Id": "bound-account", "originator": "lingtai",
        }) is expected
    assert len(requests) == 1
    assert str(requests[0].url) == CODEX_USAGE_URL
    assert requests[0].method == "GET"
    assert requests[0].headers["authorization"] == "Bearer bound-token"
    assert requests[0].headers["chatgpt-account-id"] == "bound-account"


def test_usage_timeout_is_unknown_without_exception_text():
    def timeout(request):
        raise httpx.ReadTimeout("secret-token", request=request)

    with openai.OpenAI(api_key="fake", http_client=httpx.Client(
        transport=httpx.MockTransport(timeout),
    )) as client:
        assert read_included_usage_allowed(client=client, headers={}) is None


def test_usage_authentication_failure_is_sanitized():
    with openai.OpenAI(api_key="fake", http_client=httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "secret-token"}}),
    ))) as client:
        with pytest.raises(CodexUsageAuthError) as caught:
            read_included_usage_allowed(client=client, headers={})
    assert "secret-token" not in str(caught.value)


@pytest.mark.parametrize("refreshed_account_id", ["acct-account-a", "acct-refreshed", None])
def test_usage_auth_refresh_keeps_the_same_auth_binding(monkeypatch, refreshed_account_id):
    reads = []
    def read(*, client, headers):
        reads.append((client.api_key, headers.get("ChatGPT-Account-Id")))
        if len(reads) == 1:
            raise CodexUsageAuthError()
        return True
    monkeypatch.setattr("lingtai.llm.openai.codex_usage.read_included_usage_allowed", read)
    adapter, source, manager, responses = credit_adapter()
    monkeypatch.setattr(manager, "get_account_id", lambda:
        refreshed_account_id if manager.refresh_calls else "acct-account-a")
    session = adapter.create_chat(model="gpt-6-sol", system_prompt="sys", interface=ChatInterface())
    session.send_stream("hello")
    assert reads == [("secret-account-a", "acct-account-a"),
                     ("recovered-account-a", refreshed_account_id)]
    assert len(source.calls) == 1
    assert manager.refresh_calls == ["secret-account-a"]
    assert responses.client_api_keys == ["recovered-account-a"]
    assert responses.calls[0]["extra_headers"].get("ChatGPT-Account-ID") == refreshed_account_id


def credit_adapter(allow=False):
    source = _RecordingSource("account-a")
    manager = _RefreshingManager("account-a")
    responses = _Responses([_success_events(), _success_events()])
    adapter = _adapter(source, {"account-a": manager}, responses, codex_allow_credits=allow)
    return adapter, source, manager, responses


@pytest.mark.parametrize("transport", ["rest", "websocket"])
@pytest.mark.parametrize("allowance", [False, None])
def test_default_off_blocks_exhausted_or_unknown_before_wire(monkeypatch, transport, allowance):
    monkeypatch.setenv("LINGTAI_CODEX_TRANSPORT", transport)
    monkeypatch.setattr("lingtai.llm.openai.codex_usage.read_included_usage_allowed",
                        lambda **kw: allowance)
    adapter, source, manager, responses = credit_adapter()
    interface = ChatInterface()
    session = adapter.create_chat(model="gpt-6-sol", system_prompt="sys", interface=interface)
    with pytest.raises(NoCandidateError, match="preset"):
        session.send_stream("hello")
    assert responses.calls == []
    assert all(entry.role != "assistant" for entry in interface.entries)
    assert len(source.calls) == 1


def test_opt_in_continues_without_usage_probe_or_new_wire_field(monkeypatch):
    def unexpected(**kw):
        pytest.fail("Credit opt-in leaves billing eligibility to OpenAI")
    monkeypatch.setattr("lingtai.llm.openai.codex_usage.read_included_usage_allowed", unexpected)
    adapter, source, manager, responses = credit_adapter(True)
    session = adapter.create_chat(model="gpt-6-sol", system_prompt="sys", interface=ChatInterface())
    assert session.send_stream("hello").text == "ok"
    assert len(responses.calls) == 1
    assert "codex_allow_credits" not in responses.calls[0]
    assert "codex_allow_credits" not in responses.calls[0].get("extra_body", {})


def test_sticky_account_rechecks_allowance_and_recovers_after_reset(monkeypatch):
    allowance = iter([True, False, True])
    monkeypatch.setattr("lingtai.llm.openai.codex_usage.read_included_usage_allowed",
                        lambda **kw: next(allowance))
    adapter, source, manager, responses = credit_adapter()
    session = adapter.create_chat(model="gpt-6-sol", system_prompt="sys", interface=ChatInterface())
    session.send_stream("first")
    with pytest.raises(NoCandidateError, match="exhausted"):
        session.send_stream("blocked")
    assert len(responses.calls) == 1
    session.send_stream("after reset")
    assert len(responses.calls) == 2
    assert len(source.calls) == 1  # Quota gating never excludes the account.


@pytest.mark.parametrize("value", [False, True, None])
def test_preset_option_reaches_codex_factory(value):
    llm = {"provider": "codex", "model": "gpt-6-sol"}
    if value is not None:
        llm["codex_allow_credits"] = value
    defaults = build_provider_defaults_from_manifest_llm(llm, max_rpm=60)
    register_all_adapters()
    adapter = LLMService._adapter_registry["codex"](defaults=(defaults or {}).get("codex"))
    assert adapter._codex_allow_credits is (value is True)
