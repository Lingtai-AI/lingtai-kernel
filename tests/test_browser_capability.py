"""Hermetic browse tests through the canonical unified ``web`` capability."""
from __future__ import annotations

from dataclasses import dataclass

from lingtai.agent import Agent
from lingtai.kernel.config import TOOL_PROSE_SECTION_ENABLED_ENV
from lingtai.tools.browser.core import BrowserEngine
from lingtai.tools.browser.port import ResolvedTarget, TransportResponse
from lingtai.tools.web_search import get_schema
from tests._service_helpers import make_gemini_mock_service


@dataclass
class FakeBrowserPort:
    body_by_url: dict[str, bytes]

    def __post_init__(self):
        self.calls: list[tuple[str, ResolvedTarget]] = []

    def resolve(self, hostname: str, *, timeout_s: float):
        return ("93.184.216.34",)

    def request(self, url: str, *, resolved: ResolvedTarget, max_bytes: int, timeout_s: float):
        self.calls.append((url, resolved))
        body = self.body_by_url.get(
            url,
            b"<html><body><p>linked page</p></body></html>",
        )
        return TransportResponse(
            status=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=body,
            truncated=False,
            url=url,
        )


class FakeSearch:
    def __init__(self):
        self.queries: list[str] = []

    def search(self, query: str, max_results: int | None = None):
        self.queries.append(query)
        return [{
            "title": "Provider-shaped result",
            "url": "https://public.example/page",
            "snippet": "Hermetic search evidence",
        }]


