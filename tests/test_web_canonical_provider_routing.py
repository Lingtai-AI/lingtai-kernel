"""Focused table-driven checks for the canonical web provider ownership/routing
change: the real no-config default path, settings-only Anthropic/Gemini
opt-in with canonical-backend eligibility, explicit retired-provider
rejection, the one typed-error-only OpenAI->DuckDuckGo runtime fallback, and
usable (URL-bearing) results from all three canonical providers. Each case
protects a distinct invariant from the 2026-07-28 web-canonical-provider-
routing contract and its 2026-07-28 21:40 PDT repair addendum.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lingtai.services.websearch import SearchProviderError, SearchResult, SearchService
from lingtai.services.websearch.anthropic import AnthropicSearchError, AnthropicSearchService, _extract_results as anthropic_extract
from lingtai.services.websearch.gemini import GeminiSearchError, GeminiSearchService, _extract_results as gemini_extract
from lingtai.services.websearch.openai import OpenAISearchError, OpenAISearchService, _extract_results as openai_extract
from lingtai.tools.web_search import PROVIDERS, RetiredProviderError, SettingsOnlyProviderError, _same_provider_identity, setup


class _Port:
    def resolve(self, hostname: str, *, timeout_s: float):
        return ["93.184.216.34"]

    def request(self, url: str, *, resolved, max_bytes: int, timeout_s: float):
        raise AssertionError("browse must not be reached by these search-only cases")


def _agent(tmp_path, provider: str | None) -> MagicMock:
    agent = MagicMock()
    agent._config.language = "en"
    agent._working_dir = tmp_path
    if provider is None:
        agent.service.provider = MagicMock()  # not a str -> never eligible
    else:
        agent.service.provider = provider
    return agent


def _write_settings(tmp_path: Path, engine: str) -> None:
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir(exist_ok=True)
    (settings_dir / "web.search.json").write_text(json.dumps({"schema_version": 1, "engine": engine}))


# ─── Canonical backend identity predicate (private to web_search) ─────────

def test_same_provider_identity_exact_match_only():
    assert _same_provider_identity(_agent(tempfile.mkdtemp(), "anthropic"), "anthropic") is True
    assert _same_provider_identity(_agent(tempfile.mkdtemp(), "gemini"), "gemini") is True


@pytest.mark.parametrize("provider", ["claude-code", "claude_code", "custom", "openrouter", "codex", "ANTHROPIC-compat"])
def test_same_provider_identity_rejects_aliases_and_substrings(provider):
    assert _same_provider_identity(_agent(tempfile.mkdtemp(), provider), "anthropic") is False


def test_same_provider_identity_handles_missing_service_attribute():
    agent = MagicMock(spec=[])  # no .service at all
    assert _same_provider_identity(agent, "anthropic") is False


def test_same_provider_identity_rejects_non_gated_name():
    # openai is a real canonical provider but not one of the two
    # settings-gated engines, so it is never a valid *name* argument here.
    assert _same_provider_identity(_agent(tempfile.mkdtemp(), "openai"), "openai") is False


# ─── PROVIDERS admission list ───────────────────────────────────────────────

def test_minimax_and_zhipu_absent_from_built_in_providers():
    assert "minimax" not in PROVIDERS["providers"]
    assert "zhipu" not in PROVIDERS["providers"]
    assert set(PROVIDERS["providers"]) == {"duckduckgo", "gemini", "anthropic", "openai"}


# ─── Real no-config default path (repair item 1) ───────────────────────────

def test_real_no_config_default_selects_openai_via_standard_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-real-env-test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = _agent(tmp_path, "claude-code")  # deliberately not "openai" -- must never matter
    with patch("lingtai.services.websearch.create_search_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=SearchService)
        # No engines=, no provider=, no default_engine=, no search_service=:
        # the actual ordinary runtime path with zero operator config.
        mgr = setup(agent, browser_port=_Port())
        result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["current_setting"]["source"] == "built_in_default"
    assert result["engine"] == "openai"
    mock_factory.assert_called_once_with("openai", api_key="sk-real-env-test", model=None)


def test_real_no_config_default_falls_back_to_duckduckgo_without_openai_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    agent = _agent(tmp_path, None)
    mgr = setup(agent, browser_port=_Port())
    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.return_value = [
            SearchResult(title="t", url="https://example.test", snippet="s"),
        ]
        result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["current_setting"]["source"] == "built_in_default"
    assert result["engine"] == "duckduckgo"
    assert result["status"] == "ok"
    mock_ddg_cls.return_value.search.assert_called_once_with("q", max_results=None)


def test_real_no_config_default_reports_anthropic_and_gemini_as_selectable_but_unselected(tmp_path, monkeypatch):
    # Only current_setting/engine-selection diagnostics are asserted here, no
    # search execution needed -- browse the diagnostics without ever
    # constructing (let alone calling) any provider service.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("GEMINI_API_KEY", "sk-gem-test")
    agent = _agent(tmp_path, None)
    mgr = setup(agent, browser_port=_Port())
    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.return_value = []
        result = mgr.handle({"action": "search", "input": {"query": "q"}})
    names = set(result["current_setting"]["available_engine_names"])
    assert names == {"duckduckgo", "openai", "anthropic", "gemini"}
    statuses = result["current_setting"]["available_engine_status"]
    assert statuses["anthropic"] == "available"
    assert statuses["gemini"] == "available"
    # present and available, but never the *selected* default:
    assert result["engine"] == "duckduckgo"


def test_real_no_config_default_never_touches_agent_service_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-standard-env")
    agent = _agent(tmp_path, "openai")
    agent.service.api_key = "should-never-be-read"
    with patch("lingtai.services.websearch.create_search_service") as mock_factory:
        mock_factory.return_value = MagicMock(spec=SearchService)
        mgr = setup(agent, browser_port=_Port())
        mgr.handle({"action": "search", "input": {"query": "q"}})
    assert mock_factory.call_args.kwargs["api_key"] == "sk-standard-env"
    assert mock_factory.call_args.kwargs["api_key"] != "should-never-be-read"


# ─── Anthropic/Gemini: settings-only opt-in (repair item 2) ───────────────

@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
def test_default_engine_kwarg_rejects_gated_engines_at_composition(tmp_path, engine):
    # Anthropic/Gemini are active canonical providers, never "retired" --
    # this must raise SettingsOnlyProviderError, distinct from
    # RetiredProviderError (reserved for minimax/zhipu, g2 repair item 1).
    with pytest.raises(SettingsOnlyProviderError):
        setup(_agent(tmp_path, engine), default_engine=engine, engines={engine: {"api_key": "x"}}, browser_port=_Port())


@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
def test_provider_kwarg_rejects_gated_engines_at_composition(tmp_path, engine):
    with (
        patch("lingtai.services.websearch.create_search_service") as mock_factory,
        pytest.raises(SettingsOnlyProviderError),
    ):
        setup(_agent(tmp_path, engine), provider=engine, api_key="x", browser_port=_Port())
    mock_factory.assert_not_called()


@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
def test_settings_only_provider_error_is_not_a_retired_provider_error(tmp_path, engine):
    # The two error classes must stay distinct types -- a caller catching
    # only RetiredProviderError must not accidentally swallow a
    # SettingsOnlyProviderError for an active canonical provider.
    assert not issubclass(SettingsOnlyProviderError, RetiredProviderError)
    assert not issubclass(RetiredProviderError, SettingsOnlyProviderError)


@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
def test_gated_engine_present_in_engines_map_is_never_the_default(tmp_path, engine):
    # engines={} may still declare a bounded spec for credential/service
    # injection (tests/integration), but composing it must never make it the
    # *default* selection -- only a settings-file selection can.
    agent = _agent(tmp_path, engine)
    service = MagicMock(spec=SearchService)
    mgr = setup(agent, engines={engine: {"search_service": service}}, browser_port=_Port())
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    # With no duckduckgo/openai spec composed, the built-in default resolver
    # must never land on the gated engine either -- it reports an honest
    # SEARCH_ENGINE_UNAVAILABLE rather than silently selecting/constructing
    # the settings-gated provider.
    assert result.get("engine") != engine
    assert result["status"] == "failed"
    assert not service.search.called


@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
def test_settings_file_selection_succeeds_on_canonical_backend(tmp_path, engine):
    agent = _agent(tmp_path, engine)
    service = MagicMock(spec=SearchService)
    service.search.return_value = [SearchResult(title="t", url="https://example.test", snippet="s")]
    mgr = setup(agent, engines={engine: {"search_service": service}, "duckduckgo": {}}, browser_port=_Port())
    _write_settings(tmp_path, engine)
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["status"] == "ok"
    assert result["engine"] == engine
    service.search.assert_called_once_with("q", max_results=None)


@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
@pytest.mark.parametrize("backend", ["claude-code", "openai", "openrouter", "custom", "codex"])
def test_settings_file_selection_fails_on_every_noncanonical_backend(tmp_path, engine, backend):
    agent = _agent(tmp_path, backend)
    service = MagicMock(spec=SearchService)
    mgr = setup(agent, engines={engine: {"search_service": service}, "duckduckgo": {}}, browser_port=_Port())
    _write_settings(tmp_path, engine)
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["status"] == "failed"
    assert result["error_code"] == "PROVIDER_BACKEND_INELIGIBLE"
    assert not service.search.called
    assert "secret" not in json.dumps(result).lower()


def test_settings_file_selection_is_hot_read_live_change_no_refresh(tmp_path):
    agent = _agent(tmp_path, "anthropic")
    service = MagicMock(spec=SearchService)
    service.search.return_value = [SearchResult(title="t", url="https://example.test", snippet="s")]
    ddg_service = MagicMock(spec=SearchService)
    ddg_service.search.return_value = [SearchResult(title="d", url="https://example.test/ddg", snippet="s")]
    mgr = setup(
        agent,
        engines={"anthropic": {"search_service": service}, "duckduckgo": {"search_service": ddg_service}},
        browser_port=_Port(),
    )

    _write_settings(tmp_path, "anthropic")
    first = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert first["engine"] == "anthropic"

    _write_settings(tmp_path, "duckduckgo")
    second = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert second["engine"] == "duckduckgo"
    assert second["current_setting"]["source"] == "settings/web.search.json"
    ddg_service.search.assert_called_once_with("q", max_results=None)


@pytest.mark.parametrize("engine", ["anthropic", "gemini"])
def test_settings_selected_engine_runtime_failure_does_not_silently_fall_back(tmp_path, engine):
    agent = _agent(tmp_path, engine)
    service = MagicMock(spec=SearchService)
    service.search.side_effect = RuntimeError("provider exploded")
    mgr = setup(agent, engines={engine: {"search_service": service}, "duckduckgo": {}}, browser_port=_Port())
    _write_settings(tmp_path, engine)

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    mock_ddg_cls.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_code"] == "SEARCH_FAILED"


# ─── Typed provider errors: no swallow-to-[], no DDG for Anthropic/Gemini (g2 repair item 2) ─

def test_anthropic_service_raises_typed_error_on_sdk_failure():
    svc = AnthropicSearchService(api_key="sk-test")
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("rate limited: sk-super-secret-token")
    with patch("anthropic.Anthropic", return_value=fake_client):
        with pytest.raises(AnthropicSearchError) as excinfo:
            svc.search("q")
    assert excinfo.value.provider == "anthropic"
    assert excinfo.value.failure_class == "RuntimeError"
    assert "sk-super-secret-token" not in str(excinfo.value)


def test_gemini_service_raises_typed_error_on_sdk_failure():
    svc = GeminiSearchService(api_key="sk-test")
    fake_client = MagicMock()
    fake_client.models.generate_content.side_effect = RuntimeError("boom: sk-super-secret-token")
    with patch("google.genai.Client", return_value=fake_client):
        with pytest.raises(GeminiSearchError) as excinfo:
            svc.search("q")
    assert excinfo.value.provider == "gemini"
    assert excinfo.value.failure_class == "RuntimeError"
    assert "sk-super-secret-token" not in str(excinfo.value)


def test_anthropic_extract_results_raises_on_in_body_web_search_tool_result_error():
    # Anthropic's web search tool can fail inside an HTTP-200 response
    # ("the Claude API still returns a 200 (success) response"); content is
    # then a *single* error object, not a list of result blocks.
    error_content = SimpleNamespace(type="web_search_tool_result_error", error_code="max_uses_exceeded")
    block = SimpleNamespace(type="web_search_tool_result", tool_use_id="x", content=error_content)
    raw = SimpleNamespace(content=[block])
    with pytest.raises(AnthropicSearchError) as excinfo:
        anthropic_extract(raw, 5)
    assert "max_uses_exceeded" in excinfo.value.failure_class


@pytest.mark.parametrize("engine,service_cls,error_cls", [
    ("anthropic", AnthropicSearchService, AnthropicSearchError),
    ("gemini", GeminiSearchService, GeminiSearchError),
])
def test_settings_selected_engine_typed_provider_error_end_to_end_never_calls_ddg(tmp_path, engine, service_cls, error_cls):
    # Provider-shaped end-to-end: the real adapter class raises its own
    # typed error, WebManager reports SEARCH_FAILED with bounded
    # provider_failure_class provenance, and DuckDuckGo is never invoked --
    # only OpenAI has an automatic runtime fallback.
    agent = _agent(tmp_path, engine)
    service = MagicMock(spec=SearchService)
    service.search.side_effect = error_cls("AuthenticationError")
    mgr = setup(agent, engines={engine: {"search_service": service}, "duckduckgo": {}}, browser_port=_Port())
    _write_settings(tmp_path, engine)

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    mock_ddg_cls.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_code"] == "SEARCH_FAILED"
    assert result["provider_failure_class"] == "AuthenticationError"


def test_error_hierarchy_openai_anthropic_gemini_share_search_provider_error_base():
    assert issubclass(OpenAISearchError, SearchProviderError)
    assert issubclass(AnthropicSearchError, SearchProviderError)
    assert issubclass(GeminiSearchError, SearchProviderError)
    # Only OpenAISearchError triggers the DDG fallback -- the other two must
    # not be (mis)treated as that exact subclass.
    assert not issubclass(AnthropicSearchError, OpenAISearchError)
    assert not issubclass(GeminiSearchError, OpenAISearchError)


# ─── Retired providers fail explicitly, never DuckDuckGo (repair item 3) ──

@pytest.mark.parametrize("retired", ["minimax", "zhipu"])
def test_provider_kwarg_rejects_retired_providers_explicitly(tmp_path, retired):
    with (
        patch("lingtai.services.websearch.create_search_service") as mock_factory,
        pytest.raises(RetiredProviderError),
    ):
        setup(_agent(tmp_path, "openai"), provider=retired, api_key="x", browser_port=_Port())
    mock_factory.assert_not_called()


@pytest.mark.parametrize("retired", ["minimax", "zhipu"])
def test_default_engine_kwarg_rejects_retired_providers_explicitly(tmp_path, retired):
    with pytest.raises(RetiredProviderError):
        setup(_agent(tmp_path, "openai"), default_engine=retired, engines={retired: {"api_key": "x"}}, browser_port=_Port())


@pytest.mark.parametrize("retired", ["minimax", "zhipu"])
def test_engines_map_rejects_retired_providers_explicitly_never_duckduckgo(tmp_path, retired):
    with (
        patch("lingtai.services.websearch.create_search_service") as mock_factory,
        pytest.raises(RetiredProviderError),
    ):
        setup(_agent(tmp_path, "openai"), engines={retired: {"api_key": "x"}}, browser_port=_Port())
    mock_factory.assert_not_called()


def test_engines_map_still_supports_legacy_unrecognized_provider_fallback(tmp_path):
    # A genuinely unrecognized/inherited legacy provider name (never one of
    # the deliberately-retired minimax/zhipu) keeps the pre-existing
    # legacy_fallback_from -> DuckDuckGo behavior unchanged.
    agent = _agent(tmp_path, "openai")
    real_ddg = MagicMock(spec=SearchService)
    real_ddg.search.return_value = [SearchResult(title="t", url="https://example.test", snippet="s")]
    mgr = setup(agent, engines={"some-old-preset-provider": {"api_key": "x"}, "duckduckgo": {"search_service": real_ddg}}, browser_port=_Port())
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["actual_engine"] == "duckduckgo"
    assert result["current_setting"]["legacy_fallback_from"] == "some-old-preset-provider"


# ─── OpenAI Responses API shape and typed-error-only fallback (repair items 5, 7) ─

def test_openai_service_uses_responses_api_not_chat_completions():
    svc = OpenAISearchService(api_key="sk-test", model="gpt-5.6")
    annotation = SimpleNamespace(type="url_citation", url="https://example.test/a", title="A")
    block = SimpleNamespace(type="output_text", text="answer text", annotations=[annotation])
    message = SimpleNamespace(type="message", content=[block])
    fake_client = MagicMock()
    fake_client.responses.create.return_value = SimpleNamespace(output=[message], output_text="answer text")
    with patch("openai.OpenAI", return_value=fake_client):
        results = svc.search("what is lingtai")
    fake_client.responses.create.assert_called_once_with(
        model="gpt-5.6", tools=[{"type": "web_search"}], input="what is lingtai",
    )
    assert not fake_client.chat.completions.create.called
    assert results == [SearchResult(title="A", url="https://example.test/a", snippet="answer text")]


def test_openai_service_raises_typed_error_on_sdk_failure():
    svc = OpenAISearchService(api_key="sk-test")
    fake_client = MagicMock()
    fake_client.responses.create.side_effect = RuntimeError("rate limited")
    with patch("openai.OpenAI", return_value=fake_client):
        with pytest.raises(OpenAISearchError) as excinfo:
            svc.search("q")
    assert excinfo.value.failure_class == "RuntimeError"
    assert "rate limited" not in str(excinfo.value)


def test_openai_runtime_failure_falls_back_to_duckduckgo_once(tmp_path):
    agent = _agent(tmp_path, "openai")
    failing_service = MagicMock(spec=SearchService)
    failing_service.search.side_effect = OpenAISearchError("Timeout")
    mgr = setup(agent, engines={"openai": {"search_service": failing_service}}, browser_port=_Port())

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.return_value = [
            SearchResult(title="fallback", url="https://example.test", snippet="s"),
        ]
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    assert result["status"] == "ok"
    assert result["engine"] == "openai"
    assert result["actual_engine"] == "duckduckgo"
    assert "comment" in result and "OpenAI" in result["comment"] and "DuckDuckGo" in result["comment"]
    assert result["openai_failure_class"] == "Timeout"
    assert result["count"] == 1
    mock_ddg_cls.return_value.search.assert_called_once_with("q", max_results=None)


def test_openai_runtime_failure_no_secrets_in_comment_or_diagnostics(tmp_path):
    agent = _agent(tmp_path, "openai")
    failing_service = MagicMock(spec=SearchService)
    failing_service.search.side_effect = OpenAISearchError("AuthenticationError")
    mgr = setup(agent, engines={"openai": {"search_service": failing_service, "api_key": "sk-super-secret"}}, browser_port=_Port())

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.return_value = []
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    assert "sk-super-secret" not in json.dumps(result)


def test_openai_and_duckduckgo_both_fail_reports_typed_failure_no_second_retry(tmp_path):
    agent = _agent(tmp_path, "openai")
    failing_service = MagicMock(spec=SearchService)
    failing_service.search.side_effect = OpenAISearchError("ServerError")
    mgr = setup(agent, engines={"openai": {"search_service": failing_service}}, browser_port=_Port())

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.side_effect = RuntimeError("ddg down")
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    assert result["status"] == "failed"
    assert result["error_code"] == "SEARCH_FAILED"
    assert result["openai_failure_class"] == "ServerError"
    assert result["duckduckgo_failure_class"] == "RuntimeError"
    mock_ddg_cls.return_value.search.assert_called_once_with("q", max_results=None)


def test_openai_programming_bug_does_not_trigger_duckduckgo_fallback(tmp_path):
    # A bug (TypeError/AttributeError from _run_service/_result_fields, or
    # anything OpenAISearchService itself did not raise as OpenAISearchError)
    # must fail normally -- Jason authorized runtime *provider* failure
    # fallback, not hiding manager/programming defects.
    agent = _agent(tmp_path, "openai")
    buggy_service = MagicMock(spec=SearchService)
    buggy_service.search.side_effect = TypeError("not a provider failure")
    mgr = setup(agent, engines={"openai": {"search_service": buggy_service}}, browser_port=_Port())

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    mock_ddg_cls.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_code"] == "SEARCH_FAILED"


def test_non_openai_engine_failure_does_not_trigger_duckduckgo_fallback(tmp_path):
    agent = _agent(tmp_path, "anthropic")
    failing_service = MagicMock(spec=SearchService)
    failing_service.search.side_effect = RuntimeError("boom")
    mgr = setup(agent, engines={"anthropic": {"search_service": failing_service}, "duckduckgo": {}}, browser_port=_Port())
    _write_settings(tmp_path, "anthropic")

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        result = mgr.handle({"action": "search", "input": {"query": "q"}})

    mock_ddg_cls.assert_not_called()
    assert result["status"] == "failed"
    assert result["error_code"] == "SEARCH_FAILED"


# ─── Usable canonical-provider results: real citation URLs (repair item 4) ─

def test_openai_extract_results_uses_real_url_citations():
    annotation = SimpleNamespace(type="url_citation", url="https://en.wikipedia.org/wiki/Test", title="Test - Wikipedia")
    block = SimpleNamespace(type="output_text", text="Some answer", annotations=[annotation])
    message = SimpleNamespace(type="message", content=[block])
    raw = SimpleNamespace(output=[message], output_text="Some answer")
    results = openai_extract(raw, 5)
    assert results == [SearchResult(title="Test - Wikipedia", url="https://en.wikipedia.org/wiki/Test", snippet="Some answer")]


def test_openai_extract_results_falls_back_to_bounded_narrative_with_no_url(tmp_path):
    message = SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="answer", annotations=[])])
    raw = SimpleNamespace(output=[message], output_text="answer text, no citations")
    results = openai_extract(raw, 5)
    assert results == [SearchResult(title="OpenAI Web Search", url="", snippet="answer text, no citations")]


def test_anthropic_extract_results_uses_real_web_search_result_location():
    citation = SimpleNamespace(type="web_search_result_location", url="https://en.wikipedia.org/wiki/Claude_Shannon", title="Claude Shannon - Wikipedia", cited_text="born 1916")
    text_block = SimpleNamespace(type="text", text="Claude Shannon was born...", citations=[citation])
    raw = SimpleNamespace(content=[text_block])
    results = anthropic_extract(raw, 5)
    assert results == [SearchResult(title="Claude Shannon - Wikipedia", url="https://en.wikipedia.org/wiki/Claude_Shannon", snippet="born 1916")]


def test_anthropic_extract_results_uses_web_search_result_block_when_no_text_citations():
    result_item = SimpleNamespace(type="web_search_result", url="https://example.test/page", title="Example")
    tool_result_block = SimpleNamespace(type="web_search_tool_result", content=[result_item])
    raw = SimpleNamespace(content=[tool_result_block])
    results = anthropic_extract(raw, 5)
    assert results == [SearchResult(title="Example", url="https://example.test/page", snippet="")]


def test_anthropic_extract_results_falls_back_to_bounded_narrative_with_no_url():
    text_block = SimpleNamespace(type="text", text="narrative only, no sources", citations=[])
    raw = SimpleNamespace(content=[text_block])
    results = anthropic_extract(raw, 5)
    assert results == [SearchResult(title="Anthropic Web Search", url="", snippet="narrative only, no sources")]


def test_gemini_extract_results_uses_real_grounding_chunks():
    web = SimpleNamespace(uri="https://example.com/page", title="example.com")
    chunk = SimpleNamespace(web=web)
    grounding_metadata = SimpleNamespace(grounding_chunks=[chunk])
    part = SimpleNamespace(text="Grounded answer", thought=False)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content, grounding_metadata=grounding_metadata)
    raw = SimpleNamespace(candidates=[candidate])
    results = gemini_extract(raw, 5)
    assert results == [SearchResult(title="example.com", url="https://example.com/page", snippet="")]


def test_gemini_extract_results_falls_back_to_bounded_narrative_when_grounding_metadata_is_none():
    part = SimpleNamespace(text="answer with no grounding", thought=False)
    content = SimpleNamespace(parts=[part])
    candidate = SimpleNamespace(content=content, grounding_metadata=None)
    raw = SimpleNamespace(candidates=[candidate])
    results = gemini_extract(raw, 5)
    assert results == [SearchResult(title="Gemini Web Search", url="", snippet="answer with no grounding")]


def test_gemini_extract_results_handles_empty_candidates():
    raw = SimpleNamespace(candidates=[])
    assert gemini_extract(raw, 5) == []


def test_web_manager_preserves_real_citation_as_link_ref(tmp_path):
    agent = _agent(tmp_path, "openai")
    svc = MagicMock(spec=SearchService)
    svc.search.return_value = [SearchResult(title="Wiki", url="https://en.wikipedia.org/wiki/X", snippet="cited text")]
    mgr = setup(agent, engines={"openai": {"search_service": svc}}, browser_port=_Port())
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["count"] == 1
    assert result["results"][0]["link_ref"]
    assert result["results"][0]["url"] == "https://en.wikipedia.org/wiki/X"


def test_web_manager_preserves_bounded_narrative_without_fake_link_ref(tmp_path):
    agent = _agent(tmp_path, "openai")
    svc = MagicMock(spec=SearchService)
    svc.search.return_value = [SearchResult(title="OpenAI Web Search", url="", snippet="narrative only")]
    mgr = setup(agent, engines={"openai": {"search_service": svc}}, browser_port=_Port())
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["count"] == 1
    assert result["results"][0]["url"] == ""
    assert result["results"][0]["link_ref"] is None


def test_web_manager_still_discards_a_fully_empty_result_item(tmp_path):
    # A result with neither URL, title, nor snippet is genuinely empty --
    # still discarded, distinct from a real bounded narrative result.
    agent = _agent(tmp_path, "openai")
    svc = MagicMock(spec=SearchService)
    svc.search.return_value = [SearchResult(title="", url="", snippet="")]
    mgr = setup(agent, engines={"openai": {"search_service": svc}}, browser_port=_Port())
    result = mgr.handle({"action": "search", "input": {"query": "q"}})
    assert result["count"] == 0


# ─── Browse/manual independence is untouched by any of the above ───────────

def test_manual_and_browse_unaffected_by_backend_gating(tmp_path):
    # A manual/browse call never constructs a search provider or reaches the
    # backend-eligibility gate, regardless of the current LLM backend or the
    # configured search engine's eligibility.
    agent = _agent(tmp_path, "claude-code")
    mgr = setup(agent, engines={"anthropic": {"search_service": MagicMock(spec=SearchService)}, "duckduckgo": {}}, browser_port=_Port())
    manual_result = mgr.handle({"action": "manual", "input": {}})
    assert manual_result.get("error_code") != "PROVIDER_BACKEND_INELIGIBLE"
    assert manual_result["current_setting"]["source"] == "not_applicable"
