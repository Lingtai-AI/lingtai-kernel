"""Focused proofs for Web's read-only five-field settings owner."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from lingtai.services.websearch import SearchResult
from lingtai.tools.web_search import DECLARATION, WebManager, _EngineSpec, setup
from lingtai.tools.web_search.settings import (
    ANTHROPIC_API_KEY,
    API_KEY,
    DEFAULT_ENGINE_NAMES,
    ENGINES_KEY,
    GEMINI_API_KEY,
    MODEL_KEY,
    OPENAI_API_KEY,
    OUTPUT_MAX_CHARS_KEY,
    PROVIDER_KEY,
    SEARCH_ENGINE_KEY,
    WEB_ENGINE_ENV,
    WEB_MAX_CHARS_ENV,
)

_WEB_ENV = (
    WEB_ENGINE_ENV,
    WEB_MAX_CHARS_ENV,
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
)
_ROW_KEYS = (
    PROVIDER_KEY,
    MODEL_KEY,
    API_KEY,
    ENGINES_KEY,
    SEARCH_ENGINE_KEY,
    OUTPUT_MAX_CHARS_KEY,
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
)
_COMMENTS = {
    PROVIDER_KEY: "web-manual#provider",
    MODEL_KEY: "web-manual#model",
    API_KEY: "web-manual#api-key",
    ENGINES_KEY: "web-manual#engines",
    SEARCH_ENGINE_KEY: "web-manual#search-engine",
    OUTPUT_MAX_CHARS_KEY: "web-manual#output-max-chars",
    OPENAI_API_KEY: "web-manual#openai-api-key",
    ANTHROPIC_API_KEY: "web-manual#anthropic-api-key",
    GEMINI_API_KEY: "web-manual#gemini-api-key",
}
_SENSITIVE_KEYS = (
    API_KEY,
    OPENAI_API_KEY,
    ANTHROPIC_API_KEY,
    GEMINI_API_KEY,
)


@pytest.fixture(autouse=True)
def _isolated_web_environment(monkeypatch):
    for name in _WEB_ENV:
        monkeypatch.delenv(name, raising=False)


class _Workdir:
    def __init__(self, path: Path) -> None:
        self.path = path


class _ProviderIdentity:
    provider = "openai"


class _BrowserPort:
    def resolve(self, hostname: str, *, timeout_s: float):
        return ["93.184.216.34"]

    def request(self, url: str, *, resolved, max_bytes: int, timeout_s: float):
        raise AssertionError("browse is outside these settings/search checks")


class _Search:
    def __init__(self, label: str) -> None:
        self.label = label
        self.calls: list[str] = []

    def search(self, query: str, max_results: int | None = None):
        self.calls.append(query)
        return [
            SearchResult(
                title=self.label,
                url=f"https://{self.label}.example/result",
                snippet="complete",
            )
        ]


class _OfficialHost:
    """Small host exercising the real declaration/bind registrar path."""

    def __init__(self, root: Path) -> None:
        self._working_dir = root
        self.service = SimpleNamespace(provider="openai")
        self._official_tool_plugins = {}

    @property
    def working_dir(self) -> Path:
        return self._working_dir

    @property
    def official_tool_plugins(self):
        return self._official_tool_plugins

    def update_system_prompt(self, *_args, **_kwargs) -> None:
        pass

    def _authorize_official_tool_declaration(self, _declaration) -> None:
        pass

    def _record_official_tool_binding(self, _declaration, _plugin) -> None:
        pass

    def _mount_official_tool(self, transaction) -> None:
        transaction.consume()
        self.handler = transaction.plugin.handler
        transaction.mark_mounted(self)

    def _claim_official_tool(self, transaction) -> None:
        self._official_tool_plugins[transaction.declaration.name] = (
            transaction.declaration
        )


def _call(manager: WebManager, action: str, action_input: object) -> dict:
    return manager.handle(
        {"action": action, "input": action_input, "reasoning": "focused proof"}
    )


def _manager(
    tmp_path: Path,
    *,
    include_openai: bool = False,
) -> tuple[WebManager, _Search, _Search | None]:
    duckduckgo = _Search("duckduckgo")
    specs = {
        "duckduckgo": _EngineSpec(
            "duckduckgo", provider="duckduckgo", service=duckduckgo
        )
    }
    openai = None
    if include_openai:
        openai = _Search("openai")
        specs["openai"] = _EngineSpec(
            "openai", provider="openai", service=openai
        )
    manager = WebManager(
        _Workdir(tmp_path),
        _ProviderIdentity(),
        _BrowserPort(),
        specs=specs,
        default_engine="duckduckgo",
        default_source="operator_default",
    )
    return manager, duckduckgo, openai


def _settings(manager: WebManager) -> list[dict]:
    result = _call(manager, "settings", {})
    assert set(result) == {"settings"}
    return result["settings"]


def test_web_opts_in_with_exact_five_field_rows_and_manual_targets(tmp_path):
    manager = setup(_OfficialHost(tmp_path), browser_port=_BrowserPort())

    assert DECLARATION.settings is True
    assert DECLARATION.public_actions == ("search", "browse", "settings", "manual")
    assert manager._family.child_names == DECLARATION.public_actions

    rows = _settings(manager)
    assert tuple(row["key"] for row in rows) == _ROW_KEYS
    assert all(
        tuple(row) == ("key", "current", "default", "configurable", "comment")
        for row in rows
    )
    by_key = {row["key"]: row for row in rows}
    assert by_key[PROVIDER_KEY]["current"] == "automatic"
    assert by_key[PROVIDER_KEY]["default"] == "automatic"
    assert by_key[MODEL_KEY]["current"] == "provider-default"
    assert by_key[MODEL_KEY]["default"] == "provider-default"
    assert by_key[ENGINES_KEY]["current"] == list(DEFAULT_ENGINE_NAMES)
    assert by_key[ENGINES_KEY]["default"] == list(DEFAULT_ENGINE_NAMES)
    assert by_key[SEARCH_ENGINE_KEY]["current"] == "duckduckgo"
    assert by_key[SEARCH_ENGINE_KEY]["default"] == "duckduckgo"
    assert by_key[OUTPUT_MAX_CHARS_KEY]["current"] == 50_000
    assert by_key[OUTPUT_MAX_CHARS_KEY]["default"] == 50_000
    assert all(row["configurable"] is True for row in rows)
    assert {row["key"]: row["comment"] for row in rows} == _COMMENTS
    for key in _SENSITIVE_KEYS:
        assert by_key[key]["current"] == "<redacted>"
        assert by_key[key]["default"] == "<redacted>"

    manual = (
        Path(__file__).parents[1] / "src/lingtai/tools/web_search/manual/SKILL.md"
    ).read_text(encoding="utf-8")
    for pointer in _COMMENTS.values():
        heading = pointer.partition("#")[2]
        assert f"#### {heading}\n" in manual


def test_env_precedence_changes_current_not_default_and_search_still_works(
    tmp_path, monkeypatch
):
    settings_dir = tmp_path / "settings"
    settings_dir.mkdir()
    search_path = settings_dir / "web.search.json"
    output_path = settings_dir / "web.json"
    search_path.write_text(
        json.dumps({"schema_version": 1, "engine": "duckduckgo"}),
        encoding="utf-8",
    )
    output_path.write_text(
        json.dumps({"schema_version": 1, "max_chars": 1_000}),
        encoding="utf-8",
    )
    before = (search_path.read_bytes(), output_path.read_bytes())
    manager, duckduckgo, openai = _manager(tmp_path, include_openai=True)

    initial = {row["key"]: row for row in _settings(manager)}
    assert initial[SEARCH_ENGINE_KEY]["current"] == "duckduckgo"
    assert initial[OUTPUT_MAX_CHARS_KEY]["current"] == 1_000

    monkeypatch.setenv(WEB_ENGINE_ENV, "openai")
    monkeypatch.setenv(WEB_MAX_CHARS_ENV, "999")

    by_key = {row["key"]: row for row in _settings(manager)}
    assert by_key[PROVIDER_KEY]["current"] is None
    assert by_key[MODEL_KEY]["current"] is None
    assert by_key[SEARCH_ENGINE_KEY]["current"] == "openai"
    assert by_key[SEARCH_ENGINE_KEY]["default"] == "duckduckgo"
    assert by_key[OUTPUT_MAX_CHARS_KEY]["current"] == 999
    assert by_key[OUTPUT_MAX_CHARS_KEY]["default"] == 50_000

    result = _call(manager, "search", {"query": "unchanged basic action"})
    assert result["status"] == "ok"
    assert result["engine"] == "openai"
    assert result["current_setting"]["source"] == "environment"
    assert result["current_setting"]["output_max_chars"]["source"] == "environment"
    assert openai is not None and openai.calls == ["unchanged basic action"]
    assert duckduckgo.calls == []
    assert (search_path.read_bytes(), output_path.read_bytes()) == before


def test_flat_launcher_snapshots_are_redacted_and_show_has_no_mutation_api(
    tmp_path, monkeypatch
):
    secret = "sk-never-project-this"
    manager = setup(
        _OfficialHost(tmp_path),
        provider="openai",
        model="web-model",
        api_key=secret,
        browser_port=_BrowserPort(),
    )

    env_before = {name: os.environ.get(name) for name in _WEB_ENV}
    result = _call(manager, "settings", {})
    serialized = json.dumps(result, sort_keys=True)
    assert secret not in serialized
    by_key = {row["key"]: row for row in result["settings"]}
    assert by_key[PROVIDER_KEY]["current"] == "openai"
    assert by_key[MODEL_KEY]["current"] == "web-model"
    for key in _SENSITIVE_KEYS:
        assert by_key[key]["current"] == "<redacted>"
        assert by_key[key]["default"] == "<redacted>"

    refused = _call(manager, "settings", {"set": OUTPUT_MAX_CHARS_KEY, "value": 1})
    assert refused["status"] == "failed"
    assert refused["error_code"] == "INVALID_ARGUMENT"
    assert not (tmp_path / "settings").exists()
    assert {name: os.environ.get(name) for name in _WEB_ENV} == env_before


def test_credential_route_stays_active_after_service_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "route-sentinel")
    manager = WebManager(
        _Workdir(tmp_path),
        _ProviderIdentity(),
        _BrowserPort(),
        specs={
            "owner-openai": _EngineSpec(
                "owner-openai", provider="openai", api_key_env="OPENAI_API_KEY"
            )
        },
        default_engine="owner-openai",
        default_source="operator_default",
    )
    assert manager._credential_configured("openai") is True
    manager._services["owner-openai"] = object()
    monkeypatch.delenv("OPENAI_API_KEY")
    assert manager._credential_configured("openai") is True


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ("web.search.json", "{not-json"),
        ("web.json", '{"schema_version":1,"max_chars":0}'),
        (WEB_ENGINE_ENV, "not-admitted"),
        (WEB_MAX_CHARS_ENV, "0"),
    ],
)
def test_unavailable_current_returns_one_fixed_failure_without_rows(
    tmp_path, monkeypatch, source, value
):
    if source.startswith("LINGTAI_"):
        monkeypatch.setenv(source, value)
    else:
        settings_dir = tmp_path / "settings"
        settings_dir.mkdir()
        (settings_dir / source).write_text(value, encoding="utf-8")
    manager, _duckduckgo, _openai = _manager(tmp_path)

    assert _call(manager, "settings", {}) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_web_manual_keeps_resolvable_operation_links():
    import re
    root = Path(__file__).parents[1] / "src/lingtai/tools/web_search/manual"
    text = (root / "SKILL.md").read_text()
    operation = (root / "reference/operation-contract.md").read_text()
    headings = {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-")
                for h in re.findall(r"^#{1,6} (.+)$", operation, re.M)}
    for anchor in re.findall(r"operation-contract.md#([a-z-]+)", text):
        assert anchor in headings


def test_web_guidance_distinguishes_hot_show_and_cached_services():
    root = Path(__file__).parents[1] / "src/lingtai/tools/web_search/manual"
    operation = (root / "reference/operation-contract.md").read_text()
    for phrase in ("LINGTAI_WEB_PROVIDER", "LINGTAI_WEB_MODEL", "cached service",
                   "does not change SHOW", "before applying", "SETTINGS_UNAVAILABLE"):
        assert phrase in operation


def test_web_legacy_helper_limitations_are_explicit():
    root = Path(__file__).parents[1] / "src/lingtai/tools/web_search/manual"
    maintenance = (root / "reference/maintenance-bundles/SKILL.md").read_text()
    for phrase in ("AttributeError", "extraction-pipeline.json", "not valid JSON",
                   "WEB_BROWSING_CACHE_DIR", "clear_cache", "no automatic purge",
                   "--fallback", "Jina", "preview", "authorization"):
        assert phrase.lower() in " ".join(maintenance.lower().split())
