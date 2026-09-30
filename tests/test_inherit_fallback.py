"""Tests for graceful inherit fallback in capability setup().

When a capability's resolved provider isn't in its supported list, it should
either fall back to its declared agnostic fallback or silently skip
registration. Never raise."""
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _stub_agent(language: str = "en"):
    a = MagicMock()
    a._config = MagicMock()
    a._config.language = language
    a._tool_handlers = {}
    a._tool_schemas = {}
    a._capability_managers = {}
    a._log_events = []
    # service._base_url = None so resolve_media_host returns None (no real LLM)
    a.service = MagicMock()
    a.service._base_url = None
    def _log(event, **kw):
        a._log_events.append((event, kw))
    a._log = _log
    # add_tool is what setup() typically calls — record it on the stub
    def _add_tool(name, **kw):
        a._tool_handlers[name] = kw.get("handler")
        a._tool_schemas[name] = kw.get("schema")
    a.add_tool = _add_tool
    a.add_capability = MagicMock()
    a.working_dir = Path.cwd()
    a.official_tool_plugins = {}

    def _mount_official(transaction):
        transaction.consume()
        plugin = transaction.plugin
        a.add_tool(
            plugin.name,
            schema=plugin.schema,
            handler=plugin.handler,
            description=plugin.description,
            glossary_package=plugin.glossary_package,
        )
        transaction.mark_mounted(a)

    def _claim_official(transaction):
        a.official_tool_plugins[transaction.declaration.name] = transaction.declaration

    a._mount_official_tool = _mount_official
    a._claim_official_tool = _claim_official
    a._authorize_official_tool_declaration = lambda _declaration: None
    a._record_official_tool_binding = lambda _declaration, _plugin: None
    return a


def test_web_unknown_provider_falls_back_to_duckduckgo():
    """web with provider='deepseek' (unknown) falls back to duckduckgo."""
    from lingtai.tools.web_search import setup as ws_setup
    a = _stub_agent()
    # Pretend agent's main LLM is deepseek (which has no web search service)
    ws_setup(a, provider="deepseek", api_key=None)
    # Must NOT raise; should have registered web via duckduckgo
    assert "web" in a._tool_handlers


def test_vision_unknown_provider_registers_manual_only_route():
    """Current preset routing keeps vision discoverable when direct setup is unavailable."""
    from lingtai.tools.vision import VisionManager, setup as vision_setup

    a = _stub_agent()
    result = vision_setup(a, provider="deepseek", api_key=None, api_key_env="X")
    assert isinstance(result, VisionManager)
    assert "vision" in a._tool_handlers
    assert "manual" in result._manual_reason
    assert not any(event == "capability_skipped" for event, _ in a._log_events)


def test_web_supported_provider_uses_it(monkeypatch):
    """web with provider='openai' (supported, ungated) uses openai, no fallback."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from lingtai.tools.web_search import setup as ws_setup
    a = _stub_agent()
    ws_setup(a, provider="openai", api_key="sk-test")
    assert "web" in a._tool_handlers


def test_web_gated_provider_raises_actionable_error(monkeypatch):
    """web with provider='anthropic' is explicit opt-in through settings only;
    a direct provider= composition rejects it outright rather than silently
    admitting or falling back (2026-07-28 canonical-provider-routing repair).
    Anthropic is an active canonical engine, never "retired" -- this raises
    SettingsOnlyProviderError, not RetiredProviderError (g2 repair)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from lingtai.tools.web_search import SettingsOnlyProviderError, setup as ws_setup
    a = _stub_agent()
    with pytest.raises(SettingsOnlyProviderError):
        ws_setup(a, provider="anthropic", api_key="sk-test")


def test_web_removed_gemini_engine_falls_back_without_raising():
    """The removed ``gemini`` engine is now an unknown name: composition takes
    the legacy DuckDuckGo fallback instead of failing."""
    from lingtai.tools.web_search import setup as ws_setup

    a = _stub_agent()
    ws_setup(a, provider="gemini", api_key="sk-test")
    assert "web" in a._tool_handlers


def test_web_no_provider_uses_duckduckgo():
    """web with no provider arg defaults to duckduckgo (existing behavior)."""
    from lingtai.tools.web_search import setup as ws_setup
    a = _stub_agent()
    ws_setup(a)
    assert "web" in a._tool_handlers


def test_shipped_init_jsonc_web_block_registers_the_web_tool():
    """The canonical init.jsonc `web` block must compose successfully through
    setup() -- a template-derived Agent must never silently lose the whole
    `web` capability (no search, no browse, no manual) to a composition-time
    error swallowed by Agent.__init__'s `except (ValueError, ImportError,
    TypeError): capability_skipped` catch (Opus review 2026-07-28, blocker B1).
    Composes the exact shipped dict, not a hand-written stand-in, so any
    future edit to init.jsonc that reintroduces a retired/gated provider name
    fails this test instead of silently deregistering `web` for every new
    template-derived agent."""
    from pathlib import Path

    from lingtai.kernel.config_resolve import load_jsonc
    from lingtai.tools.web_search import setup as ws_setup

    root = Path(__file__).parents[1]
    data = load_jsonc(root / "src/lingtai/init.jsonc")
    web_kwargs = data["manifest"]["capabilities"]["web"]

    a = _stub_agent()
    ws_setup(a, **web_kwargs)

    assert "web" in a._tool_handlers
    assert not any(event == "capability_skipped" for event, _ in a._log_events)


def test_shipped_init_jsonc_web_block_names_no_retired_or_gated_provider():
    """A narrower, faster-failing companion to the composition test above:
    directly asserts the shipped `web` block never names minimax/zhipu
    (RetiredProviderError) or anthropic (SettingsOnlyProviderError)
    through a direct default_engine=/provider= route, so a future edit is
    caught at the exact invariant even before touching setup()."""
    from pathlib import Path

    from lingtai.kernel.config_resolve import load_jsonc
    from lingtai.tools.web_search import PROVIDERS

    root = Path(__file__).parents[1]
    data = load_jsonc(root / "src/lingtai/init.jsonc")
    web_kwargs = data["manifest"]["capabilities"]["web"]

    named_providers = set()
    if "provider" in web_kwargs:
        named_providers.add(web_kwargs["provider"])
    if "default_engine" in web_kwargs:
        named_providers.add(web_kwargs["default_engine"])
    for name, spec in web_kwargs.get("engines", {}).items():
        named_providers.add(spec.get("provider", name) if isinstance(spec, dict) else name)

    assert named_providers <= set(PROVIDERS["providers"]), (
        f"init.jsonc web block names a retired/unadmitted provider: {named_providers - set(PROVIDERS['providers'])}"
    )
    assert "minimax" not in named_providers
    assert "zhipu" not in named_providers
