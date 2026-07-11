"""Regression tests for the canonical ``wire_api`` OpenAI wire selector."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.init_schema import validate_init
from lingtai.llm._register import register_all_adapters
from lingtai.llm.custom.adapter import create_custom_adapter
from lingtai.llm.openai.adapter import OpenAIAdapter
from lingtai.llm.service import LLMService, build_provider_defaults_from_manifest_llm


# ---------------------------------------------------------------------------
# init.json schema validation
# ---------------------------------------------------------------------------


def _minimal_init(llm_extra: dict | None = None) -> dict:
    return {
        "manifest": {
            "llm": {
                "provider": "openai",
                "model": "gpt-5.5",
                **(llm_extra or {}),
            },
        },
        "covenant": "",
        "pad": "",
    }


@pytest.mark.parametrize("value", ["auto", "chat_completions", "responses"])
def test_schema_accepts_all_wire_api_values(value):
    validate_init(_minimal_init({"wire_api": value}))


def test_schema_rejects_invalid_wire_api_value():
    with pytest.raises(ValueError, match="wire_api"):
        validate_init(_minimal_init({"wire_api": "unknown"}))


def test_schema_rejects_non_string_wire_api_value():
    with pytest.raises(ValueError, match="wire_api"):
        validate_init(_minimal_init({"wire_api": 123}))


@pytest.mark.parametrize("provider", ["anthropic", "gemini", "minimax", "claude-code", "codex", "codex-pool"])
def test_schema_rejects_non_auto_wire_api_for_non_openai_providers(provider):
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        validate_init(_minimal_init({"provider": provider, "wire_api": "responses"}))


def test_schema_rejects_non_auto_wire_api_for_custom_anthropic_compat():
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        validate_init(_minimal_init({
            "provider": "custom",
            "api_compat": "anthropic",
            "base_url": "https://bedrock.example",
            "wire_api": "responses",
        }))


def test_schema_allows_wire_api_for_custom_openai_compat():
    validate_init(_minimal_init({
        "provider": "custom",
        "api_compat": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "wire_api": "responses",
    }))


def test_schema_auto_is_allowed_for_non_openai_providers():
    validate_init(_minimal_init({"provider": "anthropic", "wire_api": "auto"}))


# ---------------------------------------------------------------------------
# OpenAIAdapter wire selection
# ---------------------------------------------------------------------------


def _chat_raw():
    msg = SimpleNamespace(content="ok", reasoning_content=None, tool_calls=[])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )


def _responses_raw():
    return SimpleNamespace(
        id="resp_fake",
        output=[SimpleNamespace(type="output_text", text="ok")],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )


def _both_client():
    """Fake openai client that records both Chat Completions and Responses calls."""
    client = MagicMock()
    client.chat.completions.create.return_value = _chat_raw()
    client.responses.create.return_value = _responses_raw()
    return client


def test_openai_defaults_metadata_auto_delegates_to_legacy_responses():
    """Consumers that inject ``openai/defaults.py`` retain its legacy preference.

    The metadata carries both ``use_responses_api=True`` and ``wire_api="auto"``;
    the canonical selector delegates to the legacy flag. Bare service/adapter
    construction does not load this mapping and is covered separately below.
    """
    from lingtai.llm.openai.defaults import DEFAULTS

    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(model="gpt-5.5", defaults=DEFAULTS, api_key="fake")
    assert adapter._use_responses is True
    assert adapter._wire_api == "auto"
    assert adapter._should_use_responses() is True


def test_bare_adapter_constructor_keeps_chat_completions():
    adapter = OpenAIAdapter(api_key="fake")
    assert adapter._should_use_responses() is False


def test_bare_llm_service_keeps_chat_completions():
    """The runtime service does not implicitly load ``openai/defaults.py``."""
    register_all_adapters()
    service = LLMService(provider="openai", model="gpt-5.5", api_key="fake")
    adapter = service.get_adapter("openai")
    assert adapter._wire_api == "auto"
    assert adapter._use_responses is False
    assert adapter._should_use_responses() is False


def test_auto_custom_base_url_uses_chat_completions():
    """Custom OpenAI-compatible endpoints fall back to Chat Completions."""
    adapter = OpenAIAdapter(api_key="fake", base_url="https://custom.example/v1")
    assert adapter._should_use_responses() is False


def test_auto_with_use_responses_and_no_base_url_uses_responses():
    """Legacy ``use_responses=True`` without a custom base_url selects Responses."""
    adapter = OpenAIAdapter(api_key="fake", use_responses=True)
    assert adapter._should_use_responses() is True


def test_auto_with_force_responses_and_base_url_uses_responses():
    """Legacy ``force_responses=True`` allows Responses even with a custom base_url."""
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        use_responses=True,
        force_responses=True,
    )
    assert adapter._should_use_responses() is True


def test_wire_api_responses_overrides_chat_default():
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        wire_api="responses",
    )
    assert adapter._should_use_responses() is True


def test_wire_api_chat_completions_overrides_responses_default():
    adapter = OpenAIAdapter(
        api_key="fake",
        use_responses=True,
        force_responses=True,
        wire_api="chat_completions",
    )
    assert adapter._should_use_responses() is False


def test_codex_factory_is_unaffected_by_wire_api_in_defaults():
    """Codex is out of scope: a ``wire_api`` in provider defaults must NOT reach
    the Codex adapter. Codex stays forced Responses regardless."""
    from lingtai.llm.openai.adapter import CodexOpenAIAdapter

    register_all_adapters()
    factory = LLMService._adapter_registry["codex"]
    # Even an explicit non-auto value in defaults must be ignored by the Codex
    # factory — it never forwards wire_api to CodexOpenAIAdapter.
    adapter = factory(
        model="gpt-5.5",
        defaults={"wire_api": "chat_completions"},
        api_key="fake",
    )
    assert isinstance(adapter, CodexOpenAIAdapter)
    # Codex is forcibly on Responses; its constructor default leaves _wire_api
    # at ``auto`` because the factory does not pass wire_api through.
    assert adapter._wire_api == "auto"
    assert adapter._should_use_responses() is True


def test_wire_api_auto_preserves_legacy_responses_preference():
    adapter = OpenAIAdapter(
        api_key="fake",
        use_responses=True,
        force_responses=True,
        wire_api="auto",
    )
    assert adapter._should_use_responses() is True


# ---------------------------------------------------------------------------
# Session creation follows wire_api
# ---------------------------------------------------------------------------


def test_custom_base_url_responses_creates_responses_session():
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        wire_api="responses",
    )
    adapter._client = _both_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    assert adapter._client.responses.create.called is True
    assert adapter._client.chat.completions.create.called is False


def test_chat_completions_explicit_creates_chat_session():
    adapter = OpenAIAdapter(
        api_key="fake",
        use_responses=True,
        wire_api="chat_completions",
    )
    adapter._client = _both_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    assert adapter._client.chat.completions.create.called is True
    assert adapter._client.responses.create.called is False


def test_auto_custom_base_url_creates_chat_session():
    adapter = OpenAIAdapter(api_key="fake", base_url="https://custom.example/v1")
    adapter._client = _both_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    assert adapter._client.chat.completions.create.called is True
    assert adapter._client.responses.create.called is False


# ---------------------------------------------------------------------------
# One-shot generate() consistency
# ---------------------------------------------------------------------------


def test_generate_uses_chat_completions_by_default():
    adapter = OpenAIAdapter(api_key="fake", base_url="https://custom.example/v1")
    adapter._client = _both_client()

    adapter.generate("gpt-5.5", "hello", system_prompt="be brief")

    assert adapter._client.chat.completions.create.called is True
    assert adapter._client.responses.create.called is False


def test_generate_uses_responses_when_wire_api_responses():
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        wire_api="responses",
    )
    adapter._client = _both_client()

    adapter.generate("gpt-5.5", "hello", system_prompt="be brief")

    assert adapter._client.responses.create.called is True
    assert adapter._client.chat.completions.create.called is False


def test_generate_responses_passes_system_prompt_and_parameters():
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        wire_api="responses",
    )
    adapter._client = _both_client()

    adapter.generate(
        "gpt-5.5",
        "hello",
        system_prompt="be brief",
        temperature=0.5,
        max_output_tokens=100,
    )

    kwargs = adapter._client.responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert kwargs["instructions"] == "be brief"
    assert kwargs["temperature"] == 0.5
    assert kwargs["max_output_tokens"] == 100
    assert kwargs["input"] == [{"role": "user", "content": "hello"}]


# ---------------------------------------------------------------------------
# Factory and LLMService propagation
# ---------------------------------------------------------------------------


def test_openai_factory_passes_wire_api_from_defaults():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"wire_api": "responses"},
        api_key="fake",
    )
    assert adapter._wire_api == "responses"


def test_openai_factory_legacy_use_responses_api_still_works():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"use_responses_api": True},
        api_key="fake",
    )
    assert adapter._use_responses is True
    assert adapter._should_use_responses() is True


def test_openai_factory_wire_api_wins_over_legacy_use_responses_api():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"wire_api": "chat_completions", "use_responses_api": True},
        api_key="fake",
    )
    assert adapter._wire_api == "chat_completions"
    assert adapter._should_use_responses() is False


def test_custom_factory_passes_wire_api_for_openai_compat():
    register_all_adapters()
    factory = LLMService._adapter_registry["custom"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"api_compat": "openai", "wire_api": "responses"},
        api_key="fake",
        base_url="https://openrouter.ai/api/v1",
    )
    assert adapter._wire_api == "responses"


def test_llm_service_threads_wire_api_via_provider_defaults():
    register_all_adapters()
    service = LLMService(
        provider="openai",
        model="gpt-5.5",
        api_key="fake",
        provider_defaults={"openai": {"wire_api": "responses"}},
    )
    adapter = service.get_adapter("openai")
    assert adapter._wire_api == "responses"


def test_llm_service_generate_follows_wire_api_responses():
    register_all_adapters()
    service = LLMService(
        provider="openai",
        model="gpt-5.5",
        api_key="fake",
        provider_defaults={"openai": {"wire_api": "responses"}},
    )
    adapter = service.get_adapter("openai")
    adapter._client = _both_client()

    service.generate("hello", model="gpt-5.5")

    assert adapter._client.responses.create.called is True
    assert adapter._client.chat.completions.create.called is False


def test_manifest_llm_wire_api_propagates_to_provider_defaults():
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "openai", "model": "gpt-5.5", "wire_api": "responses"},
        max_rpm=0,
    )
    assert defaults == {"openai": {"wire_api": "responses"}}


def test_manifest_llm_without_wire_api_omits_it_from_provider_defaults():
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "openai", "model": "gpt-5.5"},
        max_rpm=0,
    )
    assert defaults is None


def test_generate_responses_json_schema_uses_text_not_response_format():
    """The Responses API selects structured output via ``text.format`` (openai
    >=2.x); it has NO ``response_format`` kwarg. Assert ``text`` is shaped
    correctly and ``response_format`` is absent."""
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        wire_api="responses",
    )
    adapter._client = _both_client()

    schema = {"type": "object", "title": "Answer", "properties": {"x": {"type": "integer"}}}
    adapter.generate("gpt-5.5", "hello", json_schema=schema)

    kwargs = adapter._client.responses.create.call_args.kwargs
    assert "response_format" not in kwargs
    assert kwargs["text"] == {
        "format": {
            "type": "json_schema",
            "name": "Answer",
            "schema": schema,
            "strict": True,
        },
    }


def test_generate_chat_completions_json_schema_keeps_response_format():
    """Chat Completions one-shot still uses the ``response_format`` kwarg (its
    structured-output shape is unchanged)."""
    adapter = OpenAIAdapter(
        api_key="fake",
        base_url="https://custom.example/v1",
        wire_api="chat_completions",
    )
    adapter._client = _both_client()

    schema = {"type": "object", "title": "Answer"}
    adapter.generate("gpt-5.5", "hello", json_schema=schema)

    kwargs = adapter._client.chat.completions.create.call_args.kwargs
    assert "text" not in kwargs
    assert kwargs["response_format"]["type"] == "json_schema"
    assert kwargs["response_format"]["json_schema"]["strict"] is True


# ---------------------------------------------------------------------------
# Identity / .agent.json safelist: wire_api must NOT be surfaced
# ---------------------------------------------------------------------------


def test_safe_llm_from_service_does_not_surface_wire_api():
    """wire_api is an init/provider concern, not a public identity surface; it
    must never reach ``.agent.json`` or the identity prompt section."""
    from lingtai.kernel.base_agent.identity import _safe_llm_from_service

    class FakeService:
        provider = "openai"
        model = "gpt-5.5"
        _base_url = None
        _context_window = 1_000_000
        _provider_defaults = {"openai": {"wire_api": "responses"}}

    class FakeAgent:
        service = FakeService()

    llm = _safe_llm_from_service(FakeAgent())
    assert "wire_api" not in llm


# ---------------------------------------------------------------------------
# Custom adapter scoping / non-OpenAI misuse
# ---------------------------------------------------------------------------


def test_custom_adapter_openai_compat_honors_legacy_responses_flags():
    """When wire_api is absent, create_custom_adapter still honors legacy flags."""
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="openai",
        base_url="https://custom.example/v1",
        use_responses=True,
        force_responses=True,
    )
    assert adapter._should_use_responses() is True


def test_custom_adapter_rejects_non_auto_wire_api_for_anthropic():
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        create_custom_adapter(
            api_key="fake",
            api_compat="anthropic",
            base_url="https://bedrock.example",
            wire_api="responses",
        )


def test_custom_adapter_allows_auto_wire_api_for_anthropic():
    """wire_api=auto is harmless for non-OpenAI compat and should be ignored."""
    adapter = create_custom_adapter(
        api_key="fake",
        api_compat="anthropic",
        base_url="https://bedrock.example",
        wire_api="auto",
    )
    assert not hasattr(adapter, "_wire_api")


def test_custom_adapter_rejects_non_auto_wire_api_for_gemini():
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        create_custom_adapter(
            api_key="fake",
            api_compat="gemini",
            wire_api="chat_completions",
        )


# ---------------------------------------------------------------------------
# Daemon preset propagation
# ---------------------------------------------------------------------------


def test_daemon_llm_defaults_from_manifest_retains_wire_api():
    from tools.daemon import DaemonManager

    # _llm_defaults_from_manifest is a static method; no instance needed.
    llm = {
        "provider": "custom",
        "model": "gpt-5.5",
        "api_compat": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "wire_api": "responses",
        "max_rpm": 60,
    }
    defaults = DaemonManager._llm_defaults_from_manifest(llm)
    assert defaults["wire_api"] == "responses"
