"""Web full-result inline-vs-artifact delivery: settings/web.json, spill
envelopes for search and browse, Unicode/threshold boundaries, atomic
uniqueness, artifact readability via shell, write failure, and no outer
double-spill.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from lingtai.tools.browser.port import TransportResponse
from lingtai.tools.web_search import setup
from lingtai.tools.web_search._spill import spill_if_over_threshold
from lingtai.tools.web_search.settings import (
    DEFAULT_OUTPUT_MAX_CHARS,
    OutputSettingsSnapshot,
    read_output_settings,
)
from lingtai.kernel.tool_executor import ToolExecutor
from lingtai.kernel.tool_result_artifacts import PREVENTIVE_MAX_CHARS, is_spill_manifest


def _snapshot(max_chars: int) -> OutputSettingsSnapshot:
    return OutputSettingsSnapshot(max_chars, "default", "default", "test-digest")


class _OfficialHost:
    """Minimal registrar host used by Web's direct behavior tests."""

    def __init__(self, root: Path) -> None:
        self._working_dir = root
        self.service = SimpleNamespace(provider=None)
        self._official_tool_plugins = {}
        self._bound_plugins = {}

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

    def _record_official_tool_binding(self, declaration, plugin) -> None:
        self._bound_plugins[declaration.name] = plugin

    def _mount_official_tool(self, transaction) -> None:
        transaction.consume()
        self.tool_name = transaction.plugin.name
        self.schema = transaction.plugin.schema
        self.handler = transaction.plugin.handler
        transaction.mark_mounted(self)

    def _claim_official_tool(self, transaction) -> None:
        self._official_tool_plugins[transaction.declaration.name] = transaction.declaration


class _LargeSearch:
    """Returns a large finite result set, including synthesized (non-URL) hits."""

    def __init__(self, count: int, *, include_synthesized: bool = False) -> None:
        self._count = count
        self._include_synthesized = include_synthesized

    def search(self, query: str, max_results=None):
        assert max_results is None
        results = [
            {
                "title": f"Result {i}",
                "url": f"https://example.test/article-{i}",
                "snippet": "Lorem ipsum dolor sit amet.",
            }
            for i in range(self._count)
        ]
        if self._include_synthesized:
            results.append({"title": "Synthesized answer", "url": "", "snippet": "no url here"})
        return results


class _Port:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.requests: list[str] = []

    def resolve(self, hostname: str, *, timeout_s: float):
        return ["93.184.216.34"]

    def request(self, url: str, *, resolved, max_bytes: int, timeout_s: float):
        self.requests.append(url)
        return TransportResponse(200, {"content-type": "text/html"}, self.body, False, url)


def _read_web_json(agent) -> dict:
    path = agent._working_dir / "settings" / "web.json"
    return json.loads(path.read_text())


# --- settings/web.json shared-setting contract ---


def test_output_settings_missing_file_uses_default(tmp_path):
    agent = _OfficialHost(tmp_path)
    snapshot = read_output_settings(agent)
    assert snapshot.max_chars == DEFAULT_OUTPUT_MAX_CHARS == 50_000
    assert snapshot.source == "default"
    assert snapshot.error is None


def test_output_settings_valid_override_is_respected(tmp_path):
    agent = _OfficialHost(tmp_path)
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": 100}))
    snapshot = read_output_settings(agent)
    assert snapshot.max_chars == 100
    assert snapshot.source == "settings/web.json"
    assert snapshot.error is None


def test_output_settings_boundary_values_are_valid(tmp_path):
    agent = _OfficialHost(tmp_path)
    (tmp_path / "settings").mkdir()
    settings_path = tmp_path / "settings" / "web.json"
    for boundary in (1, 100_000):
        settings_path.write_text(json.dumps({"schema_version": 1, "max_chars": boundary}))
        snapshot = read_output_settings(agent)
        assert snapshot.max_chars == boundary
        assert snapshot.error is None


def test_output_settings_out_of_range_fails_loud_not_clamped(tmp_path):
    agent = _OfficialHost(tmp_path)
    (tmp_path / "settings").mkdir()
    settings_path = tmp_path / "settings" / "web.json"
    for bad in (0, -1, 100_001, 10**9):
        settings_path.write_text(json.dumps({"schema_version": 1, "max_chars": bad}))
        snapshot = read_output_settings(agent)
        assert snapshot.max_chars is None
        assert snapshot.error is not None


