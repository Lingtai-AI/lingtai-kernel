"""Tests for vision capability and VisionService."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.tools.vision import PROVIDERS, VisionManager, setup
from lingtai.services.vision import VisionService, create_vision_service


def make_mock_service():
    svc = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    svc.api_key = None
    svc._key_resolver = MagicMock(return_value="fake-key")
    return svc


def make_mock_agent(tmp_path, svc=None):
    agent = MagicMock()
    agent.service = svc or make_mock_service()
    agent._config = MagicMock()
    agent._config.language = "en"
    agent._working_dir = tmp_path
    return agent


def make_provider_agent(
    tmp_path,
    *,
    provider: str,
    model: str | None,
    base_url: str | None,
    defaults: dict | None = None,
):
    svc = MagicMock()
    svc.provider = provider
    svc._model = model
    svc._base_url = base_url
    svc._provider_defaults = defaults if defaults is not None else {provider: {}}
    svc.api_key = None
    svc._key_resolver = MagicMock(return_value="fake-key")
    return make_mock_agent(tmp_path, svc=svc)


def test_vision_added_by_setup(tmp_path):
    """setup() should register the vision tool on the agent."""
    mock_svc = MagicMock(spec=VisionService)
    agent = make_mock_agent(tmp_path)
    mgr = setup(agent, vision_service=mock_svc)
    agent.add_tool.assert_called_once()
    assert agent.add_tool.call_args[1]["schema"] is not None or agent.add_tool.call_args[0][1] is not None
    assert isinstance(mgr, VisionManager)


def test_vision_with_dedicated_service(tmp_path):
    """Vision capability should use VisionService if provided."""
    mock_vision_svc = MagicMock(spec=VisionService)
    mock_vision_svc.analyze_image.return_value = "A dog in the park"

    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)

    img_path = tmp_path / "test.jpg"
    img_path.write_bytes(b"\xff\xd8\xff fake jpeg")
    result = mgr.handle({"image_path": str(img_path)})
    assert result["status"] == "ok"
    assert "dog" in result["analysis"]
    mock_vision_svc.analyze_image.assert_called_once()


def test_vision_missing_image(tmp_path):
    """Vision should return error for missing image file."""
    mock_vision_svc = MagicMock(spec=VisionService)
    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)
    result = mgr.handle({"image_path": "/nonexistent/image.png"})
    assert result.get("status") == "error"


def test_vision_relative_path(tmp_path):
    """VisionManager should resolve relative paths against working directory."""
    mock_vision_svc = MagicMock(spec=VisionService)
    mock_vision_svc.analyze_image.return_value = "An image"

    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)
    img_path = tmp_path / "photo.png"
    img_path.write_bytes(b"\x89PNG fake")
    result = mgr.handle({"image_path": "photo.png"})
    assert result["status"] == "ok"
    mock_vision_svc.analyze_image.assert_called_once_with(str(img_path), prompt="Describe what you see in this image.")


def test_vision_service_error_handled(tmp_path):
    """VisionManager should catch VisionService exceptions and return error dict."""
    mock_vision_svc = MagicMock(spec=VisionService)
    mock_vision_svc.analyze_image.side_effect = RuntimeError("API down")

    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)
    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG fake")
    result = mgr.handle({"image_path": str(img_path)})
    assert result["status"] == "error"
    assert "API down" not in result["message"]
    assert "RuntimeError" in result["message"]


def test_vision_service_error_does_not_echo_secret_or_url(tmp_path):
    mock_vision_svc = MagicMock(spec=VisionService)
    mock_vision_svc.analyze_image.side_effect = RuntimeError(
        "token=secret https://user:pw@example.test/v1"
    )
    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)
    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"fake")
    result = mgr.handle({"image_path": str(img_path)})
    assert result["status"] == "error"
    assert "secret" not in result["message"]
    assert "example.test" not in result["message"]


@pytest.mark.parametrize(
    "provider",
    ["openrouter", "deepseek", "zhipu", "glm", "grok", "qwen", "kimi", "custom"],
)
def test_compatible_aliases_build_current_openai_route(tmp_path, provider):
    headers = {"X-Preset": "active"}
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider=provider,
            model="vision-current",
            base_url="https://relay.example/v1",
            defaults={provider: {"api_compat": "openai", "default_headers": headers, "wire_api": "chat_completions"}},
        )
        setup(agent, provider=provider, api_key="sk-test")
    assert mock_factory.call_args.args == ("openai",)
    assert mock_factory.call_args.kwargs == {
        "api_key": "sk-test",
        "model": "vision-current",
        "base_url": "https://relay.example/v1",
        "default_headers": headers,
        "wire_api": "chat_completions",
    }


@pytest.mark.parametrize("wire_api", ["auto", "", " \t "])
@pytest.mark.parametrize(
    ("base_url", "expected_wire"),
    [
        (None, "responses"),
        ("https://openai-compatible.example/v1", "chat_completions"),
    ],
)
def test_openai_automatic_wire_values_preserve_active_effective_route(
    tmp_path, wire_api, base_url, expected_wire
):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-5.4",
            base_url=base_url,
            defaults={
                "openai": {
                    "api_compat": "openai",
                    "wire_api": wire_api,
                    "use_responses_api": True,
                }
            },
        )
        setup(agent, provider="openai", api_key="sk-test")

    assert mock_factory.call_args.args == ("openai",)
    assert mock_factory.call_args.kwargs["wire_api"] == expected_wire


def test_openai_unknown_wire_remains_manual_without_factory_call(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-5.4",
            base_url=None,
            defaults={
                "openai": {
                    "api_compat": "openai",
                    "wire_api": "unproven_wire",
                    "use_responses_api": True,
                }
            },
        )
        mgr = setup(agent, provider="openai", api_key="sk-test")

    mock_factory.assert_not_called()
    assert mgr._vision_service is None
    result = mgr.handle({})
    assert result["status"] == "error"
    assert "manual" in result["message"]
    assert "unproven_wire" not in result["message"]


def test_generic_openai_compatible_unknown_wire_remains_manual(tmp_path):
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        agent = make_provider_agent(
            tmp_path,
            provider="openai-compatible-relay",
            model="vision-current",
            base_url="https://relay.example/v1",
            defaults={
                "openai-compatible-relay": {
                    "api_compat": "openai",
                    "wire_api": "unproven_wire",
                }
            },
        )
        mgr = setup(
            agent,
            provider="openai-compatible-relay",
            api_key="sk-test",
        )

    mock_cls.assert_not_called()
    assert mgr._vision_service is None
    result = mgr.handle({})
    assert result["status"] == "error"
    assert "manual" in result["message"]
    assert "unproven_wire" not in result["message"]


def test_claude_code_remains_manual_only(tmp_path):
    agent = make_provider_agent(
        tmp_path, provider="claude-code", model="text-only", base_url="https://relay.example/v1"
    )
    mgr = setup(agent, provider="claude-code", api_key="sk-test")
    assert mgr._vision_service is None
    assert mgr.manual()["status"] in {"ok", "degraded"}


def test_vision_empty_response_is_error(tmp_path):
    """VisionManager should return error when service returns empty string."""
    mock_vision_svc = MagicMock(spec=VisionService)
    mock_vision_svc.analyze_image.return_value = ""

    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)
    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG fake")
    result = mgr.handle({"image_path": str(img_path)})
    assert result["status"] == "error"


def test_vision_setup_with_provider_and_key(tmp_path):
    """setup() should create a VisionService from provider + api_key."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_provider_agent(
            tmp_path,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            base_url=None,
        )
        mgr = setup(agent, provider="anthropic", api_key="sk-test")

        mock_factory.assert_called_once_with(
            "anthropic",
            api_key="sk-test",
            model="claude-sonnet-4-20250514",
        )
        assert isinstance(mgr, VisionManager)


