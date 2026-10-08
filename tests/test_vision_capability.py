"""Tests for vision capability and VisionService."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.tools.vision import PROVIDERS, VisionManager, setup
from lingtai.services.vision import VisionService, create_vision_service


def analyze(image_path: str = "", question=None) -> dict:
    """Build one LTP v2 ``analyze`` envelope for the public ``vision`` family.

    ``vision`` is action-separated (``analyze``/``check``/``list``/``manual``):
    the model sends ``action`` + strict per-action ``input`` + required
    ``reasoning``. Optional
    ``question`` is a required nullable branch property, so absent is ``None``.
    """
    return {
        "action": "analyze",
        "input": {"image_path": image_path, "question": question},
        "reasoning": "analyze the test image",
    }


MANUAL_CALL = {"action": "manual", "input": {}, "reasoning": "load vision guidance"}


def make_mock_service():
    svc = MagicMock()
    svc.provider = "openai"
    svc.model = "gpt-test"
    svc.api_key = None
    svc._key_resolver = MagicMock(return_value="fake-key")
    return svc


def make_mock_agent(tmp_path, svc=None):
    """Build a recording host that exercises the official mount route."""
    agent = MagicMock()
    agent.service = svc or make_mock_service()
    agent._config = MagicMock()
    agent._config.language = "en"
    agent._working_dir = tmp_path
    agent.working_dir = tmp_path
    agent.official_tool_plugins = {}

    def mount(transaction):
        transaction.consume()
        plugin = transaction.plugin
        agent.add_tool(
            plugin.name,
            schema=plugin.schema,
            handler=plugin.handler,
            description=plugin.description,
            glossary_package=plugin.glossary_package,
        )
        transaction.mark_mounted(agent)

    def claim(transaction):
        agent.official_tool_plugins[transaction.declaration.name] = transaction.declaration

    agent._mount_official_tool.side_effect = mount
    agent._claim_official_tool.side_effect = claim
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


REMOVED_LLM_PROVIDERS = (
    "deepseek", "zhipu", "glm", "mimo", "minimax", "openrouter", "grok",
    "qwen", "kimi", "gemini", "kimi-code", "kimi_code", "custom", "claude_code",
)


@pytest.mark.parametrize("provider", REMOVED_LLM_PROVIDERS)
def test_removed_llm_providers_are_manual_only_vision_routes(tmp_path, provider):
    """No removed LLM provider name keeps a vision route or a fallback provider."""
    assert provider not in PROVIDERS["providers"]
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.services.vision.openai.OpenAIVisionService"
    ) as mock_openai, patch(
        "lingtai.services.vision.anthropic.AnthropicVisionService"
    ) as mock_anthropic:
        agent = make_provider_agent(
            tmp_path,
            provider=provider,
            model="vision-current",
            base_url="https://relay.example/v1",
            defaults={provider: {"api_compat": "openai", "wire_api": "chat_completions"}},
        )
        agent.service.api_key = "sk-current"
        mgr = setup(agent, provider=provider, api_key="sk-test")

    mock_factory.assert_not_called()
    mock_openai.assert_not_called()
    mock_anthropic.assert_not_called()
    assert mgr._vision_service is None
    assert "No direct vision route is supported" in mgr._manual_reason
    assert "claude -p" not in mgr._manual_reason
    result = mgr.handle(analyze())
    assert result["status"] == "error"
    assert "sk-" not in result["message"]


@pytest.mark.parametrize("wire_api", [None, "auto", "", " \t ", "chat_completions"])
@pytest.mark.parametrize(
    "base_url", [None, "https://openai-compatible.example/v1"],
)
def test_openai_wire_defaults_to_chat_completions(tmp_path, wire_api, base_url):
    """Omitted/legacy ``auto``/blank wire selects Chat Completions everywhere."""
    defaults = {"openai": {} if wire_api is None else {"wire_api": wire_api}}
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-5.4",
            base_url=base_url,
            defaults=defaults,
        )
        setup(agent, provider="openai", api_key="sk-test")

    assert mock_factory.call_args.args == ("openai",)
    assert mock_factory.call_args.kwargs["wire_api"] == "chat_completions"


def test_openai_responses_wire_propagates_from_active_bucket(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-5.4",
            base_url="https://relay.example/v1",
            defaults={"openai": {"wire_api": "responses"}},
        )
        agent.service.api_key = "sk-current"
        setup(agent)

    assert mock_factory.call_args.args == ("openai",)
    assert mock_factory.call_args.kwargs == {
        "api_key": "sk-current",
        "model": "gpt-5.4",
        "base_url": "https://relay.example/v1",
        "wire_api": "responses",
    }


def test_openai_unknown_wire_remains_manual_without_factory_call(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="gpt-5.4",
            base_url=None,
            defaults={"openai": {"wire_api": "unproven_wire"}},
        )
        mgr = setup(agent, provider="openai", api_key="sk-test")

    mock_factory.assert_not_called()
    assert mgr._vision_service is None
    result = mgr.handle(analyze())
    assert result["status"] == "error"
    assert "manual" in result["message"]
    assert "unproven_wire" not in result["message"]


@pytest.mark.parametrize("provider", ["claude-p", "claude-code"])
def test_claude_family_returns_cli_guidance_not_service(tmp_path, provider):
    """Every claude-family spelling routes to the claude-cli guidance comment."""
    agent = make_provider_agent(
        tmp_path, provider=provider, model="text-only", base_url="https://relay.example/v1"
    )
    mgr = setup(agent, provider=provider, api_key="sk-test")
    assert mgr._vision_service is None
    assert "claude -p" in mgr._manual_reason
    assert "vision(action='manual'" in mgr._manual_reason
    assert mgr.manual()["status"] in {"ok", "degraded"}
    result = mgr._dispatch_analyze({"image_path": "x.png", "question": None})
    assert result["status"] == "error"
    assert "claude -p" in result["message"]


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


def test_mlx_vision_is_hidden_but_explicitly_constructible(tmp_path):
    """The mlx pseudo-provider stays out of discovery yet keeps its opt-in path."""
    assert "mlx" not in PROVIDERS["providers"]

    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc
        agent = make_mock_agent(tmp_path)

        mgr = setup(
            agent,
            provider="mlx",
            model="mlx-community/local-test-model",
            max_tokens=128,
            api_compat="openai",
            base_url="https://must-not-be-forwarded.example/v1",
        )

    mock_factory.assert_called_once_with(
        "mlx",
        api_key=None,
        model="mlx-community/local-test-model",
        max_tokens=128,
    )
    assert mgr._vision_service is mock_svc
    agent.add_tool.assert_called_once()


def test_local_vision_is_advertised_and_requires_model(tmp_path):
    """Local is advertised but refuses a silent model default with guidance."""
    assert "local" in PROVIDERS["providers"]
    assert "ollama" not in PROVIDERS["providers"]

    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_svc_cls:
        agent = make_mock_agent(tmp_path)

        # Minimal config: provider only. No model anywhere -> guided failure.
        mgr = setup(agent, provider="local")

    mock_svc_cls.assert_not_called()
    assert mgr._vision_service is None
    assert "model" in mgr._manual_reason
    assert "consent" in mgr._manual_reason
    agent.add_tool.assert_called_once()


def test_local_vision_missing_model_result_guides_with_consent(tmp_path):
    """The analyze result on a missing-service setup failure guides setup with consent."""
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as _mock_svc_cls:
        agent = make_mock_agent(tmp_path)
        mgr = setup(agent, provider="local")

    result = mgr._dispatch_analyze({"image_path": "x.png", "question": None})

    assert result["status"] == "error"
    assert "ask the human for consent" in result["message"]
    assert "vision(action='manual'" in result["message"]
    # Skill load comes before the consent ask.
    assert result["message"].index("vision(action='manual'") < result["message"].index("consent")


def test_default_vision_failure_informs_agent_of_alternatives(tmp_path):
    """A default-route analyze failure explains the cause and alternatives."""
    agent = make_mock_agent(tmp_path)
    svc = MagicMock(spec=VisionService)
    svc.analyze_image.side_effect = RuntimeError("model does not support images")
    mgr = setup(agent, vision_service=svc)

    image = tmp_path / "test.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    result = mgr._dispatch_analyze({"image_path": str(image), "question": None})

    assert result["status"] == "error"
    assert "default vision route" in result["message"]
    assert "current provider's own endpoint" in result["message"]
    assert "provider='local'" in result["message"]
    assert "provider's MCP" in result["message"]
    assert "consent" in result["message"]
    assert "vision(action='manual'" in result["message"]


def test_local_vision_uses_default_base_url_with_explicit_model(tmp_path):
    """Local with an explicit model synthesizes a placeholder key and default URL."""
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_svc_cls:
        mock_svc = MagicMock(spec=VisionService)
        mock_svc_cls.return_value = mock_svc
        agent = make_mock_agent(tmp_path)

        mgr = setup(agent, provider="local", model="llava")

    mock_svc_cls.assert_called_once_with(
        api_key="local",
        model="llava",
        base_url="http://localhost:11434/v1",
        wire_api="chat_completions",
    )
    assert mgr._vision_service is mock_svc
    agent.add_tool.assert_called_once()


def test_local_vision_respects_explicit_override(tmp_path):
    """Explicit local kwargs win over defaults."""
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_svc_cls:
        mock_svc = MagicMock(spec=VisionService)
        mock_svc_cls.return_value = mock_svc
        agent = make_mock_agent(tmp_path)

        setup(
            agent,
            provider="local",
            api_key="real-key",
            model="llava",
            base_url="http://127.0.0.1:9999/v1",
            max_tokens=256,
        )

    mock_svc_cls.assert_called_once_with(
        api_key="real-key",
        model="llava",
        base_url="http://127.0.0.1:9999/v1",
        wire_api="chat_completions",
        max_tokens=256,
    )


def test_local_vision_reads_settings_json(tmp_path):
    """settings/vision.json supplies the local endpoint when kwargs are absent."""
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    (settings_dir / "vision.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "moondream",
            }
        ),
        encoding="utf-8",
    )
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_svc_cls:
        mock_svc = MagicMock(spec=VisionService)
        mock_svc_cls.return_value = mock_svc
        agent = make_mock_agent(tmp_path)

        mgr = setup(agent, provider="local")

    mock_svc_cls.assert_called_once_with(
        api_key="local",
        model="moondream",
        base_url="http://127.0.0.1:8080/v1",
        wire_api="chat_completions",
    )
    assert mgr._vision_service is mock_svc


def test_local_vision_kwargs_override_settings_json(tmp_path):
    """Capability kwargs override settings/vision.json file values."""
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    (settings_dir / "vision.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "moondream",
            }
        ),
        encoding="utf-8",
    )
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_svc_cls:
        mock_svc = MagicMock(spec=VisionService)
        mock_svc_cls.return_value = mock_svc
        agent = make_mock_agent(tmp_path)

        setup(
            agent,
            provider="local",
            model="llava",
            base_url="http://127.0.0.1:9999/v1",
        )

    mock_svc_cls.assert_called_once_with(
        api_key="local",
        model="llava",
        base_url="http://127.0.0.1:9999/v1",
        wire_api="chat_completions",
    )


def test_anthropic_vision_preserves_active_default_headers(tmp_path):
    headers = {"X-Preset": "active"}
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            base_url="https://anthropic-compatible.example",
            defaults={"anthropic": {"default_headers": headers}},
        )
        setup(agent, provider="anthropic", api_key="sk-test")
    assert mock_factory.call_args.args == ("anthropic",)
    assert mock_factory.call_args.kwargs == {
        "api_key": "sk-test",
        "model": "claude-sonnet-4-20250514",
        "base_url": "https://anthropic-compatible.example",
        "default_headers": headers,
    }


def test_vision_setup_resolves_api_key_env(tmp_path, monkeypatch):
    """setup() should resolve api_key_env before constructing provider services."""
    monkeypatch.setenv("VISION_TEST_API_KEY", "sk-from-env")
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_provider_agent(
            tmp_path,
            provider="openai",
            model="GLM-5.2",
            base_url="https://open.bigmodel.cn/api/coding/paas/v4",
        )
        mgr = setup(agent, provider="openai", api_key_env="VISION_TEST_API_KEY")

        mock_factory.assert_called_once()
        assert mock_factory.call_args.args == ("openai",)
        assert mock_factory.call_args.kwargs["api_key"] == "sk-from-env"
        assert mock_factory.call_args.kwargs["model"] == "GLM-5.2"
        assert (
            mock_factory.call_args.kwargs["base_url"]
            == "https://open.bigmodel.cn/api/coding/paas/v4"
        )
        assert isinstance(mgr, VisionManager)


def test_active_codex_vision_without_auth_path_uses_default_token_path(tmp_path, monkeypatch):
    """An active ``codex`` service whose bucket configures no ``codex_auth_path``
    binds the default Codex token file, mirroring the canonical Codex factory
    (``FixedAccountSource(codex_auth_path or default_codex_token_path())``).
    ``LINGTAI_TUI_DIR`` points at a disposable dir so no real host token file
    is named."""
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(tmp_path / "tui"))
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="codex",
            model="gpt-5.6-sol",
            base_url=None,
        )
        mgr = setup(agent, provider="codex")

    mock_factory.assert_called_once()
    assert mock_factory.call_args.args == ("codex",)
    assert mock_factory.call_args.kwargs["api_key"] is None
    assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
    assert mock_factory.call_args.kwargs["token_path"] == str(tmp_path / "tui" / "codex-auth.json")
    assert mgr._vision_service is mock_factory.return_value


def test_codex_vision_uses_native_codex_service(tmp_path):
    """The ``codex`` provider constructs the native Codex service path."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="codex",
            model="gpt-5.6-sol",
            base_url=None,
            defaults={"codex": {"codex_auth_path": "/tmp/codex-direct.json"}},
        )
        setup(agent, provider="codex")
        assert mock_factory.call_args.args == ("codex",)
        assert mock_factory.call_args.kwargs["api_key"] is None
        assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
        assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-direct.json"


