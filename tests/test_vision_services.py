"""Tests for provider-specific VisionService response handling (issue #114, Bug G)."""
from __future__ import annotations

import builtins
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _make_openai_service(monkeypatch, raw_response):
    """Build an OpenAIVisionService whose client returns `raw_response`."""
    completions = MagicMock()
    completions.create.return_value = raw_response
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    openai_cls = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai_cls))

    from lingtai.services.vision.openai import OpenAIVisionService

    return OpenAIVisionService(api_key="sk-test", model="gpt-4o", base_url="http://127.0.0.1:34891")


def test_openai_vision_raises_clear_error_on_string_response(monkeypatch, tmp_path):
    """Bug G: a raw `str` body (proxy served HTML/non-JSON) raises a clear RuntimeError.

    Previously `raw.choices` on a str raised the mystifying
    `'str' object has no attribute 'choices'`.
    """
    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG fake")

    html_body = "<!DOCTYPE html><html><body>404 Not Found dashboard</body></html>"
    svc = _make_openai_service(monkeypatch, html_body)

    with pytest.raises(RuntimeError) as exc:
        svc.analyze_image(str(img), prompt="what is this?")

    msg = str(exc.value)
    assert "ChatCompletion" in msg or "JSON" in msg
    assert "str" in msg
    # surfaces a snippet of the actual body so the user can diagnose
    assert "404 Not Found dashboard" in msg
    # no misleading AttributeError leaked through
    assert "object has no attribute" not in msg


def test_openai_vision_raises_on_non_completion_object(monkeypatch, tmp_path):
    """A non-str object without `.choices` also raises a clear RuntimeError, not AttributeError."""
    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG fake")

    svc = _make_openai_service(monkeypatch, {"unexpected": "dict"})

    with pytest.raises(RuntimeError) as exc:
        svc.analyze_image(str(img))
    assert "object has no attribute" not in str(exc.value)


def test_openai_vision_returns_content_on_valid_response(monkeypatch, tmp_path):
    """A well-formed ChatCompletion still returns its message content."""
    img = tmp_path / "chart.png"
    img.write_bytes(b"\x89PNG fake")

    message = SimpleNamespace(content="a candlestick chart")
    choice = SimpleNamespace(message=message)
    raw = SimpleNamespace(choices=[choice])
    svc = _make_openai_service(monkeypatch, raw)

    assert svc.analyze_image(str(img)) == "a candlestick chart"


def test_anthropic_vision_service_accepts_base_url(monkeypatch):
    """C-2 sibling: AnthropicVisionService accepts base_url for local proxies."""
    anthropic_cls = MagicMock()
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=anthropic_cls))

    from lingtai.services.vision.anthropic import AnthropicVisionService

    AnthropicVisionService(api_key="sk-test", model="GLM-5.1", base_url="http://127.0.0.1:34891")
    anthropic_cls.assert_called_once_with(api_key="sk-test", base_url="http://127.0.0.1:34891")


def test_anthropic_vision_service_omits_base_url_when_unset(monkeypatch):
    """No base_url → default Anthropic endpoint (no base_url kwarg passed)."""
    anthropic_cls = MagicMock()
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=anthropic_cls))

    from lingtai.services.vision.anthropic import AnthropicVisionService

    AnthropicVisionService(api_key="sk-test")
    anthropic_cls.assert_called_once_with(api_key="sk-test")


@pytest.mark.parametrize(
    ("provider", "provider_module"),
    [
        ("openai", "lingtai.services.vision.openai"),
        ("anthropic", "lingtai.services.vision.anthropic"),
    ],
)
@pytest.mark.parametrize("api_key", [None, "", "  \t"])
def test_factory_rejects_blank_api_key_before_provider_import(
    monkeypatch, provider, provider_module, api_key
):
    """Factory credential admission must precede every API provider import."""
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-sdk-sentinel")
    real_import = builtins.__import__

    def block_provider_import(name, *args, **kwargs):
        if name == provider_module or name.startswith(f"{provider_module}."):
            raise AssertionError(f"provider module imported before credential admission: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_provider_import)
    with pytest.raises(ValueError, match="api_key is required"):
        from lingtai.services.vision import create_vision_service

        create_vision_service(provider, api_key=api_key)


@pytest.mark.parametrize(
    ("provider", "sdk_name", "service_import", "service_name"),
    [
        ("openai", "openai", "lingtai.services.vision.openai", "OpenAIVisionService"),
        ("anthropic", "anthropic", "lingtai.services.vision.anthropic", "AnthropicVisionService"),
    ],
)
@pytest.mark.parametrize("api_key", [None, "", "  \t"])
def test_direct_service_rejects_blank_api_key_before_sdk_import(
    monkeypatch, provider, sdk_name, service_import, service_name, api_key
):
    """Direct constructors must not let their SDK adopt an ambient credential."""
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-sdk-sentinel")
    module = __import__(service_import, fromlist=[service_name])
    service_cls = getattr(module, service_name)
    real_import = builtins.__import__

    def block_sdk_import(name, *args, **kwargs):
        if name == sdk_name or name.startswith(f"{sdk_name}."):
            raise AssertionError(f"SDK imported before credential admission: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", block_sdk_import)
    with pytest.raises(ValueError, match="api_key is required"):
        service_cls(api_key=api_key)


def test_factory_preserves_original_nonblank_key(monkeypatch):
    """Valid keys are admitted without trimming or otherwise changing them."""
    openai_cls = MagicMock(return_value=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=openai_cls))

    from lingtai.services.vision import create_vision_service

    create_vision_service("openai", api_key="  sk-preserve  ")
    openai_cls.assert_called_once_with(api_key="  sk-preserve  ")


@pytest.mark.parametrize("provider", ["gemini", "mimo", "minimax", "zhipu", "custom"])
def test_factory_rejects_removed_vision_providers(provider):
    """Only the openai/anthropic/codex/mlx services remain in the factory."""
    from lingtai.services.vision import create_vision_service

    with pytest.raises(ValueError, match="Unsupported vision provider"):
        create_vision_service(provider, api_key="sk-test")


@pytest.mark.parametrize("module", ["gemini", "mimo"])
def test_removed_vision_service_modules_are_gone(module):
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"lingtai.services.vision.{module}")