def test_local_vision_is_hidden_but_explicitly_constructible(tmp_path):
    """The local pseudo-provider stays out of discovery yet keeps its opt-in path."""
    assert "local" not in PROVIDERS["providers"]

    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc
        agent = make_mock_agent(tmp_path)

        mgr = setup(
            agent,
            provider="local",
            model="mlx-community/local-test-model",
            max_tokens=128,
            api_compat="openai",
            base_url="https://must-not-be-forwarded.example/v1",
        )

    mock_factory.assert_called_once_with(
        "local",
        api_key=None,
        model="mlx-community/local-test-model",
        max_tokens=128,
    )
    assert mgr._vision_service is mock_svc
    agent.add_tool.assert_called_once()


def test_minimax_vision_preserves_active_default_headers(tmp_path):
    headers = {"X-Preset": "active"}
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimax.io/anthropic",
            defaults={"minimax": {"default_headers": headers}},
        )
        setup(agent, provider="minimax", api_key="sk-test")
    assert mock_factory.call_args.kwargs["default_headers"] == headers


def test_mimo_chat_route_does_not_forward_unsupported_constructor_kwargs(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="mimo",
            model="mimo-v2.5",
            base_url="https://mimo.example/v1",
            defaults={"mimo": {"default_headers": {"X": "1"}, "wire_api": "chat_completions"}},
        )
        setup(agent, provider="mimo", api_key="sk-test")
    assert mock_factory.call_args.args == ("mimo",)
    assert mock_factory.call_args.kwargs == {
        "api_key": "sk-test",
        "model": "mimo-v2.5",
        "base_url": "https://mimo.example/v1",
    }


