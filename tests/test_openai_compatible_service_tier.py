"""Tests that ``service_tier`` reaches OpenAI-compatible requests.

``llm.service_tier: "fast"`` used to be honored only by the Codex factory;
the ``openai`` and ``custom`` (``api_compat=openai``) factories silently
dropped it, so an agent pointed at a Codex-compatible proxy through the
``custom`` provider could never request the priority tier. Both factories now
normalize it at the same boundary (``fast`` -> wire ``priority``) and
``OpenAIAdapter`` adds it to Responses and Chat Completions requests.

These routes historically ignored the axis, so an unrecognized value stays
ignored instead of failing adapter construction.

No network: clients are fakes that record the kwargs they receive.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.llm._register import register_all_adapters
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


def _factory_adapter(provider: str, defaults: dict):
    register_all_adapters()
    factory = LLMService._adapter_registry[provider]
    kwargs = {"model": "gpt-test", "defaults": defaults, "api_key": "fake"}
    if provider == "custom":
        kwargs["base_url"] = "http://127.0.0.1:18766/v1"
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


_OPENAI_ROUTES = (
    ("openai", {"wire_api": "responses"}),
    ("custom", {"api_compat": "openai", "wire_api": "responses"}),
)


@pytest.mark.parametrize("provider,defaults", _OPENAI_ROUTES)
def test_fast_reaches_responses_request_as_priority(provider, defaults):
    adapter = _factory_adapter(provider, {**defaults, "service_tier": " fast "})
    assert _responses_kwargs(adapter)["service_tier"] == "priority"


@pytest.mark.parametrize("provider,defaults", _OPENAI_ROUTES)
def test_fast_reaches_chat_completions_request_as_priority(provider, defaults):
    adapter = _factory_adapter(provider, {**defaults, "service_tier": "fast"})
    assert _chat_kwargs(adapter)["service_tier"] == "priority"


@pytest.mark.parametrize("provider,defaults", _OPENAI_ROUTES)
@pytest.mark.parametrize("value", [None, "", "default", "unsupported"])
def test_absent_or_unrecognized_tier_is_omitted_without_error(
    provider, defaults, value
):
    route_defaults = dict(defaults)
    if value is not None:
        route_defaults["service_tier"] = value
    adapter = _factory_adapter(provider, route_defaults)
    assert "service_tier" not in _responses_kwargs(adapter)


@pytest.mark.parametrize("api_compat", ["anthropic", "gemini"])
def test_custom_non_openai_compat_does_not_receive_service_tier(
    monkeypatch, api_compat
):
    import lingtai.llm.custom.adapter as custom_adapter_module

    captured = {}

    def fake_create_custom_adapter(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        custom_adapter_module, "create_custom_adapter", fake_create_custom_adapter
    )
    _factory_adapter("custom", {"api_compat": api_compat, "service_tier": "fast"})
    assert captured["api_compat"] == api_compat
    assert "service_tier" not in captured


def test_direct_adapter_without_tier_sends_no_service_tier():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    assert "service_tier" not in _responses_kwargs(adapter)
