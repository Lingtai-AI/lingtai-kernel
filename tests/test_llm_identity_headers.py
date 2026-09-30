from __future__ import annotations

from lingtai.llm.identity_headers import merge_lingtai_identity_headers


def test_identity_header_merge_preserves_caller_headers(monkeypatch):
    monkeypatch.setattr("lingtai.llm.identity_headers.lingtai_version", lambda: "9.8.7")

    headers = merge_lingtai_identity_headers(
        {"user-agent": "Caller/1", "X-LingTai-Version": "caller-version", "X-Test": "1"}
    )

    assert headers["user-agent"] == "Caller/1"
    assert headers["X-LingTai-Version"] == "caller-version"
    assert headers["X-Test"] == "1"
    assert "User-Agent" not in headers
    assert headers["X-LingTai-Client"] == "LingTai"


def test_identity_header_merge_can_skip_user_agent(monkeypatch):
    monkeypatch.setattr("lingtai.llm.identity_headers.lingtai_version", lambda: "9.8.7")

    headers = merge_lingtai_identity_headers(user_agent=False)

    assert "User-Agent" not in headers
    assert headers["X-LingTai-Client"] == "LingTai"
    assert headers["X-LingTai-Version"] == "9.8.7"



def test_anthropic_adapter_builds_client_with_identity_headers(monkeypatch):
    from lingtai.llm.anthropic import adapter as mod

    captured = {}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(mod.anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr("lingtai.llm.identity_headers.lingtai_version", lambda: "9.8.7")

    mod.AnthropicAdapter(api_key="sk-test", default_headers={"X-Test": "1"})

    headers = captured["default_headers"]
    assert headers["User-Agent"] == "LingTai/9.8.7"
    assert headers["X-LingTai-Client"] == "LingTai"
    assert headers["X-LingTai-Version"] == "9.8.7"
    assert headers["X-Test"] == "1"


def test_openai_and_anthropic_factories_forward_identity_headers_for_any_endpoint(monkeypatch):
    """Identity headers reach both generic families for official and compatible
    endpoints alike; caller ``default_headers`` merge over them."""
    from lingtai.llm.anthropic import adapter as anthropic_mod
    from lingtai.llm.openai import adapter as openai_mod
    from lingtai.llm.service import LLMService

    openai_captured: list[dict] = []
    anthropic_captured: list[dict] = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            openai_captured.append(kwargs)

    class FakeAnthropic:
        def __init__(self, **kwargs):
            anthropic_captured.append(kwargs)

    monkeypatch.setattr(openai_mod.openai, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(anthropic_mod.anthropic, "Anthropic", FakeAnthropic)
    monkeypatch.setattr("lingtai.llm.identity_headers.lingtai_version", lambda: "9.8.7")

    for provider, captured in (("openai", openai_captured), ("anthropic", anthropic_captured)):
        for base_url in (None, "https://example.invalid/v1"):
            LLMService(
                provider=provider,
                model="m",
                api_key="sk-test",
                base_url=base_url,
                provider_defaults={provider: {"default_headers": {"X-Test": provider}}},
            )
            headers = captured[-1]["default_headers"]
            assert headers["User-Agent"] == "LingTai/9.8.7"
            assert headers["X-LingTai-Client"] == "LingTai"
            assert headers["X-LingTai-Version"] == "9.8.7"
            assert headers["X-Test"] == provider


def test_no_provider_specific_user_agent_policy():
    """The retired ``kimi`` User-Agent special case is gone: only caller
    headers are forwarded (identity headers are merged inside adapters)."""
    from lingtai.llm.service import LLMService

    svc = object.__new__(LLMService)
    assert svc._default_headers_for("openai", None) is None
    assert svc._default_headers_for("openai", {"default_headers": {"A": "1"}}) == {"A": "1"}