def test_mimo_responses_wire_is_manual_only(tmp_path):
    agent = make_provider_agent(
        tmp_path,
        provider="mimo",
        model="mimo-v2.5",
        base_url="https://mimo.example/v1",
        defaults={"mimo": {"wire_api": "responses"}},
    )
    mgr = setup(agent, provider="mimo", api_key="sk-test")
    assert mgr._vision_service is None
    assert mgr.handle({})["status"] == "error"


def test_vision_setup_resolves_api_key_env(tmp_path, monkeypatch):
    """setup() should resolve api_key_env before constructing provider services."""
    monkeypatch.setenv("VISION_TEST_API_KEY", "sk-from-env")
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_provider_agent(
            tmp_path,
            provider="zhipu",
            model="GLM-5.2",
            base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        )
        mgr = setup(agent, provider="zhipu", api_key_env="VISION_TEST_API_KEY")

        mock_factory.assert_called_once()
        assert mock_factory.call_args.args == ("openai",)
        assert mock_factory.call_args.kwargs["api_key"] == "sk-from-env"
        assert mock_factory.call_args.kwargs["model"] == "GLM-5.2"
        assert isinstance(mgr, VisionManager)


def test_codex_vision_without_explicit_current_oauth_identity_is_manual_only(tmp_path):
    """Codex must not silently open the legacy default OAuth account."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_provider_agent(
            tmp_path,
            provider="codex",
            model="gpt-5.6-sol",
            base_url=None,
        )
        mgr = setup(agent, provider="codex")

    mock_factory.assert_not_called()
    assert mgr._vision_service is None
    assert "no explicit current OAuth identity" in mgr._manual_reason


@pytest.mark.parametrize("provider", ["codex", "codex-pool", "codex_pool"])
def test_codex_family_vision_aliases_use_codex_service(tmp_path, provider):
    """All current Codex-family aliases construct the native Codex service path."""
    selection = None if provider == "codex" else {
        "auth_path": "/tmp/codex-pool.json",
        "selection": {"source_index": 0},
    }
    defaults = (
        {"codex": {"codex_auth_path": "/tmp/codex-direct.json"}}
        if provider == "codex"
        else None
    )
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.auth.codex_pool.select_codex_pool_auth", return_value=selection
    ) as mock_select:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider=provider,
            model="gpt-5.6-sol",
            base_url=None,
            defaults=defaults,
        )
        setup(agent, provider=provider)
        assert mock_factory.call_args.args == ("codex",)
        assert mock_factory.call_args.kwargs["api_key"] is None
        assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
        if provider == "codex":
            mock_select.assert_not_called()
            assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-direct.json"
        else:
            mock_select.assert_called_once_with({}, model="gpt-5.6-sol")
            assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-pool.json"


def test_codex_vision_inherits_active_model_and_endpoint(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = "https://codex.example/backend-api/codex"
        agent.service._provider_defaults = {
            "codex": {"codex_auth_path": "/tmp/codex-current.json"}
        }
        setup(agent, provider="codex")
        kwargs = mock_factory.call_args.kwargs
        assert kwargs["model"] == "gpt-5.6-sol"
        assert kwargs["base_url"] == "https://codex.example/backend-api/codex"
        assert kwargs["token_path"] == "/tmp/codex-current.json"


def test_codex_vision_does_not_inherit_non_codex_model(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "gemini"
        agent.service._model = "gemini-2.5-pro"
        agent.service._base_url = "https://generativelanguage.example"
        mgr = setup(agent, provider="codex")
        mock_factory.assert_not_called()
        assert mgr._vision_service is None
        assert "no resolved current model" in mgr._manual_reason


def test_direct_codex_vision_uses_configured_auth_path(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = None
        agent.service._provider_defaults = {"codex": {"codex_auth_path": "/tmp/codex-a.json"}}
        setup(agent, provider="codex")
        assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
        assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-a.json"


def test_codex_pool_vision_selects_exact_model_and_passes_result(tmp_path):
    selected = {"auth_path": "/tmp/codex-b.json", "selection": {"source_index": 1}}
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.auth.codex_pool.select_codex_pool_auth", return_value=selected
    ) as mock_select:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex-pool"
        agent.service._model = "gpt-5.6-terra"
        agent.service._base_url = "https://codex-pool.example/backend-api/codex"
        agent.service._provider_defaults = {"codex-pool": {"codex_auth_pool_path": "pool.json"}}
        setup(agent, provider="codex-pool")
        mock_select.assert_called_once_with(
            {"codex_auth_pool_path": "pool.json"}, model="gpt-5.6-terra"
        )
        assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-terra"
        assert mock_factory.call_args.kwargs["base_url"] == "https://codex-pool.example/backend-api/codex"
        assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-b.json"


@pytest.mark.parametrize(
    ("provider", "model", "base_url", "expects_base_url"),
    [
        ("openai", "gpt-4.1", "https://openai.example/v1", True),
        ("anthropic", "claude-sonnet-4-20250514", "https://anthropic.example", True),
        ("gemini", "gemini-3-flash-preview", "https://gemini.example", False),
    ],
)
def test_direct_native_vision_inherits_same_provider_model_and_endpoint(
    tmp_path, provider, model, base_url, expects_base_url
):
    """Direct-native vision keeps the active provider identity when providers match."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider=provider,
            model=model,
            base_url=base_url,
        )
        setup(agent, provider=provider, api_key="sk-test")

        mock_factory.assert_called_once()
        assert mock_factory.call_args.args == (provider,)
        kwargs = mock_factory.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["model"] == model
        if expects_base_url:
            assert kwargs["base_url"] == base_url
        else:
            assert "base_url" not in kwargs