def test_output_settings_wrong_type_and_bool_are_rejected(tmp_path):
    agent = _OfficialHost(tmp_path)
    (tmp_path / "settings").mkdir()
    settings_path = tmp_path / "settings" / "web.json"
    for bad in (True, False, "50000", 50000.0, None, [50000]):
        settings_path.write_text(json.dumps({"schema_version": 1, "max_chars": bad}))
        snapshot = read_output_settings(agent)
        assert snapshot.error is not None, bad


def test_output_settings_unknown_and_missing_fields_are_rejected(tmp_path):
    agent = _OfficialHost(tmp_path)
    (tmp_path / "settings").mkdir()
    settings_path = tmp_path / "settings" / "web.json"
    for payload in (
        {"schema_version": 1, "max_chars": 5000, "extra": "x"},
        {"schema_version": 1},
        {"max_chars": 5000},
    ):
        settings_path.write_text(json.dumps(payload))
        snapshot = read_output_settings(agent)
        assert snapshot.error is not None, payload


def test_output_settings_search_and_browse_share_one_setting(tmp_path):
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=_Port(b"<p>x</p>"))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": 7}))
    search_result = manager.handle({"action": "search", "input": {"query": "q"}})
    browse_result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/page", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert search_result["current_setting"]["output_max_chars"]["value"] == 7
    assert browse_result["current_setting"]["output_max_chars"]["value"] == 7


def test_invalid_output_settings_fails_search_and_browse_before_side_effects(tmp_path):
    port = _Port(b"<p>x</p>")
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text("{not-json")

    search_result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert search_result["status"] == "failed"
    assert search_result["error_code"] == "WEB_OUTPUT_SETTINGS_INVALID"

    browse_result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/page", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert browse_result["status"] == "failed"
    assert browse_result["error_code"] == "WEB_OUTPUT_SETTINGS_INVALID"
    # No side effect: browse never fetched the page.
    assert port.requests == []


def test_manual_performs_zero_settings_io_including_web_json(tmp_path, monkeypatch):
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=_Port(b"<p>x</p>"))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text("{not-json")

    def fail_read_output_settings(*args, **kwargs):
        raise AssertionError("manual must never read settings/web.json")

    monkeypatch.setattr("lingtai.tools.web_search.read_output_settings", fail_read_output_settings)
    result = manager.handle({"action": "manual", "input": {}})
    # No AssertionError from fail_read_output_settings means manual never
    # called it; the diagnostic block confirms the same truthfully.
    assert result["action"] == "manual"
    assert result["current_setting"]["output_max_chars"]["source"] == "not_applicable"


# --- Search: inline vs artifact delivery ---


def test_search_small_result_set_delivered_inline(tmp_path):
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(3), browser_port=_Port(b"<p>x</p>"))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["status"] == "ok"
    assert result["delivery"] == "inline"
    assert len(result["results"]) == 3
    assert "file_path" not in result


def test_search_large_result_set_spills_to_json_artifact_with_no_preview(tmp_path):
    agent = _OfficialHost(tmp_path)
    # Each result is ~350+ chars; 400 results guarantees > 50_000 total chars.
    manager = setup(agent, search_service=_LargeSearch(400), browser_port=_Port(b"<p>x</p>"))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["status"] == "ok"
    assert result["delivery"] == "artifact"
    assert "results" not in result
    assert result["count"] == 400
    assert result["content_kind"] == "search_results"
    assert result["format"] == "json"
    assert result["content_scope"] == "provider_response"

    artifact_path = tmp_path / result["file_path"]
    assert artifact_path.is_file()
    content = artifact_path.read_text(encoding="utf-8")
    assert len(content) == result["content_chars"]
    assert hashlib.sha256(content.encode("utf-8")).hexdigest() == result["content_sha256"]
    parsed = json.loads(content)
    assert len(parsed) == 400
    assert "Use shell" in result["instruction"]
    assert "file.read" not in result["instruction"]
    assert "preview" not in result


