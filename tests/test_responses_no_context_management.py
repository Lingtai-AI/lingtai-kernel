"""Tests that no LingTai Responses request carries ``context_management``.

The generic OpenAI Responses auto-compaction axis (``compact_threshold`` →
``context_management: [{"type": "compaction", ...}]``) was removed. Its old
100k default fired server-side compaction on every turn of any agent whose
context exceeded the threshold (e.g. an OpenAI-compatible provider pointed at
a Codex-compatible proxy), rewriting the context prefix each turn and driving
prompt-cache hits to zero. Codex keeps its separate standalone
``/responses/compact`` path; nothing sends ``context_management``.

Existing configs that still carry ``manifest.llm.compact_threshold`` must keep
validating and booting: the key is recognized-and-ignored.

No network: the Responses client is a fake that records the kwargs it receives.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from lingtai.init_schema import validate_init
from lingtai.llm.openai.adapter import OpenAIAdapter, OpenAIResponsesSession
from lingtai.llm.service import (
    LLMService,
    build_provider_defaults_from_manifest_llm,
)
from lingtai.llm._register import register_all_adapters


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


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


# -- The knob no longer exists ----------------------------------------------


def test_openai_adapter_has_no_compact_threshold_parameter():
    assert "compact_threshold" not in inspect.signature(OpenAIAdapter).parameters
    with pytest.raises(TypeError):
        OpenAIAdapter(api_key="fake", wire_api="responses", compact_threshold=100_000)


def test_responses_session_has_no_compact_threshold_parameter():
    assert (
        "compact_threshold"
        not in inspect.signature(OpenAIResponsesSession).parameters
    )


# -- Neither Responses wire sends context_management ------------------------


def test_streaming_responses_request_has_no_context_management():
    adapter = OpenAIAdapter(api_key="fake", wire_api="responses")
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-5.5", "sys")

    session.send_stream("hello")

    assert "context_management" not in adapter._client.responses.kwargs[-1]


def test_non_streaming_responses_request_has_no_context_management():
    adapter = OpenAIAdapter(api_key="fake", wire_api="responses")
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-5.5", "sys")

    session.send("hello")

    assert "context_management" not in adapter._client.responses.kwargs[-1]


# -- Legacy provider defaults are ignored, never forwarded ------------------


@pytest.mark.parametrize("value", [250, None])
def test_openai_factory_ignores_legacy_compact_threshold(value):
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"compact_threshold": value},
        api_key="fake",
    )
    assert isinstance(adapter, OpenAIAdapter)
    assert not hasattr(adapter, "_compact_threshold")


def test_compatible_openai_responses_session_sends_no_context_management():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-6.1-sol",
        defaults={
            "wire_api": "responses",
            "compact_threshold": 250,
        },
        api_key="fake",
        base_url="http://127.0.0.1:18766/v1",
    )
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-6.1-sol", "sys")

    session.send_stream("hello")

    assert "context_management" not in adapter._client.responses.kwargs[-1]


@pytest.mark.parametrize("value", [250, None])
def test_manifest_llm_compact_threshold_is_dropped_from_provider_defaults(value):
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "openai", "compact_threshold": value},
        max_rpm=0,
    )
    assert defaults is None


# -- init.json keeps validating with the retired key ------------------------


def _minimal_init_with_compact_threshold(value):
    return {
        "manifest": {
            "llm": {
                "provider": "openai",
                "model": "gpt-5.5",
                "compact_threshold": value,
            },
        },
        "principle": "",
        "covenant": "",
        "pad": "",
        "lingtai": "",
    }


@pytest.mark.parametrize("value", [1, 100000, None, 0, -1, True, "100000"])
def test_init_schema_tolerates_retired_compact_threshold_without_warning(value):
    warnings = validate_init(_minimal_init_with_compact_threshold(value))
    assert not any("compact_threshold" in w for w in warnings)