def test_direct_native_vision_honors_explicit_model_and_endpoint_over_active_provider(tmp_path):
    """Capability kwargs remain authoritative for direct-native services."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-4.1",
            base_url="https://active-openai.example/v1",
        )
        setup(
            agent,
            provider="openai",
            api_key="sk-test",
            model="gpt-4o",
            base_url="https://vision-openai.example/v1",
        )

        kwargs = mock_factory.call_args.kwargs
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["base_url"] == "https://vision-openai.example/v1"


def test_direct_native_vision_does_not_inherit_from_mismatched_provider(tmp_path):
    """An explicit OpenAI route must not inherit or default Anthropic identity."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_provider_agent(
            tmp_path,
            provider="anthropic",
            model="claude-opus-4.1",
            base_url="https://anthropic.example",
        )
        mgr = setup(agent, provider="openai", api_key="sk-test")

        mock_factory.assert_not_called()
        assert mgr._vision_service is None
        assert "no resolved current model" in mgr._manual_reason


def test_direct_vision_inherits_same_current_credential(tmp_path):
    """The active provider's own credential is part of its current identity."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-5.6-sol",
            base_url="https://openai.example/v1",
        )
        agent.service.api_key = "sk-current"
        setup(agent, provider="openai")

        assert mock_factory.call_args.kwargs["api_key"] == "sk-current"
        assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"


def test_direct_vision_does_not_reuse_unrelated_current_credential(tmp_path):
    """An explicit model/endpoint cannot borrow another provider's credential."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_provider_agent(
            tmp_path,
            provider="anthropic",
            model="claude-opus-4.1",
            base_url="https://anthropic.example",
        )
        agent.service.api_key = "sk-anthropic-current"
        mgr = setup(
            agent,
            provider="openai",
            model="gpt-5.6-sol",
            base_url="https://openai.example/v1",
        )

        mock_factory.assert_not_called()
        assert mgr._vision_service is None
        assert "no resolved current credential" in mgr._manual_reason