@pytest.mark.parametrize("provider", ["codex-pool", "codex_pool"])
def test_removed_codex_pool_spellings_are_not_codex_vision_routes(tmp_path, provider):
    """The in-kernel Codex pool was removed (pooling now lives in the external
    subs-pool proxy behind ``provider: openai``). Its old spellings are no longer
    Codex aliases: even over an active Codex service they never construct the
    native Codex vision service nor borrow the active Codex identity."""
    assert provider not in PROVIDERS["providers"]
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        agent = make_provider_agent(
            tmp_path,
            provider="codex",
            model="gpt-5.6-sol",
            base_url=None,
            defaults={"codex": {"codex_auth_path": "/tmp/codex-direct.json"}},
        )
        mgr = setup(agent, provider=provider)

    mock_factory.assert_not_called()
    assert mgr._vision_service is None
    assert "No direct vision route is supported" in mgr._manual_reason
    assert "/tmp/codex-direct.json" not in mgr._manual_reason


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
        agent.service.provider = "anthropic"
        agent.service._model = "claude-opus-4.1"
        agent.service._base_url = "https://anthropic.example"
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


# ---------------------------------------------------------------------------
# Codex vision OAuth identity resolution
#
# Mirrors the canonical Codex factory in ``lingtai/llm/_register.py``, which
# binds exactly one account: an explicit capability ``token_path``, else the
# active bucket's nonblank trimmed ``codex_auth_path``, else — only when the
# active provider is Codex — the default token file. An unrelated active
# provider never supplies a Codex identity; the request fails closed to manual.
# ---------------------------------------------------------------------------


