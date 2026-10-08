"""Tests that the standard ``service_tier`` reaches ``openai`` and ``codex``.

One normalizer (``lingtai.llm._register._normalize_service_tier``) owns the
axis for the two families that forward it: ``fast`` becomes the wire value
``priority``; the standard values ``auto``/``default``/``flex``/``priority``
pass through verbatim; anything else is a validation error (the same function
backs ``init_schema.validate_init``). ``anthropic`` and ``claude-code`` never
forward a tier.

No network: clients are fakes that record the kwargs they receive.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.llm._register import (
    SERVICE_TIER_VALUES,
    _normalize_service_tier,
    register_all_adapters,
)
from lingtai.llm.openai.adapter import OpenAIAdapter
from lingtai.llm.service import LLMService


@dataclass
class _Event:
    type: str
    response: object | None = None


def _completed() -> _Event:
    return _Event(
        "response.completed",
        response=SimpleNamespace(
            id="resp_fake",
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                input_tokens_details=SimpleNamespace(cached_tokens=0),
                output_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
        ),
    )


class _FakeResponses:
    def __init__(self) -> None:
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        if kwargs.get("stream"):
            return iter([_completed()])
        return SimpleNamespace(id="resp_fake", output=[], usage=None)


class _FakeResponsesClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


def _chat_client():
    msg = SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[])
    raw = SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )
    client = MagicMock()
    client.chat.completions.create.return_value = raw
    return client


def _factory_adapter(provider: str, defaults: dict, *, base_url: str | None = None):
    register_all_adapters()
    factory = LLMService._adapter_registry[provider]
    kwargs = {"model": "gpt-test", "defaults": defaults, "api_key": "fake"}
    if base_url is not None:
        kwargs["base_url"] = base_url
    return factory(**kwargs)


def _responses_kwargs(adapter) -> dict:
    adapter._client = _FakeResponsesClient()
    session = adapter._create_responses_session("gpt-test", "sys")
    session.send_stream("hello")
    return adapter._client.responses.kwargs[-1]


def _chat_kwargs(adapter) -> dict:
    adapter._client = _chat_client()
    session = adapter._create_completions_session("gpt-test", "sys")
    session.send("hello")
    return adapter._client.chat.completions.create.call_args.kwargs


# The official endpoint (base_url omitted) and a compatible endpoint behave the
# same: the tier is a standard request field.
_OPENAI_ROUTES = (
    pytest.param(None, id="official"),
    pytest.param("http://127.0.0.1:18766/v1", id="compatible"),
)


@pytest.mark.parametrize("base_url", _OPENAI_ROUTES)
def test_fast_reaches_responses_request_as_priority(base_url):
    adapter = _factory_adapter(
        "openai", {"wire_api": "responses", "service_tier": " fast "}, base_url=base_url
    )
    assert _responses_kwargs(adapter)["service_tier"] == "priority"


@pytest.mark.parametrize("base_url", _OPENAI_ROUTES)
def test_fast_reaches_chat_completions_request_as_priority(base_url):
    adapter = _factory_adapter("openai", {"service_tier": "fast"}, base_url=base_url)
    assert _chat_kwargs(adapter)["service_tier"] == "priority"


@pytest.mark.parametrize("value", SERVICE_TIER_VALUES)
def test_standard_tiers_pass_through_verbatim_on_both_wires(value):
    responses = _factory_adapter(
        "openai", {"wire_api": "responses", "service_tier": value}
    )
    assert _responses_kwargs(responses)["service_tier"] == value
    chat = _factory_adapter("openai", {"service_tier": value})
    assert _chat_kwargs(chat)["service_tier"] == value


@pytest.mark.parametrize("value", [None, "", "   "])
def test_absent_or_blank_tier_is_omitted(value):
    defaults: dict = {"wire_api": "responses"}
    if value is not None:
        defaults["service_tier"] = value
    adapter = _factory_adapter("openai", defaults)
    assert "service_tier" not in _responses_kwargs(adapter)


@pytest.mark.parametrize("value", ["unsupported", "turbo", "Fast", 3])
def test_unrecognized_tier_fails_loudly_at_the_factory(value):
    with pytest.raises(ValueError):
        _factory_adapter("openai", {"service_tier": value})


def test_normalizer_contract():
    assert _normalize_service_tier("fast") == "priority"
    assert _normalize_service_tier(" priority ") == "priority"
    for value in ("auto", "default", "flex", "priority"):
        assert _normalize_service_tier(value) == value
    assert _normalize_service_tier(None) is None
    assert _normalize_service_tier("") is None
    with pytest.raises(ValueError, match="Unsupported service_tier"):
        _normalize_service_tier("scale")


def test_anthropic_factory_never_receives_service_tier(monkeypatch):
    import lingtai.llm.anthropic.adapter as anthropic_module

    captured: dict = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(anthropic_module, "AnthropicAdapter", _FakeAnthropic)
    _factory_adapter("anthropic", {"service_tier": "fast"})
    assert "service_tier" not in captured


@pytest.mark.parametrize("authored, requested", [
    ("fast", "priority"), ("default", "default"), (None, None),
])
def test_round_usage_carries_the_requested_tier_not_an_applied_one(authored, requested):
    # Neutral billing evidence: the wire tier each round REQUESTED (None when
    # none was sent), read from the dispatched kwargs. No provider response
    # field is consulted.
    defaults = {} if authored is None else {"service_tier": authored}
    responses = _factory_adapter("openai", {"wire_api": "responses", **defaults})
    responses._client = _FakeResponsesClient()
    session = responses._create_responses_session("gpt-test", "sys")
    assert session.send_stream("hello").usage.requested_service_tier == requested
    chat = _factory_adapter("openai", defaults)
    chat._client = _chat_client()
    assert chat._create_completions_session("gpt-test", "sys").send("hello").usage.requested_service_tier == requested


def test_requested_tier_is_a_per_request_snapshot_never_filled_from_current_config():
    adapter = _factory_adapter("openai", {"service_tier": "fast"})
    adapter._client = _chat_client()
    session = adapter._create_completions_session("gpt-test", "sys")
    assert session.send("one").usage.requested_service_tier == "priority"
    session._extra_kwargs.pop("service_tier")  # a later request that sends no tier
    assert session.send("two").usage.requested_service_tier is None
    assert "service_tier" not in adapter._client.chat.completions.create.call_args.kwargs


@pytest.mark.parametrize("streaming", [False, True], ids=["send", "send_stream"])
def test_chat_requested_tier_is_the_dispatched_snapshot_not_a_later_mutation(streaming):
    adapter = _factory_adapter("openai", {"service_tier": "fast"})
    client = _chat_client()
    raw = client.chat.completions.create.return_value
    adapter._client = client
    session = adapter._create_completions_session("gpt-test", "sys")
    sent: list[dict] = []

    def create(**kwargs):
        sent.append(kwargs)
        # The session's config changes while the request is in flight.
        session._extra_kwargs["service_tier"] = "default"
        return iter([SimpleNamespace(choices=[], usage=raw.usage)]) if streaming else raw

    client.chat.completions.create.side_effect = create
    response = session.send_stream("x") if streaming else session.send("x")
    assert sent[0]["service_tier"] == "priority"
    assert response.usage.requested_service_tier == "priority"


def test_direct_adapter_without_tier_sends_no_service_tier():
    adapter = OpenAIAdapter(api_key="fake", wire_api="responses")
    assert "service_tier" not in _responses_kwargs(adapter)