def test_search_preserves_synthesized_non_url_results_and_long_fields(tmp_path):
    agent = _OfficialHost(tmp_path)
    manager = setup(
        agent,
        search_service=_LargeSearch(2, include_synthesized=True),
        browser_port=_Port(b"<p>x</p>"),
    )
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["status"] == "ok"
    assert result["count"] == 3
    synthesized = [r for r in result["results"] if r["url"] == ""]
    assert len(synthesized) == 1
    # A bounded citation-free narrative result is preserved with an explicit
    # link_ref: null — never a fabricated ref, but never an omitted key
    # either (CONTRACT.md "Provider ownership and routing").
    assert synthesized[0]["link_ref"] is None
    url_bearing = [r for r in result["results"] if r["url"]]
    assert all(r["link_ref"] is not None for r in url_bearing)


def test_search_unicode_character_count_is_code_points_not_utf8_bytes(tmp_path):
    class UnicodeSearch:
        def search(self, query, max_results=None):
            return [{"title": "中文标题" * 5000, "url": "https://example.test/z", "snippet": "s"}]

    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=UnicodeSearch(), browser_port=_Port(b"<p>x</p>"))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": 100}))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["delivery"] == "artifact"
    artifact_path = tmp_path / result["file_path"]
    content = artifact_path.read_text(encoding="utf-8")
    assert len(content) == result["content_chars"]
    # Sanity: multi-byte UTF-8 means byte length exceeds character length.
    assert len(content.encode("utf-8")) > len(content)


def test_search_threshold_boundary_off_by_one(tmp_path):
    class FixedSearch:
        def search(self, query, max_results=None):
            return [{"title": "x" * 10, "url": "https://example.test/a", "snippet": ""}]

    # Compute the exact serialized length for a 1-result payload, then probe
    # max_chars at that exact boundary from both sides.
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=FixedSearch(), browser_port=_Port(b"<p>x</p>"))
    probe_result = manager.handle({"action": "search", "input": {"query": "q"}})
    exact_chars = probe_result["content_chars"]

    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": exact_chars}))
    at_boundary = manager.handle({"action": "search", "input": {"query": "q"}})
    assert at_boundary["delivery"] == "inline"

    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": exact_chars - 1}))
    over_boundary = manager.handle({"action": "search", "input": {"query": "q"}})
    assert over_boundary["delivery"] == "artifact"


def test_search_per_engine_adapters_receive_no_count_cap(tmp_path):
    calls = []

    class RecordingSearch:
        def search(self, query, max_results=None):
            calls.append(max_results)
            return [{"title": "t", "url": "https://example.test/a", "snippet": "s"}]

    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=RecordingSearch(), browser_port=_Port(b"<p>x</p>"))
    manager.handle({"action": "search", "input": {"query": "q"}})
    assert calls == [None]