def test_active_codex_padded_auth_path_is_trimmed(tmp_path):
    """Canonical parity: like the factory's ``FixedAccountSource``, a space-padded
    bucket ``codex_auth_path`` is trimmed, and that same trimmed value — not the
    raw padded string — reaches ``create_vision_service`` together with the active
    model and endpoint."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = "https://codex.example/backend-api/codex"
        agent.service._provider_defaults = {
            "codex": {"codex_auth_path": "  /tmp/codex-direct.json  "}
        }
        setup(agent, provider="codex")

    assert mock_factory.call_args.args == ("codex",)
    assert mock_factory.call_args.kwargs["api_key"] is None
    assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
    assert mock_factory.call_args.kwargs["base_url"] == "https://codex.example/backend-api/codex"
    # The trimmed value is used as token_path, never the raw space-padded string.
    assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-direct.json"


def test_whitespace_only_bucket_auth_path_falls_back_to_default_token(tmp_path, monkeypatch):
    """Canonical parity — a whitespace-only bucket ``codex_auth_path`` is not an
    identity (the factory trims it to empty and binds the default token file), so
    vision never forwards the blank path as ``token_path``; the active Codex
    service falls back to the default token file instead."""
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(tmp_path / "tui"))
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = None
        agent.service._provider_defaults = {"codex": {"codex_auth_path": "   "}}
        setup(agent, provider="codex")

    mock_factory.assert_called_once()
    assert mock_factory.call_args.kwargs["token_path"] == str(tmp_path / "tui" / "codex-auth.json")


def test_whitespace_only_explicit_token_path_falls_back_to_bucket_identity(tmp_path):
    """A whitespace-only explicit capability ``token_path`` is not an identity: it
    must not be forwarded as a direct credential. Over an active direct bucket the
    blank explicit value is dropped and the trimmed bucket path is used instead."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = None
        agent.service._provider_defaults = {"codex": {"codex_auth_path": "/tmp/codex-bucket.json"}}
        setup(agent, provider="codex", token_path="   ")

    # The blank explicit token_path is normalized away; the trimmed bucket path wins.
    assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-bucket.json"


