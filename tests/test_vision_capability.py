"""Tests for vision capability and VisionService."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.capabilities.vision import VisionManager, setup
from lingtai.services.vision import VisionService, create_vision_service


def make_mock_service():
    svc = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    svc._key_resolver = MagicMock(return_value="fake-key")
    return svc


def make_mock_agent(tmp_path, svc=None):
    agent = MagicMock()
    agent.service = svc or make_mock_service()
    agent._config = MagicMock()
    agent._config.language = "en"
    agent._working_dir = tmp_path
    return agent


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
    assert "API down" in result["message"]


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
    with patch("lingtai.capabilities.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_mock_agent(tmp_path)
        mgr = setup(agent, provider="anthropic", api_key="sk-test")

        mock_factory.assert_called_once_with("anthropic", api_key="sk-test")
        assert isinstance(mgr, VisionManager)


def test_vision_setup_resolves_api_key_env(tmp_path, monkeypatch):
    """setup() should resolve api_key_env before constructing provider services."""
    monkeypatch.setenv("VISION_TEST_API_KEY", "sk-from-env")
    with patch("lingtai.capabilities.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_mock_agent(tmp_path)
        mgr = setup(agent, provider="zhipu", api_key_env="VISION_TEST_API_KEY")

        mock_factory.assert_called_once()
        assert mock_factory.call_args.args == ("zhipu",)
        assert mock_factory.call_args.kwargs["api_key"] == "sk-from-env"
        assert isinstance(mgr, VisionManager)


def test_vision_setup_with_codex_provider_without_api_key(tmp_path):
    """Codex vision uses ChatGPT OAuth, so setup should not require api_key."""
    with patch("lingtai.capabilities.vision.create_vision_service") as mock_factory:
        mock_svc = MagicMock(spec=VisionService)
        mock_factory.return_value = mock_svc

        agent = make_mock_agent(tmp_path)
        mgr = setup(agent, provider="codex")

        mock_factory.assert_called_once_with("codex", api_key=None)
        assert isinstance(mgr, VisionManager)


def test_vision_setup_unsupported_provider_skips(tmp_path):
    """Unsupported providers gracefully skip: setup returns None and logs capability_skipped.

    The mock agent's `service._provider_defaults` is a MagicMock (not a dict),
    so the OpenAI-compat fallback does not engage; the graceful skip path
    runs instead. Agent.py's capability loader handles None as a no-op.
    """
    agent = make_mock_agent(tmp_path)
    result = setup(agent, provider="not-real")
    assert result is None
    agent.add_tool.assert_not_called()
    agent._log.assert_called_with(
        "capability_skipped",
        capability="vision",
        requested_provider="not-real",
        reason="no vision support for provider 'not-real'",
    )


def test_vision_setup_requires_provider_or_service(tmp_path):
    """setup() without provider or service raises ValueError."""
    agent = make_mock_agent(tmp_path)
    with pytest.raises(ValueError, match="vision capability requires"):
        setup(agent)


def test_create_vision_service_codex_without_api_key(monkeypatch):
    """Codex factory should not require api_key and should use OAuth manager."""
    fake_openai = SimpleNamespace(OpenAI=MagicMock())
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    with patch("lingtai.services.vision.codex.CodexTokenManager") as mock_mgr:
        svc = create_vision_service("codex")

    from lingtai.services.vision.codex import CodexVisionService

    assert isinstance(svc, CodexVisionService)
    mock_mgr.assert_called_once_with(token_path=None)


def test_create_vision_service_codex_ignores_extra_preset_kwargs(monkeypatch):
    """Codex vision should tolerate irrelevant preset kwargs like api_key_env."""
    fake_openai = SimpleNamespace(OpenAI=MagicMock())
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    with patch("lingtai.services.vision.codex.CodexTokenManager") as mock_mgr:
        svc = create_vision_service(
            "codex",
            api_key_env="IGNORED",
            provider_note="from preset",
        )

    from lingtai.services.vision.codex import CodexVisionService

    assert isinstance(svc, CodexVisionService)
    mock_mgr.assert_called_once_with(token_path=None)


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

    with patch("lingtai.services.vision.codex.CodexTokenManager") as mock_mgr_cls:
        mock_mgr_cls.return_value.get_access_token.return_value = "oauth-token"
        from lingtai.services.vision.codex import CodexVisionService

        svc = CodexVisionService(timeout=9.5)
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


def test_vision_setup_no_provider_raises(tmp_path):
    """setup() without provider or service should raise ValueError."""
    agent = make_mock_agent(tmp_path)
    with pytest.raises(ValueError, match="vision capability requires"):
        setup(agent)