def test_search_spilled_openai_fallback_preserves_comment_and_failure_class(tmp_path):
    """The one OpenAI->DuckDuckGo runtime fallback must stay *informed* when
    its success result spills: CONTRACT.md's fallback section promises a
    top-level ``comment`` and bounded ``openai_failure_class`` with no spill
    carve-out, so both must survive in the spilled envelope exactly as they
    do inline, alongside ``engine``/``actual_engine`` and the complete
    artifact metadata."""
    from unittest.mock import MagicMock, patch

    from lingtai.services.websearch import SearchResult, SearchService
    from lingtai.services.websearch.openai import OpenAISearchError

    agent = _OfficialHost(tmp_path)
    failing_service = MagicMock(spec=SearchService)
    failing_service.search.side_effect = OpenAISearchError("Timeout")
    manager = setup(agent, engines={"openai": {"search_service": failing_service}}, browser_port=_Port(b"<p>x</p>"))
    ddg_results = [
        SearchResult(title=f"Fallback {i}", url=f"https://example.test/f-{i}", snippet="s")
        for i in range(3)
    ]

    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.return_value = ddg_results
        inline = manager.handle({"action": "search", "input": {"query": "q"}})
    assert inline["status"] == "ok"
    assert inline["delivery"] == "inline"
    assert "OpenAI" in inline["comment"] and "DuckDuckGo" in inline["comment"]
    assert inline["openai_failure_class"] == "Timeout"

    # Force the same fallback success to spill through a contractually valid
    # operator setting (settings/web.json max_chars may be as low as 1).
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": 1}))
    with patch("lingtai.services.websearch.duckduckgo.DuckDuckGoSearchService") as mock_ddg_cls:
        mock_ddg_cls.return_value.search.return_value = ddg_results
        spilled = manager.handle({"action": "search", "input": {"query": "q"}})

    assert spilled["status"] == "ok"
    assert spilled["delivery"] == "artifact"
    # Informed-substitution provenance survives the spill, identical to inline.
    assert spilled["comment"] == inline["comment"]
    assert spilled["openai_failure_class"] == "Timeout"
    assert spilled["engine"] == "openai"
    assert spilled["actual_engine"] == "duckduckgo"
    # Complete artifact metadata; no inline results, no preview.
    assert spilled["count"] == 3
    assert "results" not in spilled and "preview" not in spilled
    artifact_path = tmp_path / spilled["file_path"]
    content = artifact_path.read_text(encoding="utf-8")
    assert len(content) == spilled["content_chars"]
    assert hashlib.sha256(content.encode("utf-8")).hexdigest() == spilled["content_sha256"]
    assert spilled["content_kind"] == "search_results"
    assert spilled["format"] == "json"
    # The failure-only DDG class never appears on a success envelope.
    assert "duckduckgo_failure_class" not in inline
    assert "duckduckgo_failure_class" not in spilled


def test_search_non_fallback_spill_carries_no_fallback_fields(tmp_path):
    """A plain (non-fallback) spilled search must not grow the fallback-only
    ``comment``/``openai_failure_class`` fields."""
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(400), browser_port=_Port(b"<p>x</p>"))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["delivery"] == "artifact"
    assert "comment" not in result
    assert "openai_failure_class" not in result


# --- Browse: inline vs artifact delivery ---


def test_browse_small_page_delivered_inline_complete(tmp_path):
    agent = _OfficialHost(tmp_path)
    port = _Port(b"<html><body><p>short page text</p></body></html>")
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/page", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert result["status"] == "ok"
    assert result["delivery"] == "inline"
    assert result["partial"] is False
    assert result["next_cursor"] is None
    assert "file_path" not in result


def test_browse_large_page_spills_to_text_artifact_with_no_preview(tmp_path):
    paragraphs = "".join(f"<p>{'word ' * 50} paragraph {i}</p>" for i in range(400))
    body = f"<html><body>{paragraphs}</body></html>".encode()
    agent = _OfficialHost(tmp_path)
    port = _Port(body)
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/big", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert result["status"] == "ok"
    assert result["delivery"] == "artifact"
    assert "blocks" not in result
    assert "partial" not in result
    assert "next_cursor" not in result
    assert result["content_kind"] == "page_text"
    assert result["format"] == "text"
    assert result["content_scope"] == "fetched_static_document"

    artifact_path = tmp_path / result["file_path"]
    assert artifact_path.is_file()
    content = artifact_path.read_text(encoding="utf-8")
    assert len(content) == result["content_chars"]
    assert hashlib.sha256(content.encode("utf-8")).hexdigest() == result["content_sha256"]
    assert "preview" not in result
    assert "Use shell" in result["instruction"]
    assert "file.read" not in result["instruction"]


def test_browse_per_call_max_chars_overrides_delivery_threshold(tmp_path):
    agent = _OfficialHost(tmp_path)
    port = _Port(b"<html><body><p>twenty-five characters!!</p></body></html>")
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/page", "link_ref": None, "cursor": None, "extract": None, "max_chars": 3},
    })
    assert result["status"] == "ok"
    assert result["delivery"] == "artifact"
    assert result["current_setting"]["output_max_chars"]["value"] == 3
    assert result["current_setting"]["output_max_chars"]["source"] == "call_override"


def test_browse_null_max_chars_uses_shared_setting(tmp_path):
    agent = _OfficialHost(tmp_path)
    port = _Port(b"<html><body><p>content</p></body></html>")
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": 12345}))
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/page", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert result["current_setting"]["output_max_chars"]["value"] == 12345
    assert result["current_setting"]["output_max_chars"]["source"] == "settings/web.json"