def test_explicit_token_path_takes_precedence_over_bucket_identity(tmp_path):
    """An explicit capability ``token_path`` (trimmed) wins over the active
    bucket's ``codex_auth_path``: a vision call may bind its own account."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = None
        agent.service._provider_defaults = {"codex": {"codex_auth_path": "/tmp/codex-bucket.json"}}
        setup(agent, provider="codex", token_path="  /tmp/codex-explicit.json  ")

    assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-explicit.json"


def test_stale_pool_path_in_active_codex_bucket_is_ignored(tmp_path, monkeypatch):
    """A leftover ``codex_auth_pool_path`` (legacy-ignored since the in-kernel
    pool was removed) is not an identity: an active ``codex`` bucket carrying only
    that key binds the default token file, never the pool file."""
    monkeypatch.setenv("LINGTAI_TUI_DIR", str(tmp_path / "tui"))
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = "https://codex.example/backend-api/codex"
        agent.service._provider_defaults = {"codex": {"codex_auth_pool_path": "pool.json"}}
        setup(agent, provider="codex")

    assert mock_factory.call_args.args == ("codex",)
    assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
    assert mock_factory.call_args.kwargs["base_url"] == "https://codex.example/backend-api/codex"
    assert mock_factory.call_args.kwargs["token_path"] == str(tmp_path / "tui" / "codex-auth.json")


def test_active_codex_configured_auth_path_never_consults_default_token(tmp_path):
    """Active ``codex`` with a nonblank ``codex_auth_path`` binds that account:
    the default token file is never consulted."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.auth.codex.default_codex_token_path",
    ) as mock_default:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "codex"
        agent.service._model = "gpt-5.6-sol"
        agent.service._base_url = None
        agent.service._provider_defaults = {"codex": {"codex_auth_path": "/tmp/codex-c.json"}}
        setup(agent, provider="codex")

    mock_default.assert_not_called()
    assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
    assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-c.json"