def test_mimo_vision_preserves_current_model_and_endpoint(tmp_path):
    """MiMo uses the active current identity on its supported route."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="mimo",
            model="mimo-v2.5-pro",
            base_url="https://mimo-proxy.example/v1",
        )
        setup(agent, provider="mimo", api_key="sk-test")

        kwargs = mock_factory.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["model"] == "mimo-v2.5-pro"
        assert kwargs["base_url"] == "https://mimo-proxy.example/v1"


def test_mimo_vision_honors_explicit_model_and_endpoint(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="mimo",
            model="mimo-v2.5-pro",
            base_url="https://active-mimo.example/v1",
        )
        setup(
            agent,
            provider="mimo",
            api_key="sk-test",
            model="mimo-v2-omni",
            base_url="https://vision-mimo.example/v1",
        )

        kwargs = mock_factory.call_args.kwargs
        assert kwargs["model"] == "mimo-v2-omni"
        assert kwargs["base_url"] == "https://vision-mimo.example/v1"


def test_minimax_vision_uses_current_anthropic_route(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="minimax",
            model="MiniMax-M3",
            base_url="https://api.minimax.io/anthropic",
        )
        setup(agent, provider="minimax", api_key="sk-test")

        mock_factory.assert_called_once_with("anthropic", api_key="sk-test", model="MiniMax-M3", base_url="https://api.minimax.io/anthropic")


def test_zhipu_vision_uses_current_openai_compatible_route(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="zhipu",
            model="GLM-5.2",
            base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        )
        setup(agent, provider="zhipu", api_key="sk-test")

        mock_factory.assert_called_once_with(
            "openai",
            api_key="sk-test",
            model="GLM-5.2",
            base_url="https://open.bigmodel.cn/api/coding/paas/v4",
            wire_api="chat_completions",
        )


def test_glm_vision_alias_uses_openai_compatible_route(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="glm",
            model="GLM-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
        )
        setup(agent, provider="glm", api_key="sk-test")

        mock_factory.assert_called_once_with(
            "openai",
            api_key="sk-test",
            model="GLM-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
            wire_api="chat_completions",
        )


def test_glm_vision_alias_inherits_current_zhipu_identity(tmp_path):
    """The documented GLM/Zhipu spelling pair shares one current route."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="zhipu",
            model="GLM-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
        )
        agent.service.api_key = "sk-current-zhipu"
        setup(agent, provider="glm")

        mock_factory.assert_called_once_with(
            "openai",
            api_key="sk-current-zhipu",
            model="GLM-5.2",
            base_url="https://api.z.ai/api/coding/paas/v4",
            wire_api="chat_completions",
        )


@pytest.mark.parametrize(
    "provider",
    ["openrouter", "deepseek", "kimi", "grok", "qwen", "claude-code", "claude_code", "custom"],
)
def test_registered_adapters_remain_callable_with_manual_route(tmp_path, provider):
    agent = make_provider_agent(
        tmp_path,
        provider=provider,
        model="text-only",
        base_url="https://relay.example/v1",
        defaults={provider: {}},
    )
    result = setup(agent, provider=provider, api_key="sk-test")
    assert isinstance(result, VisionManager)
    agent.add_tool.assert_called_once()
    handler = agent.add_tool.call_args.kwargs["handler"]
    assert handler({"action": "manual"})["status"] in {"ok", "degraded"}


def test_vision_setup_unsupported_provider_keeps_manual_route(tmp_path):
    agent = make_mock_agent(tmp_path)
    result = setup(agent, provider="not-real")
    assert isinstance(result, VisionManager)
    agent.add_tool.assert_called_once()


def test_vision_setup_without_provider_keeps_manual_route(tmp_path):
    agent = make_mock_agent(tmp_path)
    mgr = setup(agent)
    assert mgr.manual()["status"] in {"ok", "degraded"}