# --- Browse: threshold decision must measure the structured inline
# serialization, not the compact joined-text file representation
# (Blocker A). A production page with many small blocks can have joined
# plain text well under the 50_000-char default threshold while its
# structured `blocks` JSON — the content actually returned inline — is
# several times larger, large enough to also cross the unrelated generic
# 200_000-char preventive-spill ceiling if left undetected. ---


def _many_small_blocks_page(count: int) -> bytes:
    paragraphs = "".join(f"<p>{i:010d}</p>" for i in range(count))
    return f"<html><body>{paragraphs}</body></html>".encode()


def test_browse_production_4500_small_blocks_spills_webs_own_complete_artifact(tmp_path):
    """Reproduces the exact production bug: 4500 ten-character blocks join to
    45_000 chars (under the 50_000 default threshold) but the structured
    `blocks` array that would actually be returned inline serializes to
    270_000 chars — over both the 50_000 web threshold and the unrelated
    200_000 generic preventive-spill ceiling. Web's own no-preview artifact
    must win: no generic lossy preview may ever be substituted."""
    agent = _OfficialHost(tmp_path)
    port = _Port(_many_small_blocks_page(4500))
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/many-blocks", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert result["status"] == "ok"
    # The bug this reproduces: joined-text-only measurement would have
    # returned "inline" here (45_000 <= 50_000). The fix must spill.
    assert result["delivery"] == "artifact"
    assert result["delivery_decision_chars"] == 270_000
    assert result["delivery_decision_basis"] == "structured_blocks"
    # content_chars/content_sha256 describe the file actually written — the
    # smaller joined-text form (45_000 chars) — never the structured
    # decision length, and never implying the file itself exceeded 50_000.
    assert result["content_chars"] == 45_000
    assert result["content_chars"] != result["delivery_decision_chars"]

    # No generic lossy preview anywhere: this is web's own complete,
    # no-preview artifact, recognized by the kernel's own marker.
    assert "preview" not in result
    assert "blocks" not in result
    assert is_spill_manifest(result) is True

    artifact_path = tmp_path / result["file_path"]
    assert artifact_path.is_file()
    file_content = artifact_path.read_text(encoding="utf-8")
    assert len(file_content) == result["content_chars"] == 45_000
    assert hashlib.sha256(file_content.encode("utf-8")).hexdigest() == result["content_sha256"]
    # The file holds the complete extracted document: every block's text is
    # present and in order, nothing trimmed.
    assert file_content == "".join(f"{i:010d}" for i in range(4500))


def test_browse_production_4500_small_blocks_never_substitutes_generic_preview(tmp_path):
    """Directly proves the generic preventive spill never re-triggers on this
    result even though its structured decision length (270_000) exceeds the
    generic 200_000-char ceiling — the web artifact is already spilled and
    explicitly marked before the generic mechanism ever sees it."""
    from lingtai.kernel.tool_result_artifacts import spill_oversized_result

    agent = _OfficialHost(tmp_path)
    port = _Port(_many_small_blocks_page(4500))
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/many-blocks", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert result["delivery"] == "artifact"

    capped = spill_oversized_result(
        result, max_chars=PREVENTIVE_MAX_CHARS, tool_name="web",
        tool_call_id="call-1", working_dir=tmp_path,
    )
    assert capped is result  # unchanged: recognized as already spilled
    assert "preview" not in capped


def test_browse_small_page_control_stays_inline_when_structured_form_also_fits(tmp_path):
    """Control: a genuinely small page (few blocks) stays inline under both
    the joined-text and structured-blocks measurements — proves the fix
    does not over-trigger spilling for ordinary small pages."""
    agent = _OfficialHost(tmp_path)
    port = _Port(_many_small_blocks_page(5))
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)
    result = manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/tiny", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert result["status"] == "ok"
    assert result["delivery"] == "inline"
    assert len(result["blocks"]) == 5
    assert result["partial"] is False
    assert result["next_cursor"] is None
    # content_chars reflects the structured serialization actually returned.
    assert result["content_chars"] == len(json.dumps(result["blocks"], ensure_ascii=False))