def test_codex_request_over_unrelated_active_provider_fails_closed(tmp_path):
    """A Codex request over an unrelated active provider must fail closed to
    manual, never borrowing the unrelated provider's model, base URL, or
    credential and never consulting the default Codex token file."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.auth.codex.default_codex_token_path",
    ) as mock_default:
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "anthropic"
        agent.service._model = "claude-opus-4.1"
        agent.service._base_url = "https://anthropic.example"
        agent.service._provider_defaults = {"anthropic": {"codex_auth_path": "/tmp/should-not-be-used.json"}}
        mgr = setup(agent, provider="codex")

    mock_factory.assert_not_called()
    mock_default.assert_not_called()
    assert mgr._vision_service is None
    assert "no resolved current model" in mgr._manual_reason
    assert "claude-opus-4.1" not in mgr._manual_reason
    assert "/tmp/should-not-be-used.json" not in mgr._manual_reason


def test_codex_request_over_unrelated_provider_with_explicit_model_never_uses_default_token(
    tmp_path,
):
    """Explicit-model variant — even when the request supplies its own ``model``
    (clearing the missing-model guard) and a whitespace-only explicit
    ``token_path``, an unrelated active provider must NOT treat that blank value
    as an identity or fall back to the default Codex token file; the request
    fails closed on the missing identity instead. Proves both normalization and
    the ``same_provider`` gate, not merely the model guard."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.auth.codex.default_codex_token_path",
    ) as mock_default:
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "anthropic"
        agent.service._model = "claude-opus-4.1"
        agent.service._base_url = "https://anthropic.example"
        agent.service._provider_defaults = {"anthropic": {}}
        mgr = setup(agent, provider="codex", model="gpt-5.6-sol", token_path="   ")

    mock_factory.assert_not_called()
    mock_default.assert_not_called()
    assert mgr._vision_service is None
    assert "no explicit current OAuth identity" in mgr._manual_reason


