"""Regression tests for the ``openai`` provider's ``wire_api`` selector.

``wire_api`` selects ``chat_completions`` (default) or ``responses``; the
legacy value ``auto`` is accepted and means the same as omitting it. The
selector belongs to the ``openai`` provider only.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from lingtai.init_schema import validate_init
from lingtai.llm._register import register_all_adapters
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


@pytest.mark.parametrize("provider", ["anthropic", "claude-code", "codex"])
def test_schema_rejects_non_auto_wire_api_for_non_openai_providers(provider):
    with pytest.raises(ValueError, match="only for provider openai"):
        validate_init(_minimal_init({"provider": provider, "wire_api": "responses"}))


def test_schema_allows_wire_api_for_compatible_openai_endpoint():
    validate_init(_minimal_init({
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


def test_openai_defaults_metadata_selects_chat_completions():
    from lingtai.llm.openai.defaults import DEFAULTS

    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(model="gpt-5.5", defaults=DEFAULTS, api_key="fake")
    assert adapter._wire_api == "chat_completions"
    assert adapter._should_use_responses() is False


def test_bare_adapter_constructor_keeps_chat_completions():
    adapter = OpenAIAdapter(api_key="fake")
    assert adapter._should_use_responses() is False


def test_bare_llm_service_keeps_chat_completions():
    register_all_adapters()
    service = LLMService(provider="openai", model="gpt-5.5", api_key="fake")
    adapter = service.get_adapter("openai")
    assert adapter._wire_api == "chat_completions"
    assert adapter._should_use_responses() is False


@pytest.mark.parametrize("base_url", [None, "https://custom.example/v1"])
def test_omitted_and_legacy_auto_select_chat_completions_for_every_endpoint(base_url):
    """Neither an official nor a compatible endpoint implies a wire."""
    omitted = OpenAIAdapter(api_key="fake", base_url=base_url)
    auto = OpenAIAdapter(api_key="fake", base_url=base_url, wire_api="auto")
    assert omitted._should_use_responses() is False
    assert auto._should_use_responses() is False
    assert auto._wire_api == "chat_completions"


@pytest.mark.parametrize("base_url", [None, "https://custom.example/v1"])
def test_wire_api_responses_selects_responses_for_every_endpoint(base_url):
    adapter = OpenAIAdapter(api_key="fake", base_url=base_url, wire_api="responses")
    assert adapter._should_use_responses() is True


def test_adapter_rejects_unknown_wire_api():
    with pytest.raises(ValueError, match="wire_api"):
        OpenAIAdapter(api_key="fake", wire_api="grpc")


def test_adapter_no_longer_accepts_legacy_wire_flags():
    for legacy in ("use_responses", "force_responses", "responses_stateless_replay",
                   "reasoning_effort_vocab", "reasoning_policy"):
        with pytest.raises(TypeError):
            OpenAIAdapter(api_key="fake", **{legacy: True})


def test_openai_default_endpoint_is_official():
    from lingtai.llm.openai.adapter import OPENAI_OFFICIAL_BASE_URL

    assert OPENAI_OFFICIAL_BASE_URL == "https://api.openai.com/v1"
    adapter = OpenAIAdapter(api_key="fake")
    assert adapter.base_url is None
    assert adapter.effective_base_url == OPENAI_OFFICIAL_BASE_URL
    compat = OpenAIAdapter(api_key="fake", base_url="https://custom.example/v1")
    assert compat.effective_base_url == "https://custom.example/v1"


def test_codex_factory_is_unaffected_by_wire_api_in_defaults():
    """Codex is out of scope: a ``wire_api`` in provider defaults must NOT reach
    the Codex adapter. Codex stays forced Responses regardless."""
    from lingtai.llm.openai.adapter import CodexOpenAIAdapter

    register_all_adapters()
    factory = LLMService._adapter_registry["codex"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"wire_api": "chat_completions"},
        api_key="fake",
    )
    assert isinstance(adapter, CodexOpenAIAdapter)
    assert adapter._wire_api == "responses"
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
    adapter = OpenAIAdapter(api_key="fake", wire_api="chat_completions")
    adapter._client = _both_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    assert adapter._client.chat.completions.create.called is True
    assert adapter._client.responses.create.called is False


def test_omitted_wire_on_compatible_base_url_creates_chat_session():
    adapter = OpenAIAdapter(api_key="fake", base_url="https://custom.example/v1")
    adapter._client = _both_client()
    session = adapter.create_chat("gpt-5.5", "system prompt")

    session.send("hello")

    assert adapter._client.chat.completions.create.called is True
    assert adapter._client.responses.create.called is False


# ---------------------------------------------------------------------------
# Standard ``thinking`` passthrough on both wires
# ---------------------------------------------------------------------------
# ``thinking`` is sent verbatim as the standard field: Chat Completions
# ``reasoning_effort`` and Responses ``reasoning: {effort}``; the omitted /
# ``default`` sentinel sends no field so the endpoint's own default applies.

_LEVELS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]


def _chat_session_kwargs(*, thinking=None):
    adapter = OpenAIAdapter(api_key="fake", base_url="https://custom.example/v1")
    adapter._client = _both_client()
    session = adapter.create_chat(
        "gpt-5.5", "system prompt", thinking=thinking or "default"
    )
    session.send("hello")
    return adapter._client.chat.completions.create.call_args.kwargs


def _responses_session_kwargs(*, thinking=None, base_url=None):
    adapter = OpenAIAdapter(api_key="fake", base_url=base_url, wire_api="responses")
    adapter._client = _both_client()
    session = adapter.create_chat(
        "gpt-5.5", "system prompt", thinking=thinking or "default"
    )
    session.send("hello")
    return adapter._client.responses.create.call_args.kwargs


@pytest.mark.parametrize("thinking", _LEVELS)
def test_chat_completions_sends_thinking_verbatim(thinking):
    assert _chat_session_kwargs(thinking=thinking)["reasoning_effort"] == thinking


@pytest.mark.parametrize("thinking", [None, "default"])
def test_chat_completions_omits_reasoning_effort_on_default(thinking):
    assert "reasoning_effort" not in _chat_session_kwargs(thinking=thinking)


@pytest.mark.parametrize("thinking", _LEVELS)
@pytest.mark.parametrize("base_url", [None, "https://custom.example/v1"])
def test_responses_sends_thinking_verbatim(thinking, base_url):
    kwargs = _responses_session_kwargs(thinking=thinking, base_url=base_url)
    assert kwargs["reasoning"] == {"effort": thinking}
    assert "reasoning_effort" not in kwargs


@pytest.mark.parametrize("thinking", [None, "default"])
def test_responses_omits_reasoning_on_default(thinking):
    assert "reasoning" not in _responses_session_kwargs(thinking=thinking)


@pytest.mark.parametrize("wire", ["chat_completions", "responses"])
def test_invalid_thinking_is_rejected_on_both_wires(wire):
    adapter = OpenAIAdapter(api_key="fake", wire_api=wire)
    with pytest.raises(ValueError, match="thinking must be one of"):
        adapter.create_chat("gpt-5.5", "system prompt", thinking="ultra")


def test_codex_keeps_its_explicit_xhigh_default():
    """Only Codex substitutes an explicit ``xhigh`` for the omitted default."""
    from lingtai.llm.openai.adapter import _responses_reasoning_kwargs

    assert _responses_reasoning_kwargs("default") == {}
    assert _responses_reasoning_kwargs(None) == {}
    assert _responses_reasoning_kwargs("low") == {"reasoning": {"effort": "low"}}


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


def test_openai_factory_ignores_legacy_use_responses_api():
    """``use_responses_api`` is a recognized-and-ignored legacy manifest key:
    it no longer selects a wire."""
    register_all_adapters()
    defaults = build_provider_defaults_from_manifest_llm(
        {"provider": "openai", "model": "gpt-5.5", "use_responses_api": True},
        max_rpm=0,
    )
    assert defaults is None
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(model="gpt-5.5", defaults={}, api_key="fake")
    assert adapter._should_use_responses() is False


def test_openai_factory_passes_wire_api_for_compatible_endpoint():
    register_all_adapters()
    factory = LLMService._adapter_registry["openai"]
    adapter = factory(
        model="gpt-5.5",
        defaults={"wire_api": "responses"},
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
# Daemon preset propagation
# ---------------------------------------------------------------------------


def test_daemon_llm_defaults_from_manifest_retains_wire_api():
    from lingtai.tools.daemon import DaemonManager

    # _llm_defaults_from_manifest is a static method; no instance needed.
    llm = {
        "provider": "openai",
        "model": "gpt-5.5",
        "base_url": "https://openrouter.ai/api/v1",
        "wire_api": "responses",
        "max_rpm": 60,
    }
    defaults = DaemonManager._llm_defaults_from_manifest(llm)
    assert defaults["wire_api"] == "responses"


def test_parse_chat_completion_rejects_non_object_response():
    """A scalar string from a non-conforming gateway yields a typed error.

    Regression for the detached-daemon misroute: when a provider configured
    with ``wire_api: responses`` was reconstructed as ``auto`` and routed to
    ``/chat/completions``, a scalar-string response crashed with
    ``AttributeError: 'str' object has no attribute 'choices'``. The adapter
    must reject the wrong-typed payload with a clear TypeError instead of
    dereferencing ``.choices`` on it.
    """
    import pytest

    from lingtai.llm.openai.adapter import _parse_response

    with pytest.raises(TypeError, match="non-ChatCompletion"):
        _parse_response("{\"error\": \"not a chat completion\"}")