def test_browse_structured_decision_chars_exact_boundary(tmp_path):
    """Exact boundary on the structured-blocks decision length: at the
    threshold stays inline; one character over spills — proving the decision
    is made against the structured serialization, not the joined text."""
    agent = _OfficialHost(tmp_path)

    # 60 blocks: structured JSON is comfortably below 50_000 (joined text is
    # tiny too) — establish a working count near the boundary by binary
    # search on the structured length computed directly.
    import json as _json
    from lingtai.tools.browser.extractor import extract_html

    def _structured_len(count: int) -> int:
        html = _many_small_blocks_page(count)
        doc = extract_html(html, base_url="https://public.example/probe")
        structured = [{"id": b.id, "kind": b.kind, "text": b.text} for b in doc.blocks]
        return len(_json.dumps(structured, ensure_ascii=False))

    # Find the smallest block count whose structured length exceeds 50_000.
    count = 1
    while _structured_len(count) <= 50_000:
        count += 50
    over_count = count

    (tmp_path / "settings").mkdir()
    exact_over_chars = _structured_len(over_count)
    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": exact_over_chars}))

    at_boundary_port = _Port(_many_small_blocks_page(over_count))
    at_boundary_manager = setup(_OfficialHost(tmp_path), search_service=_LargeSearch(1), browser_port=at_boundary_port)
    at_boundary = at_boundary_manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/at-boundary", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert at_boundary["delivery"] == "inline"
    assert at_boundary["content_chars"] == exact_over_chars

    (tmp_path / "settings" / "web.json").write_text(json.dumps({"schema_version": 1, "max_chars": exact_over_chars - 1}))
    over_boundary = at_boundary_manager.handle({
        "action": "browse",
        "input": {"url": "https://public.example/over-boundary", "link_ref": None, "cursor": None, "extract": None, "max_chars": None},
    })
    assert over_boundary["delivery"] == "artifact"
    assert over_boundary["delivery_decision_chars"] == exact_over_chars


# --- Browse: snapshot eviction between engine success and delivery must
# fail loud, never fall back to a partial/first-page success (Blocker B). ---


def test_browse_snapshot_eviction_before_delivery_fails_loud_not_partial(tmp_path):
    """Deterministically evicts the snapshot the engine just built, between
    its fetch success and WebManager's delivery decision, and proves the
    result is a typed, loud failure — never a partial body, never a
    next_cursor, never a degraded "best effort" success."""
    agent = _OfficialHost(tmp_path)
    port = _Port(b"<html><body><p>content that would otherwise deliver fine</p></body></html>")
    manager = setup(agent, search_service=_LargeSearch(1), browser_port=port)

    # Drive the engine directly to get a real fetch success, exactly what
    # _dispatch_browse would have received before calling _deliver_browse.
    engine_result = manager.browser_engine.handle({
        "url": "https://public.example/page", "extract": "article", "max_chars": 12000,
    })
    assert engine_result["status"] == "ok"
    snapshot_id = engine_result["snapshot_id"]
    assert manager.browser_engine.snapshots.get(snapshot_id) is not None

    # Deterministically evict: remove the snapshot from the store's backing
    # dict directly, simulating the LRU having evicted it under concurrent
    # pressure between the engine's success and this delivery decision.
    del manager.browser_engine.snapshots._items[snapshot_id]
    assert manager.browser_engine.snapshots.get(snapshot_id) is None

    engine_result["action"] = "browse"
    engine_result["current_setting"] = manager._browse_diagnostic(_snapshot(DEFAULT_OUTPUT_MAX_CHARS))
    result = manager._deliver_browse(engine_result, _snapshot(DEFAULT_OUTPUT_MAX_CHARS))

    assert result["status"] == "failed"
    assert result["error_code"] == "BROWSE_SNAPSHOT_UNAVAILABLE"
    # No partial-success body may escape: no blocks, no partial flag implying
    # success, no continuation cursor.
    assert "blocks" not in result
    assert "partial" not in result
    assert "next_cursor" not in result
    assert "delivery" not in result
    assert result["current_setting"] is not None
    assert result["snapshot_id"] == snapshot_id