def test_setup_failure_retains_safe_manual_reason(tmp_path):
    with patch("lingtai.services.vision.create_vision_service", side_effect=RuntimeError(
        "token=secret https://user:pw@example.test/v1"
    )):
        agent = make_provider_agent(
            tmp_path, provider="openai", model="gpt-4o", base_url="https://example.test/v1"
        )
        (tmp_path / "x.png").write_bytes(b"fake")
        mgr = setup(agent, provider="openai", api_key="sk-test")
    result = mgr.handle({"image_path": "x.png"})
    assert result["status"] == "error"
    assert "RuntimeError" in result["message"]
    assert "secret" not in result["message"]
    assert "example.test" not in result["message"]


@pytest.mark.parametrize("token_path", [None, "", "  "])
def test_create_vision_service_codex_requires_explicit_token_path(token_path):
    """Codex factory must reject missing identity before importing its service."""
    kwargs = {} if token_path is None else {"token_path": token_path}

    with pytest.raises(ValueError, match="token_path is required"):
        create_vision_service("codex", **kwargs)


@pytest.mark.parametrize("token_path", [None, "", "  "])
def test_codex_vision_service_rejects_missing_token_path(token_path):
    """Direct Codex construction must not bypass the explicit identity guard."""
    from lingtai.services.vision.codex import CodexVisionService

    kwargs = {} if token_path is None else {"token_path": token_path}
    with pytest.raises(ValueError, match="token_path is required"):
        CodexVisionService(**kwargs)