def test_web_browse_vertical_slice(tmp_path):
    port = FakeBrowserPort(
        {
            "https://public.example/page": (
                b"<html><head><title>Example</title></head><body>"
                b"<h1>Heading</h1><p>First paragraph with enough text.</p>"
                b"<p><a href='/next'>Next page</a></p></body></html>"
            ),
            "https://public.example/next": b"<html><body><p>Next page text</p></body></html>",
        }
    )
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="web-browse-vertical-slice",
        working_dir=tmp_path,
        capabilities={"web": {"browser_port": port}},
        disable=[
            "knowledge", "skills", "shell", "avatar", "daemon", "mcp",
            "read", "write", "edit", "glob", "grep", "vision",
        ],
    )
    try:
        assert "web" in agent._tool_handlers
        schemas = agent._build_tool_schemas()
        web_schema = next(schema for schema in schemas if schema.name == "web")
        assert set(web_schema.parameters["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert web_schema.parameters["required"] == ["action", "input", "reasoning"]
        assert web_schema.parameters["additionalProperties"] is False
        assert all(
            "reasoning" not in branch["properties"]
            and "_reasoning" not in branch["properties"]
            and "summarize" not in branch["properties"]
            for branch in web_schema.parameters["properties"]["input"]["oneOf"]
        )
        first = agent._tool_handlers["web"]({
            "action": "browse",
            "input": {
                "url": "https://public.example/page",
                "link_ref": None,
                "cursor": None,
                "extract": None,
                "max_chars": None,
            },
        })
        assert first["status"] == "ok"
        assert first["source_sha256"]
        assert first["snapshot_id"]
        assert first["requested_url"] == "https://public.example/page"
        assert first["final_url"] == "https://public.example/page"
        assert first["untrusted_content"] is True
        # A fresh Browse success delivers the complete extracted document in
        # one call and never mints a continuation cursor.
        assert first["next_cursor"] is None
        assert first["partial"] is False
        assert first["delivery"] == "inline"
        calls_after_first = len(port.calls)

        # A legacy cursor (obtained directly from the underlying engine, as a
        # pre-migration caller would have) remains accepted only as a
        # compatibility locator: it resolves the same cached snapshot without
        # a refetch, but still returns the complete document under the new
        # full-output policy rather than another partial page.
        from lingtai.tools.browser.cursor import CursorPayload
        web_manager = agent._tool_handlers["web"].__self__
        engine = web_manager.browser_engine
        snapshot_id = first["snapshot_id"]
        legacy_cursor = engine.cursor.encode(CursorPayload(snapshot_id, "article", 0, 5))
        second = agent._tool_handlers["web"]({
            "action": "browse",
            "input": {
                "url": "https://public.example/page",
                "link_ref": None,
                "cursor": legacy_cursor,
                "extract": None,
                "max_chars": None,
            },
        })
        assert second["status"] == "ok"
        assert len(port.calls) == calls_after_first
        assert second["next_cursor"] is None
        assert second["partial"] is False
        assert second["blocks"] == first["blocks"]

        link_ref = first["links"][0]["ref"]
        followed = agent._tool_handlers["web"]({
            "action": "browse",
            "input": {
                "url": None,
                "link_ref": link_ref,
                "cursor": None,
                "extract": None,
                "max_chars": None,
            },
        })
        assert followed["status"] == "ok"
        assert followed["requested_url"] == "https://public.example/next"

        manual = agent._tool_handlers["web"]({
            "action": "manual",
            "input": {},
            "_reasoning": "normalized by ToolExecutor",
        })
        assert manual["status"] == "ok"
        assert manual["action"] == "manual"
        assert len(port.calls) == calls_after_first + 1
    finally:
        agent.stop(timeout=1.0)


def test_web_action_input_search_to_link_ref_browse(tmp_path):
    search = FakeSearch()
    port = FakeBrowserPort({
        "https://public.example/page": b"<html><body><p>Provider-shaped page</p></body></html>",
    })
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="web-action-input",
        working_dir=tmp_path,
        capabilities={
            "web": {
                "default_engine": "duckduckgo",
                "engines": {
                    "duckduckgo": {
                        "provider": "duckduckgo",
                        "search_service": search,
                    }
                },
                "browser_port": port,
            }
        },
        disable=[
            "knowledge", "skills", "shell", "avatar", "daemon", "mcp",
            "read", "write", "edit", "glob", "grep", "vision",
        ],
    )
    try:
        handler = agent._tool_handlers["web"]
        manual = handler({"action": "manual", "input": {}})
        assert manual["status"] == "ok"
        found = handler({"action": "search", "input": {"query": "mission interval"}})
        assert found["status"] == "ok"
        assert search.queries == ["mission interval"]
        link_ref = found["results"][0]["link_ref"]
        browsed = handler({
            "action": "browse",
            "input": {
                "url": None,
                "link_ref": link_ref,
                "cursor": None,
                "extract": None,
                "max_chars": None,
            },
        })
        assert browsed["status"] == "ok"
        assert browsed["requested_url"] == "https://public.example/page"
        # A root-level key that IS a declared property of the selected
        # action's schema and is entirely absent from ``input`` is relocated
        # into ``input`` rather than rejected (issue #1251) — the search must
        # run with the relocated query.
        flat = handler({"action": "search", "input": {}, "query": "legacy flat"})
        assert flat["status"] == "ok"
        assert search.queries == ["mission interval", "legacy flat"]
        engine = handler({"action": "manual", "input": {"engine": "duckduckgo"}})
        assert engine["error_code"] == "INVALID_ARGUMENT"
        for legacy_key in ("parameters", "parameter"):
            legacy = handler({"action": "manual", legacy_key: {}})
            assert legacy["error_code"] == "INVALID_ARGUMENT"
    finally:
        agent.stop(timeout=1.0)


def _web_startup_agent(tmp_path, name):
    return Agent(
        service=make_gemini_mock_service(),
        agent_name=name,
        working_dir=tmp_path,
        capabilities={"web": {}},
        disable=[
            "knowledge", "skills", "shell", "avatar", "daemon", "mcp",
            "read", "write", "edit", "glob", "grep", "vision",
        ],
    )


def test_web_real_agent_startup_and_complete_prompt_build(tmp_path, monkeypatch):
    """The resident ``## tools`` walkthrough is opt-in, so assert it opted in.

    The gate (``LINGTAI_TOOL_PROSE_SECTION_ENABLED``) decides whether the
    section exists at all; this test is about ``web`` reaching a *complete*
    prompt build, so it turns the section on. The default state — where ``web``
    reaches the model through its tool schema instead — is covered by
    ``test_web_real_agent_startup_reaches_schemas_without_prose_section``.
    """
    monkeypatch.setenv(TOOL_PROSE_SECTION_ENABLED_ENV, "1")
    agent = _web_startup_agent(tmp_path, "web-startup-gate")
    try:
        agent.start()
        prompt = agent._build_system_prompt()
        batches = agent._build_system_prompt_batches()
        schemas = agent._build_tool_schemas()
        assert "### web" in prompt
        assert "web" in {schema.name for schema in schemas}
        assert "web" in "\n\n".join(batches)
    finally:
        agent.stop(timeout=1.0)


def test_web_real_agent_startup_reaches_schemas_without_prose_section(
    tmp_path, monkeypatch
):
    """Default gate state: no ``## tools`` section, but ``web`` is still callable."""
    monkeypatch.delenv(TOOL_PROSE_SECTION_ENABLED_ENV, raising=False)
    agent = _web_startup_agent(tmp_path, "web-startup-gate-off")
    try:
        agent.start()
        prompt = agent._build_system_prompt()
        schemas = {schema.name: schema for schema in agent._build_tool_schemas()}
        assert "### web" not in prompt
        assert "## tools" not in prompt
        # The capability is not lost — its full prose rides on the schema the
        # provider payload is built from.
        assert "web" in schemas
        assert schemas["web"].description
    finally:
        agent.stop(timeout=1.0)


def test_browser_policy_and_cursor_failures_are_typed_and_sanitized():
    port = FakeBrowserPort({"https://public.example/page": b"<p>content</p>"})
    engine = BrowserEngine(port)
    unsafe = engine.handle({"url": "http://127.0.0.1/admin"})
    assert unsafe["status"] == "failed"
    assert unsafe["error_code"] == "LOOPBACK_BLOCKED"
    assert "127.0.0.1" not in unsafe["message"]
    assert not port.calls
    malformed = engine.handle({"url": "https://user:password@public.example/page"})
    assert malformed["error_code"] == "URL_USERINFO_FORBIDDEN"
    stale = engine.handle({"url": "https://public.example/page", "cursor": "broken"})
    assert stale["error_code"] == "CURSOR_MALFORMED"


def test_web_schema_includes_strict_action_input():
    schema = get_schema()
    assert schema["properties"]["action"]["enum"] == ["search", "browse", "manual"]
    assert schema["required"] == ["action", "input", "reasoning"]
    assert schema["additionalProperties"] is False
    branches = schema["properties"]["input"]["oneOf"]
    assert [branch["title"] for branch in branches] == [
        "search input", "browse input", "manual input",
    ]
    for branch in branches:
        assert branch["additionalProperties"] is False
        assert set(branch["required"]) == set(branch["properties"])
    browse = branches[1]["properties"]
    assert browse["url"]["type"] == ["string", "null"]
    assert browse["extract"]["enum"] == ["article", None]
    assert branches[2]["properties"] == {}
