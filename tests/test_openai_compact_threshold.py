"""Tests that the OpenAI Responses adapter honors the configured
``compact_threshold`` end to end.

Regression coverage for the bug where ``_create_responses_session`` read the
threshold via ``from config import get`` against a non-existent top-level
``config`` module, swallowed the resulting ``ImportError``, and therefore left
``compact_threshold`` permanently ``None`` — silently disabling Responses-API
auto-compaction regardless of configuration.

No network: the Responses client is a fake that records the kwargs it receives.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from lingtai.init_schema import validate_init
from lingtai.llm.openai.adapter import OpenAIAdapter, _parse_response
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


class _FakeChatCompletions:
    def __init__(self) -> None:
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        message = SimpleNamespace(
            content="ok",
            tool_calls=None,
            reasoning_content=None,
            reasoning=None,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=None,
        )


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeChatCompletions()


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()
        self.chat = _FakeChat()


# -- Default behavior is preserved (100000) --------------------------------


def test_responses_session_default_compact_threshold_is_100000():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    session = adapter._create_responses_session("gpt-5.5", "sys")
    assert session._compact_threshold == 100000


# -- A configured value propagates into the session ------------------------


def test_responses_session_honors_configured_compact_threshold():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, compact_threshold=12345)
    session = adapter._create_responses_session("gpt-5.5", "sys")
    assert session._compact_threshold == 12345


def test_configured_compact_threshold_can_be_disabled_with_none():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, compact_threshold=None)
    session = adapter._create_responses_session("gpt-5.5", "sys")
    assert session._compact_threshold is None


@pytest.mark.parametrize("bad_value", [0, -1, True, False, "100000"])
def test_invalid_compact_threshold_values_are_rejected(bad_value):
    with pytest.raises(ValueError, match="compact_threshold"):
        OpenAIAdapter(api_key="fake", use_responses=True, compact_threshold=bad_value)


# -- The threshold actually reaches the Responses wire ---------------------


def test_compact_threshold_reaches_responses_wire():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, compact_threshold=777)
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-5.5", "sys")

    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert sent["context_management"] == [
        {"type": "compaction", "compact_threshold": 777}
    ]


def test_compact_threshold_reaches_non_streaming_responses_wire():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, compact_threshold=888)
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-5.5", "sys")

    session.send("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert sent["context_management"] == [
        {"type": "compaction", "compact_threshold": 888}
    ]


def test_no_context_management_when_threshold_disabled():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, compact_threshold=None)
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-5.5", "sys")

    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert "context_management" not in sent


def test_service_tier_reaches_responses_wire():
    adapter = OpenAIAdapter(api_key="fake", use_responses=True, service_tier=" fast ")
    adapter._client = _FakeClient()
    session = adapter._create_responses_session("gpt-5.5", "sys")

    session.send_stream("hello")

    sent = adapter._client.responses.kwargs[-1]
    assert sent["service_tier"] == "fast"


def test_service_tier_reaches_chat_completions_wire():
    adapter = OpenAIAdapter(api_key="fake", service_tier="fast")
    adapter._client = _FakeClient()
    session = adapter._create_completions_session("gpt-5.5", "sys")

    session.send("hello")

    sent = adapter._client.chat.completions.kwargs[-1]
    assert sent["service_tier"] == "fast"


def test_service_tier_reaches_generate_wire():
    adapter = OpenAIAdapter(api_key="fake", service_tier="fast")
    adapter._client = _FakeClient()

    adapter.generate("gpt-5.5", "hello")

    sent = adapter._client.chat.completions.kwargs[-1]
    assert sent["service_tier"] == "fast"


# -- Config flows from the injected provider defaults (host config path) ---


def test_openai_factory_passes_compact_threshold_from_defaults():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"compact_threshold": 250},
        api_key="fake",
    )
    assert adapter._compact_threshold == 250


def test_openai_factory_defaults_to_100000_without_config():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(model="gpt-5.5", defaults={}, api_key="fake")
    assert adapter._compact_threshold == 100000


def test_openai_factory_preserves_explicit_none_disable():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"compact_threshold": None},
        api_key="fake",
    )
    assert adapter._compact_threshold is None


def test_openai_factory_passes_wire_api_responses_from_defaults():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"wire_api": "responses"},
        api_key="fake",
    )
    assert adapter._use_responses is True
    assert adapter._force_responses is True


def test_custom_factory_defaults_to_auto_without_wire_api():
    register_all_adapters()
    factory = LLMService._adapter_registry["custom"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"api_compat": "openai"},
        api_key="fake",
        base_url="https://example.test/v1",
    )
    assert adapter._use_responses is False
    assert adapter._force_responses is False


def test_custom_factory_wire_api_responses_forces_responses_with_base_url():
    register_all_adapters()
    factory = LLMService._adapter_registry["custom"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"api_compat": "openai", "wire_api": "responses"},
        api_key="fake",
        base_url="https://example.test/v1",
    )
    assert adapter._use_responses is True
    assert adapter._force_responses is True


def test_nvidia_factory_uses_openai_compat_without_prompt_cache_key():
    register_all_adapters()
    factory = LLMService._adapter_registry["nvidia"]
    adapter = factory(
        model="meta/llama-3.3-70b-instruct",
        defaults={"api_compat": "openai"},
        api_key="fake",
        base_url="https://integrate.api.nvidia.com/v1",
    )

    assert isinstance(adapter, OpenAIAdapter)
    assert adapter._prompt_cache_key_policy is False


def test_custom_factory_drops_openai_controls_for_anthropic_compat():
    from lingtai.llm.anthropic.adapter import AnthropicAdapter

    register_all_adapters()
    factory = LLMService._adapter_registry["custom"]
    adapter = factory(
        model="claude-test",
        defaults={
            "api_compat": "anthropic",
            "wire_api": "responses",
            "service_tier": "fast",
            "use_responses_api": True,
            "use_responses": True,
            "force_responses": True,
        },
        api_key="fake",
        base_url="https://example.test",
    )

    assert isinstance(adapter, AnthropicAdapter)


def test_codex_factory_passes_service_tier_from_defaults(monkeypatch):
    import lingtai.auth.codex as codex_auth

    class _FakeCodexTokenManager:
        _path = None

        def __init__(self, **kwargs):
            pass

        def get_access_token(self):
            return "fake"

        def get_account_id(self):
            return None

    monkeypatch.setattr(codex_auth, "CodexTokenManager", _FakeCodexTokenManager)
    register_all_adapters()
    factory = LLMService._adapter_registry["codex"]

    adapter = factory(
        model="gpt-5.5",
        defaults={"service_tier": "fast"},
    )

    assert adapter._service_tier == "fast"


def test_llm_service_threads_compact_threshold_via_provider_defaults():
    register_all_adapters()
    service = LLMService(
        provider="openai",
        model="gpt-5.5",
        api_key="fake",
        provider_defaults={"openai": {"compact_threshold": 4321}},
    )
    adapter = service.get_adapter("openai")
    assert adapter._compact_threshold == 4321


# -- Manifest llm block propagates the value into provider defaults --------


def test_manifest_llm_compact_threshold_propagates_to_provider_defaults():
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "openai", "compact_threshold": 250},
        max_rpm=0,
    )
    assert defaults == {"openai": {"compact_threshold": 250}}


def test_manifest_llm_wire_api_propagates_to_provider_defaults():
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "custom", "wire_api": "responses"},
        max_rpm=0,
    )
    assert defaults == {"custom": {"wire_api": "responses"}}


def test_manifest_llm_explicit_none_compact_threshold_is_preserved():
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "openai", "compact_threshold": None},
        max_rpm=0,
    )
    assert defaults == {"openai": {"compact_threshold": None}}

# -- init.json schema validation for manifest.llm.compact_threshold --------


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


@pytest.mark.parametrize("value", [1, 100000, None])
def test_init_schema_accepts_valid_compact_threshold(value):
    validate_init(_minimal_init_with_compact_threshold(value))


@pytest.mark.parametrize("value", [0, -1, True, False, "100000"])
def test_init_schema_rejects_invalid_compact_threshold(value):
    with pytest.raises(ValueError, match="manifest.llm.compact_threshold"):
        validate_init(_minimal_init_with_compact_threshold(value))


@pytest.mark.parametrize("wire_api", ["chat_completions", "responses", "auto"])
def test_init_schema_accepts_wire_api(wire_api):
    data = _minimal_init_with_compact_threshold(None)
    data["manifest"]["llm"]["wire_api"] = wire_api
    validate_init(data)


def test_init_schema_accepts_openai_controls_for_nvidia():
    data = _minimal_init_with_compact_threshold(None)
    llm = data["manifest"]["llm"]
    llm.update({
        "provider": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_compat": "openai",
        "wire_api": "chat_completions",
        "service_tier": "fast",
    })

    validate_init(data)


def test_init_schema_rejects_invalid_wire_api():
    data = _minimal_init_with_compact_threshold(None)
    data["manifest"]["llm"]["wire_api"] = "completions"
    with pytest.raises(ValueError, match="manifest.llm.wire_api"):
        validate_init(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("wire_api", "responses"),
        ("service_tier", "fast"),
        ("use_responses_api", True),
        ("use_responses", True),
        ("force_responses", True),
    ],
)
def test_init_schema_rejects_openai_controls_for_anthropic_compat(field, value):
    data = _minimal_init_with_compact_threshold(None)
    llm = data["manifest"]["llm"]
    llm.update({"provider": "custom", "api_compat": "anthropic", field: value})

    with pytest.raises(ValueError, match="supported only for OpenAI-compatible"):
        validate_init(data)


def test_init_schema_accepts_service_tier_for_codex():
    data = _minimal_init_with_compact_threshold(None)
    llm = data["manifest"]["llm"]
    llm.update({"provider": "codex", "service_tier": "fast"})

    validate_init(data)


def test_init_schema_rejects_wire_api_for_fixed_responses_codex():
    data = _minimal_init_with_compact_threshold(None)
    llm = data["manifest"]["llm"]
    llm.update({"provider": "codex", "wire_api": "responses"})

    with pytest.raises(ValueError, match="configurable wire protocols"):
        validate_init(data)


def test_init_schema_rejects_service_tier_for_openrouter():
    data = _minimal_init_with_compact_threshold(None)
    llm = data["manifest"]["llm"]
    llm.update({"provider": "openrouter", "service_tier": "fast"})

    with pytest.raises(ValueError, match="OpenAI-compatible or Codex"):
        validate_init(data)


@pytest.mark.parametrize("provider", ["minimax", "deepseek", "zhipu", "mimo", "openrouter"])
@pytest.mark.parametrize("field", ["wire_api", "service_tier"])
def test_init_schema_rejects_openai_controls_for_native_providers(provider, field):
    data = _minimal_init_with_compact_threshold(None)
    llm = data["manifest"]["llm"]
    llm.update({"provider": provider, "api_compat": "openai", field: "fast"})
    if field == "wire_api":
        llm[field] = "responses"

    with pytest.raises(ValueError, match="supported only for OpenAI-compatible|OpenAI-compatible or Codex"):
        validate_init(data)


def test_chat_completion_wrong_shape_reports_wire_api_hint():
    with pytest.raises(TypeError, match='wire_api="responses"'):
        _parse_response("not a chat completion")