def test_explicit_token_path_over_unrelated_provider_is_an_independent_identity(tmp_path):
    """An explicit model plus nonblank ``token_path`` is a complete, independent
    Codex identity: it is honored over an unrelated active provider without
    inheriting that provider's endpoint or consulting the default token file."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory, patch(
        "lingtai.auth.codex.default_codex_token_path",
    ) as mock_default:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_mock_agent(tmp_path)
        agent.service.provider = "anthropic"
        agent.service._model = "claude-opus-4.1"
        agent.service._base_url = "https://anthropic.example"
        agent.service._provider_defaults = {"anthropic": {}}
        setup(agent, provider="codex", model="gpt-5.6-sol", token_path="/tmp/codex-own.json")

    mock_default.assert_not_called()
    assert mock_factory.call_args.args == ("codex",)
    assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"
    assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-own.json"
    assert "base_url" not in mock_factory.call_args.kwargs


@pytest.mark.parametrize(
    ("provider", "model", "base_url", "expects_base_url"),
    [
        ("openai", "gpt-4.1", "https://openai.example/v1", True),
        ("anthropic", "claude-sonnet-4-20250514", "https://anthropic.example", True),
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


@pytest.mark.parametrize(
    "provider",
    ["openrouter", "deepseek", "kimi", "grok", "qwen", "claude-code", "claude_code", "custom", "gemini"],
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
    assert handler(MANUAL_CALL)["status"] in {"ok", "degraded"}


def test_vision_setup_unsupported_provider_keeps_manual_route(tmp_path):
    agent = make_mock_agent(tmp_path)
    result = setup(agent, provider="not-real")
    assert isinstance(result, VisionManager)
    agent.add_tool.assert_called_once()


def test_vision_setup_without_provider_keeps_manual_route(tmp_path):
    agent = make_mock_agent(tmp_path)
    mgr = setup(agent)
    assert isinstance(mgr, VisionManager)
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
    result = mgr.handle(analyze("x.png"))
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
    import base64

    # A genuinely valid 1x1 truecolor PNG (stdlib base64 decode; no external
    # dependency, file fixture, or network). This exercises the direct Codex
    # request seam with real image bytes rather than a non-PNG placeholder.
    valid_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    )
    assert valid_png.startswith(b"\x89PNG\r\n\x1a\n")
    img_path = tmp_path / "chart.png"
    img_path.write_bytes(valid_png)

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
    image_url = content[1]["image_url"]
    assert image_url.startswith("data:image/png;base64,")
    # The direct seam must carry the exact valid PNG, not a mangled placeholder.
    assert base64.b64decode(image_url.split(",", 1)[1]) == valid_png


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


# ---------------------------------------------------------------------------
# Default route inherits the agent's own provider family (four-family collapse)
# ---------------------------------------------------------------------------


def _real_service(provider: str, **kwargs):
    """A real LLMService for the active provider (no network is touched)."""
    import lingtai.llm  # noqa: F401 — registers the adapter factories
    from lingtai.llm.service import LLMService

    return LLMService(provider=provider, **kwargs)


def _agent_over(tmp_path, service):
    agent = make_mock_agent(tmp_path)
    agent.service = service
    return agent


def test_default_openai_route_uses_effective_official_endpoint_when_base_url_omitted(
    tmp_path, monkeypatch
):
    """Regression: vision must use the adapter's effective endpoint, never a
    missing manifest ``base_url`` that lets the key reach a guessed host."""
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    service = _real_service("openai", model="gpt-5.5", api_key="sk-active-test")
    assert service.effective_base_url == "https://api.openai.com/v1"
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        mgr = setup(_agent_over(tmp_path, service))

    mock_cls.assert_called_once_with(
        api_key="sk-active-test",
        model="gpt-5.5",
        base_url="https://api.openai.com/v1",
        wire_api="chat_completions",
    )
    assert mgr._vision_service is mock_cls.return_value


def test_default_openai_route_uses_configured_endpoint_and_responses_wire(tmp_path):
    service = _real_service(
        "openai",
        model="vendor-vision",
        api_key="sk-active-test",
        base_url="https://api.vendor.example/v1",
        provider_defaults={
            "openai": {
                "wire_api": "responses",
                "default_headers": {"X-Tenant": "t1"},
            }
        },
    )
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(_agent_over(tmp_path, service))

    mock_cls.assert_called_once_with(
        api_key="sk-active-test",
        model="vendor-vision",
        base_url="https://api.vendor.example/v1",
        default_headers={"X-Tenant": "t1"},
        wire_api="responses",
    )


def test_inherit_expansion_routes_to_the_active_openai_endpoint(tmp_path, monkeypatch):
    """``provider: inherit`` expands to the main provider and still targets the
    active effective endpoint rather than the (omitted) manifest base_url."""
    from lingtai.kernel.presets import expand_inherit

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("VISION_INHERIT_TEST_KEY", "sk-active-test")
    caps = expand_inherit(
        {"vision": {"provider": "inherit"}},
        {"provider": "openai", "model": "gpt-5.5", "api_key_env": "VISION_INHERIT_TEST_KEY"},
    )
    assert "api_compat" not in caps["vision"]
    service = _real_service("openai", model="gpt-5.5", api_key="sk-active-test")
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(_agent_over(tmp_path, service), **caps["vision"])

    assert mock_cls.call_args.kwargs["base_url"] == "https://api.openai.com/v1"
    assert mock_cls.call_args.kwargs["api_key"] == "sk-active-test"
    assert mock_cls.call_args.kwargs["model"] == "gpt-5.5"


def test_default_anthropic_route_inherits_effective_endpoint_key_and_model(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    service = _real_service("anthropic", model="claude-sonnet-5", api_key="sk-ant-test")
    with patch("lingtai.services.vision.anthropic.AnthropicVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(_agent_over(tmp_path, service))

    mock_cls.assert_called_once_with(
        api_key="sk-ant-test",
        model="claude-sonnet-5",
        base_url="https://api.anthropic.com",
    )


def test_default_anthropic_compatible_endpoint_is_inherited(tmp_path):
    service = _real_service(
        "anthropic",
        model="glm-vision",
        api_key="sk-ant-test",
        base_url="https://anthropic-compatible.example/api",
    )
    with patch("lingtai.services.vision.anthropic.AnthropicVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(_agent_over(tmp_path, service))

    assert mock_cls.call_args.kwargs["base_url"] == "https://anthropic-compatible.example/api"
    assert mock_cls.call_args.kwargs["api_key"] == "sk-ant-test"


def test_default_codex_route_keeps_active_account(tmp_path):
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="codex",
            model="gpt-5.6-sol",
            base_url=None,
            defaults={"codex": {"codex_auth_path": "/tmp/codex-default-route.json"}},
        )
        setup(agent)

    assert mock_factory.call_args.args == ("codex",)
    assert mock_factory.call_args.kwargs["token_path"] == "/tmp/codex-default-route.json"
    assert mock_factory.call_args.kwargs["model"] == "gpt-5.6-sol"


def test_default_claude_code_route_is_manual_cli_guidance(tmp_path):
    agent = make_provider_agent(tmp_path, provider="claude-code", model="opus", base_url=None)
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mgr = setup(agent)
    mock_factory.assert_not_called()
    assert mgr._vision_service is None
    assert "claude -p" in mgr._manual_reason


def test_active_credential_never_reaches_a_different_explicit_endpoint(tmp_path):
    """Credential-leak guard: an explicit vision base_url that differs from the
    active effective endpoint must bring its own key; the active key is never
    sent there."""
    service = _real_service(
        "openai",
        model="gpt-5.5",
        api_key="sk-active-secret",
        base_url="https://api.active.example/v1",
    )
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls, patch(
        "lingtai.services.vision.create_vision_service"
    ) as mock_factory:
        mgr = setup(
            _agent_over(tmp_path, service),
            provider="openai",
            base_url="https://other-vision.example/v1",
        )

    mock_cls.assert_not_called()
    mock_factory.assert_not_called()
    assert mgr._vision_service is None
    assert "no resolved current credential" in mgr._manual_reason
    assert "sk-active-secret" not in mgr._manual_reason
    assert "other-vision.example" not in mgr._manual_reason


def test_active_credential_is_reused_for_the_same_explicit_endpoint(tmp_path):
    """Naming the active endpoint explicitly (trailing slash ignored) is not a
    different host, so the active credential may be reused."""
    service = _real_service(
        "openai",
        model="gpt-5.5",
        api_key="sk-active-test",
        base_url="https://api.active.example/v1",
    )
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(
            _agent_over(tmp_path, service),
            provider="openai",
            base_url="https://api.active.example/v1/",
            model="gpt-vision",
        )

    assert mock_cls.call_args.kwargs["api_key"] == "sk-active-test"
    assert mock_cls.call_args.kwargs["model"] == "gpt-vision"
    assert mock_cls.call_args.kwargs["base_url"] == "https://api.active.example/v1/"


def test_explicit_endpoint_with_its_own_key_is_honored(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_OWN_ENDPOINT_KEY", "sk-vision-own")
    service = _real_service(
        "openai",
        model="gpt-5.5",
        api_key="sk-active-secret",
        base_url="https://api.active.example/v1",
    )
    with patch("lingtai.services.vision.openai.OpenAIVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(
            _agent_over(tmp_path, service),
            provider="openai",
            base_url="https://other-vision.example/v1",
            api_key_env="VISION_OWN_ENDPOINT_KEY",
            model="vision-model",
        )

    assert mock_cls.call_args.kwargs["api_key"] == "sk-vision-own"
    assert mock_cls.call_args.kwargs["base_url"] == "https://other-vision.example/v1"


def test_explicit_anthropic_route_over_openai_agent_needs_its_own_identity(tmp_path):
    """A different family lends nothing: model and credential must be explicit."""
    service = _real_service("openai", model="gpt-5.5", api_key="sk-active-secret")
    with patch("lingtai.services.vision.anthropic.AnthropicVisionService") as mock_cls:
        mgr = setup(_agent_over(tmp_path, service), provider="anthropic")
    mock_cls.assert_not_called()
    assert "no resolved current model" in mgr._manual_reason

    with patch("lingtai.services.vision.anthropic.AnthropicVisionService") as mock_cls:
        mock_cls.return_value = MagicMock(spec=VisionService)
        setup(
            _agent_over(tmp_path, service),
            provider="anthropic",
            api_key="sk-ant-own",
            model="claude-sonnet-5",
        )
    mock_cls.assert_called_once_with(api_key="sk-ant-own", model="claude-sonnet-5")


def test_legacy_api_compat_capability_value_is_ignored(tmp_path):
    """``api_compat`` belonged to the retired ``custom`` provider: a leftover
    capability value neither selects nor blocks a route."""
    with patch("lingtai.services.vision.create_vision_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=VisionService)
        agent = make_provider_agent(
            tmp_path,
            provider="anthropic",
            model="claude-sonnet-5",
            base_url="https://anthropic-compatible.example",
        )
        mgr = setup(
            agent,
            provider="anthropic",
            api_key="sk-test",
            api_compat="openai",
        )

    mock_factory.assert_called_once_with(
        "anthropic",
        api_key="sk-test",
        model="claude-sonnet-5",
        base_url="https://anthropic-compatible.example",
    )
    assert isinstance(mgr, VisionManager)