# --- Atomicity, write failure, direct spill helper unit coverage ---


def test_spill_helper_writes_under_canonical_tool_results_dir(tmp_path):
    result = spill_if_over_threshold(
        content="x" * 100, output_setting=_snapshot(1), working_dir=tmp_path, action="search",
        content_scope="provider_response", content_kind="search_results", format="json",
    )
    assert result["file_path"].startswith("tmp/tool-results/")
    assert (tmp_path / result["file_path"]).is_file()


def test_spill_helper_atomic_unique_filenames_across_rapid_calls(tmp_path):
    first = spill_if_over_threshold(
        content="x" * 100, output_setting=_snapshot(1), working_dir=tmp_path, action="search",
        content_scope="provider_response", content_kind="search_results", format="json",
    )
    second = spill_if_over_threshold(
        content="x" * 100, output_setting=_snapshot(1), working_dir=tmp_path, action="search",
        content_scope="provider_response", content_kind="search_results", format="json",
    )
    assert first["file_path"] != second["file_path"]
    assert (tmp_path / first["file_path"]).is_file()
    assert (tmp_path / second["file_path"]).is_file()


def test_spill_helper_inline_when_at_or_under_threshold():
    assert spill_if_over_threshold(
        content="x" * 10, output_setting=_snapshot(10), working_dir=Path("/tmp"), action="search",
        content_scope="provider_response", content_kind="search_results", format="json",
    ) is None


def test_spill_helper_write_failure_is_explicit_not_lossy_fallback(tmp_path, monkeypatch):
    import lingtai.tools.web_search._spill as spill_mod

    def fail_write(*args, **kwargs):
        return None, None, "OSError: disk full"

    monkeypatch.setattr(spill_mod, "write_artifact_file", fail_write)
    result = spill_if_over_threshold(
        content="x" * 100, output_setting=_snapshot(1), working_dir=tmp_path, action="search",
        content_scope="provider_response", content_kind="search_results", format="json",
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "ARTIFACT_WRITE_FAILED"
    assert "content" not in result


def test_spill_helper_envelope_carries_output_setting_source_revision_hash(tmp_path):
    snapshot = OutputSettingsSnapshot(5, "settings/web.json", "abc123", "deadbeef" * 4)
    result = spill_if_over_threshold(
        content="x" * 100, output_setting=snapshot, working_dir=tmp_path, action="search",
        content_scope="provider_response", content_kind="search_results", format="json",
    )
    assert result["output_setting_source"] == "settings/web.json"
    assert result["output_setting_revision"] == "abc123"
    assert result["output_setting_hash"] == "deadbeef" * 4


def test_output_settings_default_case_has_deterministic_revision_and_hash(tmp_path):
    agent = _OfficialHost(tmp_path)
    first = read_output_settings(agent)
    second = read_output_settings(agent)
    assert first.source == "default"
    assert first.revision == second.revision
    assert first.digest is not None
    assert first.digest == second.digest


def test_search_artifact_write_failure_surfaces_as_typed_failure(tmp_path, monkeypatch):
    import lingtai.tools.web_search as ws_mod

    def fail_spill(**kwargs):
        return {"status": "failed", "message": "boom"}

    monkeypatch.setattr(ws_mod, "spill_if_over_threshold", fail_spill)
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(400), browser_port=_Port(b"<p>x</p>"))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["status"] == "failed"
    assert result["error_code"] == "ARTIFACT_WRITE_FAILED"


# --- No outer double-spill: the web artifact envelope carries an explicit,
# namespaced marker (WEB_ARTIFACT_MARKER) that the kernel's is_spill_manifest
# recognizes on its own terms, so the generic preventive spill treats an
# already-built web artifact as "already spilled" and never re-spills it —
# this is a recognized-manifest guarantee, not an incidental "the envelope
# happens to be small" property. Both are proven below: the explicit marker
# recognition directly, and the same guarantee holding even when the
# envelope is deliberately padded past the 200_000-char preventive ceiling.


def test_web_artifact_envelope_is_recognized_by_is_spill_manifest(tmp_path):
    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(400), browser_port=_Port(b"<p>x</p>"))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["delivery"] == "artifact"
    assert is_spill_manifest(result) is True