def test_invalid_codex_direct_construction_never_imports_auth_manager():
    """A fresh interpreter must reject invalid identity before importing auth code."""
    script = """
import sys

assert "lingtai.auth.codex" not in sys.modules
from lingtai.services.vision.codex import CodexVisionService
assert "lingtai.auth.codex" not in sys.modules

for kwargs in ({}, {"token_path": None}, {"token_path": ""}, {"token_path": "  "}):
    try:
        CodexVisionService(**kwargs)
    except ValueError as exc:
        assert "token_path is required" in str(exc)
    else:
        raise AssertionError(f"invalid Codex identity was accepted: {kwargs!r}")

assert "lingtai.auth.codex" not in sys.modules
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_mimo_chat_completions_constructor_accepts_current_route(monkeypatch):
    fake_client = MagicMock()
    fake_openai = SimpleNamespace(OpenAI=MagicMock(return_value=fake_client))
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    svc = create_vision_service(
        "mimo", api_key="sk-test", model="mimo-v2.5", base_url="https://mimo.example/v1", max_tokens=777
    )
    from lingtai.services.vision.mimo import MiMoVisionService
    assert isinstance(svc, MiMoVisionService)
    fake_openai.OpenAI.assert_called_once_with(api_key="sk-test", base_url="https://mimo.example/v1")


def test_create_vision_service_codex_uses_explicit_path_and_filters_extra_kwargs(monkeypatch):
    """Codex vision keeps the explicit path while ignoring preset-only kwargs."""
    fake_openai = SimpleNamespace(OpenAI=MagicMock())
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    with patch("lingtai.auth.codex.CodexTokenManager") as mock_mgr:
        svc = create_vision_service(
            "codex",
            token_path="/tmp/codex-explicit.json",
            api_key_env="IGNORED",
            provider_note="from preset",
        )

    from lingtai.services.vision.codex import CodexVisionService

    assert isinstance(svc, CodexVisionService)
    mock_mgr.assert_called_once_with(token_path="/tmp/codex-explicit.json")


def test_codex_vision_service_streams_responses_api(monkeypatch, tmp_path):
    """CodexVisionService should parse streaming output_text deltas without network calls."""
    img_path = tmp_path / "chart.png"
    img_path.write_bytes(b"fake png bytes")

    events = [
        SimpleNamespace(type="response.created"),
        SimpleNamespace(type="response.output_text.delta", delta="A chart"),
        SimpleNamespace(type="response.output_text.delta", delta=" with candles"),
        SimpleNamespace(type="response.completed"),
    ]
    responses = MagicMock()
    responses.create.return_value = events
    client = SimpleNamespace(responses=responses)
    openai_cls = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai_cls))

    with patch("lingtai.auth.codex.CodexTokenManager") as mock_mgr_cls:
        mock_mgr_cls.return_value.get_access_token.return_value = "oauth-token"
        from lingtai.services.vision.codex import CodexVisionService

        svc = CodexVisionService(timeout=9.5, token_path="/tmp/codex-stream.json")
        result = svc.analyze_image(str(img_path), prompt="What is shown?")

    assert result == "A chart with candles"
    openai_cls.assert_called_once_with(
        api_key="oauth-token",
        base_url="https://chatgpt.com/backend-api/codex",
        timeout=9.5,
    )
    responses.create.assert_called_once()
    kwargs = responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert kwargs["instructions"]
    assert kwargs["stream"] is True
    assert kwargs["store"] is False
    assert "max_output_tokens" not in kwargs
    content = kwargs["input"][0]["content"]
    assert content[0] == {"type": "input_text", "text": "What is shown?"}
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")


def test_openai_responses_vision_sends_exact_request_shape(monkeypatch, tmp_path):
    img_path = tmp_path / "chart.png"
    img_path.write_bytes(b"fake png bytes")
    responses = MagicMock()
    responses.create.return_value = SimpleNamespace(output_text="answer")
    client = SimpleNamespace(responses=responses)
    openai_cls = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai_cls))
    from lingtai.services.vision.openai import OpenAIVisionService

    svc = OpenAIVisionService(
        api_key="sk-test", model="gpt-5.5", base_url="https://relay.example/v1",
        max_tokens=321, default_headers={"X-Preset": "active"}, wire_api="responses",
    )
    assert svc.analyze_image(str(img_path), prompt="Read this") == "answer"
    openai_cls.assert_called_once_with(
        api_key="sk-test", base_url="https://relay.example/v1", default_headers={"X-Preset": "active"}
    )
    kwargs = responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.5"
    assert kwargs["max_output_tokens"] == 321
    assert set(kwargs) == {"model", "max_output_tokens", "input"}
    assert kwargs["input"][0]["content"][0] == {"type": "input_text", "text": "Read this"}
    assert kwargs["input"][0]["content"][1]["type"] == "input_image"


def test_openai_vision_rejects_unknown_wire_before_client_construction(monkeypatch):
    responses = MagicMock()
    chat = MagicMock()
    client = SimpleNamespace(
        responses=responses,
        chat=SimpleNamespace(completions=chat),
    )
    openai_cls = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai_cls))
    from lingtai.services.vision.openai import OpenAIVisionService

    with pytest.raises(ValueError, match="Unsupported OpenAI vision wire"):
        OpenAIVisionService(
            api_key="sk-test",
            model="gpt-5.5",
            wire_api="unproven_wire",
        )

    openai_cls.assert_not_called()
    responses.create.assert_not_called()
    chat.create.assert_not_called()


def test_create_vision_service_unknown_provider():
    """create_vision_service should raise ValueError for unknown providers."""
    with pytest.raises(ValueError, match="Unsupported vision provider"):
        create_vision_service("unknown_provider", api_key="key")


def test_vision_service_abc_cannot_instantiate():
    """VisionService ABC should not be instantiable directly."""
    with pytest.raises(TypeError):
        VisionService()


def test_vision_empty_image_path(tmp_path):
    """VisionManager should return error for empty image path."""
    mock_vision_svc = MagicMock(spec=VisionService)
    agent = make_mock_agent(tmp_path)
    mgr = VisionManager(agent, vision_service=mock_vision_svc)
    result = mgr.handle({"image_path": ""})
    assert result["status"] == "error"
    assert "image_path" in result["message"].lower() or "provide" in result["message"].lower()


def test_vision_setup_no_provider_is_manual_only(tmp_path):
    agent = make_mock_agent(tmp_path)
    assert isinstance(setup(agent), VisionManager)


def make_custom_agent(tmp_path, *, api_compat=None, base_url=None, model=None):
    """Agent whose main LLM is a `provider='custom'` relay.

    `_provider_defaults` is the real shape: ``{provider_name: defaults_dict}``,
    so the fallback must peek into the per-provider bucket to read api_compat.
    """
    svc = MagicMock()
    svc.provider = "custom"
    svc._model = model
    svc._base_url = base_url
    svc._provider_defaults = {"custom": {"api_compat": api_compat}} if api_compat else {"custom": {}}
    return make_mock_agent(tmp_path, svc=svc)


# ---------------------------------------------------------------------------
# Issue #114 — vision fallback for provider='custom'
# ---------------------------------------------------------------------------

def test_vision_fallback_reads_api_compat_from_provider_bucket(tmp_path):
    """C-1: api_compat is read from _provider_defaults[provider], not the outer dict.

    `_provider_defaults` is shaped {provider_name: defaults_dict}. The old code
    called defaults.get("api_compat") on the OUTER dict, which always returned
    None, so the OpenAI fallback never engaged for custom providers.
    """
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        agent = make_custom_agent(
            tmp_path, api_compat="openai", base_url="http://127.0.0.1:34891/v1", model="GLM-5.1"
        )
        mgr = setup(agent, provider="custom", api_key="sk-test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["base_url"] == "http://127.0.0.1:34891/v1"
        assert kwargs["model"] == "GLM-5.1"
        assert isinstance(mgr, VisionManager)


def test_vision_fallback_anthropic_compat_routes_to_anthropic_service(tmp_path):
    """C-2: api_compat='anthropic' routes vision through AnthropicVisionService.

    Previously only the openai branch existed; anthropic-compat custom proxies
    retains a manual route even though AnthropicVisionService exists.
    """
    with patch("lingtai.services.vision.anthropic.AnthropicVisionService") as mock_cls:
        agent = make_custom_agent(
            tmp_path, api_compat="anthropic", base_url="http://127.0.0.1:34891", model="GLM-5.1"
        )
        mgr = setup(agent, provider="custom", api_key="sk-test")

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["api_key"] == "sk-test"
        assert kwargs["base_url"] == "http://127.0.0.1:34891"
        assert kwargs["model"] == "GLM-5.1"
        assert isinstance(mgr, VisionManager)


def test_vision_fallback_honors_capability_kwargs_over_service(tmp_path):
    """C-3: explicit capability model/base_url/api_compat override the main LLM.

    The whole point of explicit kwargs in init.json is to route vision through a
    different (vision-capable) model than the text-only main LLM. The fallback
    must consult kwargs first and only fall back to service._model/._base_url.
    """
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        # main LLM is GLM-5.1 (text-only) on an anthropic-compat proxy
        agent = make_custom_agent(
            tmp_path, api_compat="anthropic", base_url="http://127.0.0.1:34891", model="GLM-5.1"
        )
        # capability explicitly overrides: openai-compat vision model on the /v1 route
        mgr = setup(
            agent,
            provider="custom",
            api_key="sk-test",
            api_compat="openai",
            model="Kimi-K2.6",
            base_url="http://127.0.0.1:34891/v1",
            max_tokens=2048,
        )

        mock_cls.assert_called_once()
        kwargs = mock_cls.call_args.kwargs
        assert kwargs["model"] == "Kimi-K2.6"
        assert kwargs["base_url"] == "http://127.0.0.1:34891/v1"
        assert kwargs["max_tokens"] == 2048
        assert isinstance(mgr, VisionManager)


def test_vision_fallback_unknown_api_compat_keeps_manual_route(tmp_path):
    """Fallback with an unhandled api_compat skips and names api_compat in the reason."""
    agent = make_custom_agent(tmp_path, api_compat="gemini")
    result = setup(agent, provider="custom", api_key="sk-test")
    assert isinstance(result, VisionManager)
    agent.add_tool.assert_called_once()
    assert result.manual()["status"] in {"ok", "degraded"}


def test_minimax_vision_setup_uses_anthropic_route(tmp_path):
    """MiniMax vision should ignore LLM transport kwargs inherited from presets.

    Regression: presets.expand_inherit copies api_compat from the main LLM into
    `vision: {provider: inherit}`. The current MiniMax route is Anthropic-
    compatible, so setup must filter provider transport metadata before factory
    construction.
    """
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_mock_agent(tmp_path)
        agent.service._base_url = "https://api.minimaxi.com/anthropic"
        mgr = setup(
            agent,
            provider="minimax",
            api_key="sk-test",
            api_compat="anthropic",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/anthropic",
        )

        mock_factory.assert_called_once_with(
            "anthropic",
            api_key="sk-test",
            model="MiniMax-M3",
            base_url="https://api.minimaxi.com/anthropic",
        )
        assert isinstance(mgr, VisionManager)
