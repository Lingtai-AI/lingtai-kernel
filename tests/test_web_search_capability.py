"""Tests for the canonical unified web capability's search path."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lingtai.agent import Agent
from lingtai.tools.web_search import WebManager, setup
from lingtai.services.websearch import SearchResult, SearchService, create_search_service
from tests._service_helpers import make_gemini_mock_service as make_mock_service




def test_web_missing_query(tmp_path):
    """web search should return a typed failure for missing query."""
    agent = Agent(service=make_mock_service(), agent_name="test", working_dir=tmp_path,
                       capabilities={"web": {"provider": "duckduckgo"}})
    result = agent._tool_handlers["web"]({"action": "search", "input": {"query": ""}})
    assert result.get("status") == "failed"
    assert result.get("error_code") == "INVALID_QUERY"


def test_web_manager_uses_search_service():
    """WebManager should call search_service.search() when available."""
    mock_svc = MagicMock(spec=SearchService)
    mock_svc.search.return_value = [
        SearchResult(title="Result", url="https://example.com", snippet="A snippet")
    ]
    agent = MagicMock()
    mgr = WebManager(agent, search_service=mock_svc)
    result = mgr.handle({"action": "search", "input": {"query": "test"}})
    assert result["status"] == "ok"
    assert result["results"][0]["title"] == "Result"
    mock_svc.search.assert_called_once_with("test", max_results=None)


def test_web_service_exception():
    """WebManager should return error if SearchService raises."""
    mock_svc = MagicMock(spec=SearchService)
    mock_svc.search.side_effect = RuntimeError("connection failed")
    agent = MagicMock()
    mgr = WebManager(agent, search_service=mock_svc)
    result = mgr.handle({"action": "search", "input": {"query": "test"}})
    assert result["status"] == "failed"
    assert result["error_code"] == "SEARCH_FAILED"
    assert "connection failed" not in result["message"]


def test_create_search_service_duckduckgo():
    """Factory should create DuckDuckGoSearchService."""
    from lingtai.services.websearch.duckduckgo import DuckDuckGoSearchService
    svc = create_search_service("duckduckgo")
    assert isinstance(svc, DuckDuckGoSearchService)


def test_create_search_service_requires_key():
    """Factory should raise RuntimeError for providers needing api_key when none given."""
    with pytest.raises(RuntimeError, match="requires an api_key"):
        create_search_service("anthropic")


def test_create_search_service_unknown():
    """Factory should raise ValueError for unknown provider."""
    with pytest.raises(ValueError, match="Unknown web search provider"):
        create_search_service("nonexistent", api_key="key")


def test_create_search_service_rejects_retired_minimax():
    """MiniMax was deleted from the factory 2026-07-28 (issue 11114); it must
    raise the documented ValueError like any other unrecognized name -- never
    an uncaught ModuleNotFoundError from a dangling factory branch."""
    with pytest.raises(ValueError, match="Unknown web search provider"):
        create_search_service("minimax", api_key="key")


def test_create_search_service_rejects_retired_zhipu():
    """Zhipu was deleted from the factory 2026-07-28 (issue 11114); it must
    raise the documented ValueError like any other unrecognized name -- never
    an uncaught ModuleNotFoundError from a dangling factory branch."""
    with pytest.raises(ValueError, match="Unknown web search provider"):
        create_search_service("zhipu", api_key="key")


def test_create_search_service_rejects_unknown_kwargs():
    """The factory API is intentionally narrow; provider kwargs must be explicit."""
    with pytest.raises(TypeError):
        create_search_service("anthropic", api_key="sk-test", unknown=True)


def test_web_with_provider_kwarg(tmp_path):
    """web capability with provider= should create service via factory."""
    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=tmp_path,
        capabilities={"web": {"provider": "duckduckgo"}},
    )
    assert "web" in agent._tool_handlers


def test_web_setup_resolves_api_key_env(monkeypatch):
    """setup() resolves api_key_env before constructing provider services."""
    monkeypatch.setenv("WEB_SEARCH_TEST_API_KEY", "sk-from-env")
    agent = MagicMock()
    agent._config.language = "en"
    agent.service.provider = "openai"
    agent.service._base_url = None

    with patch("lingtai.services.websearch.create_search_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=SearchService)
        mgr = setup(agent, provider="openai", api_key_env="WEB_SEARCH_TEST_API_KEY")
        mgr.handle({"action": "search", "input": {"query": "test"}})

    assert isinstance(mgr, WebManager)
    mock_factory.assert_called_once()
    assert mock_factory.call_args.args == ("openai",)
    assert mock_factory.call_args.kwargs["api_key"] == "sk-from-env"


def test_web_setup_api_key_env_overrides_raw_key(monkeypatch):
    """api_key_env takes precedence over a raw api_key, matching vision."""
    monkeypatch.setenv("WEB_SEARCH_TEST_API_KEY", "sk-from-env")
    agent = MagicMock()
    agent._config.language = "en"
    agent.service.provider = "openai"
    agent.service._base_url = None

    with patch("lingtai.services.websearch.create_search_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=SearchService)
        mgr = setup(
            agent,
            provider="openai",
            api_key="sk-raw",
            api_key_env="WEB_SEARCH_TEST_API_KEY",
        )
        mgr.handle({"action": "search", "input": {"query": "test"}})

    assert mock_factory.call_args.kwargs["api_key"] == "sk-from-env"


def test_inherited_web_env_key_registers(tmp_path, monkeypatch):
    """A provider:inherit web config with env-only credentials boots."""
    from lingtai.kernel.presets import expand_inherit

    monkeypatch.setenv("WEB_SEARCH_TEST_API_KEY", "sk-from-env")
    capabilities = {"web": {"provider": "inherit"}}
    expand_inherit(
        capabilities,
        {
            "provider": "openai",
            "api_key": None,
            "api_key_env": "WEB_SEARCH_TEST_API_KEY",
        },
    )

    with patch("lingtai.services.websearch.create_search_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=SearchService)
        service = make_mock_service()
        service._base_url = None
        agent = Agent(
            service=service,
            agent_name="test",
            working_dir=tmp_path / "test",
            capabilities=capabilities,
        )
        agent._tool_handlers["web"]({"action": "search", "input": {"query": "test"}})

    try:
        assert agent.has_capability("web") is True
        assert "web" in agent._tool_handlers
        call = next(
            call for call in mock_factory.call_args_list
            if call.args == ("openai",)
        )
        assert call.kwargs["api_key"] == "sk-from-env"
    finally:
        agent.stop(timeout=1.0)