def test_generic_preventive_spill_never_rewrites_an_already_marked_web_artifact(tmp_path):
    """Directly exercise kernel spill_oversized_result on a web artifact result
    padded well past the 200_000-char preventive ceiling, proving recognition
    — not smallness — is what prevents double-spill."""
    from lingtai.kernel.tool_result_artifacts import spill_oversized_result

    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(400), browser_port=_Port(b"<p>x</p>"))
    result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert result["delivery"] == "artifact"
    # Pad the envelope itself well past the generic preventive ceiling with a
    # large advisory-shaped field, so a size-based recognition would have
    # re-spilled it; only the explicit marker can prevent that.
    result["_padding_for_test"] = "z" * (PREVENTIVE_MAX_CHARS + 50_000)
    assert len(json.dumps(result)) > PREVENTIVE_MAX_CHARS

    capped = spill_oversized_result(
        result, max_chars=PREVENTIVE_MAX_CHARS, tool_name="web",
        tool_call_id="call-1", working_dir=tmp_path,
    )
    assert capped is result  # unchanged: recognized as already spilled


def test_tool_executor_does_not_double_spill_a_large_web_artifact_result(tmp_path):
    """End-to-end through the real ToolExecutor: a web search artifact result,
    padded past the preventive ceiling, must reach the wire unchanged — not
    replaced by a second, generic spill manifest."""
    from lingtai.kernel.llm.base import ToolCall
    from lingtai.kernel.loop_guard import LoopGuard

    agent = _OfficialHost(tmp_path)
    manager = setup(agent, search_service=_LargeSearch(400), browser_port=_Port(b"<p>x</p>"))
    web_result = manager.handle({"action": "search", "input": {"query": "q"}})
    assert web_result["delivery"] == "artifact"
    web_result["_padding_for_test"] = "z" * (PREVENTIVE_MAX_CHARS + 50_000)

    def make_tool_result_fn(name, result, **kw):
        return {"role": "tool", "name": name, "content": result, **kw}

    executor = ToolExecutor(
        dispatch_fn=lambda tc: web_result,
        make_tool_result_fn=make_tool_result_fn,
        guard=LoopGuard(),
        known_tools={"web"},
        working_dir=tmp_path,
    )
    results, _, _ = executor.execute(
        [ToolCall(name="web", args={"action": "search", "input": {"query": "q"}}, id="w1")]
    )
    wire_content = results[0]["content"]
    assert is_spill_manifest(wire_content)
    assert wire_content.get("delivery") == "artifact"
    assert wire_content.get("file_path") == web_result["file_path"]
    # The generic manifest's own distinguishing fields must be absent — this
    # is still web's own envelope, not a second, generic replacement.
    assert "cap_chars" not in wire_content
    assert "spill_path" not in wire_content


def test_artifact_readable_end_to_end_via_shell_tool(tmp_path):
    """The spilled artifact is a plain workdir-relative file that ``shell``
    (the one durable filesystem surface) reads back in full."""
    from lingtai.agent import Agent
    from tests._service_helpers import make_gemini_mock_service

    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="web-artifact-shell-read",
        working_dir=tmp_path,
        capabilities={
            "web": {"search_service": _LargeSearch(400), "browser_port": _Port(b"<p>x</p>")},
            "shell": {"yolo": True},
        },
        disable=["knowledge", "skills", "avatar", "daemon", "mcp", "vision"],
    )
    try:
        result = agent._tool_handlers["web"]({"action": "search", "input": {"query": "q"}})
        assert result["delivery"] == "artifact"
        read_result = agent._tool_handlers["shell"]({
            "action": "run",
            "input": {
                "command": f"wc -c < {result['file_path']}",
                "timeout": None,
                "working_dir": None,
                "async": None,
                "reminder": None,
            },
            "reasoning": "read the spilled web artifact",
        })
        assert read_result["status"] == "ok"
        assert int(read_result["stdout"].strip()) == len(
            (tmp_path / result["file_path"]).read_bytes()
        )
        parsed = json.loads((tmp_path / result["file_path"]).read_text())
        assert len(parsed) == 400
    finally:
        agent.stop(timeout=1.0)
