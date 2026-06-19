"""Tests for Codex/OpenAI Responses ``prompt_cache_key`` plumbing.

Codex's ``/backend-api/codex/responses`` endpoint accepts ``prompt_cache_key``
to opt into cross-request prompt caching, but rejects ``prompt_cache_retention``
(``Unsupported parameter``) and content-block ``cache_control`` (``Unknown
parameter``). These tests assert the wire kwargs the session sends:

  * Codex Responses requests carry a stable ``prompt_cache_key``.
  * They never carry ``prompt_cache_retention``.
  * No Anthropic-style ``cache_control`` leaks into input/tools/instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace

from lingtai.llm.openai.adapter import (
    CodexOpenAIAdapter,
    CodexResponsesSession,
    OpenAIResponsesSession,
)
from lingtai_kernel.llm.base import FunctionSchema


@dataclass
class Event:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None
    item_id: str | None = None
    text: str | None = None


class FakeResponses:
    def __init__(self, events: list[Event]):
        self.events = events
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        yield from self.events


class FakeClient:
    def __init__(self, events: list[Event]):
        self.responses = FakeResponses(events)


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=10,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _completed() -> Event:
    return Event(
        "response.completed",
        response=SimpleNamespace(id="resp_fake", usage=_usage()),
    )


def _function_schema() -> FunctionSchema:
    return FunctionSchema(
        name="report_answer",
        description="Report answer",
        parameters={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )


def _create_codex_session(events: list[Event], *, model: str = "gpt-5.5"):
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
    )
    adapter._client = FakeClient(events)
    return adapter.create_chat(
        model,
        "system prompt",
        tools=[_function_schema()],
        force_tool_call=True,
        thinking="high",
    )


def _no_cache_control(payload) -> bool:
    """Return True iff ``cache_control`` appears nowhere in ``payload``."""
    return "cache_control" not in json.dumps(payload, default=str)


def test_codex_request_includes_default_prompt_cache_key():
    session = _create_codex_session([_completed()], model="gpt-5.5")

    session.send("please answer via tool")

    sent = session._client.responses.kwargs[0]
    assert sent["prompt_cache_key"] == "lingtai-codex:gpt-5.5:v1"


def test_codex_request_omits_prompt_cache_retention():
    session = _create_codex_session([_completed()])

    session.send("please answer via tool")

    sent = session._client.responses.kwargs[0]
    assert "prompt_cache_retention" not in sent


def test_codex_request_has_no_cache_control_anywhere():
    session = _create_codex_session([_completed()])

    session.send("please answer via tool")

    sent = session._client.responses.kwargs[0]
    assert _no_cache_control(sent)


def test_codex_prompt_cache_key_is_stable_across_requests():
    session = _create_codex_session([_completed(), _completed()], model="gpt-5.5")

    session.send("first")
    session.send("second")

    keys = [kw["prompt_cache_key"] for kw in session._client.responses.kwargs]
    assert keys == ["lingtai-codex:gpt-5.5:v1", "lingtai-codex:gpt-5.5:v1"]


def test_explicit_prompt_cache_key_overrides_default():
    session = CodexResponsesSession(
        client=FakeClient([_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        prompt_cache_key="custom-key:v2",
    )

    session.send("hi")

    sent = session._client.responses.kwargs[0]
    assert sent["prompt_cache_key"] == "custom-key:v2"


def test_responses_session_omits_cache_key_when_unset():
    """Non-Codex Responses sessions don't send prompt_cache_key unless asked."""
    session = OpenAIResponsesSession(
        client=FakeClient([_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )

    session.send_stream("hi")

    sent = session._client.responses.kwargs[0]
    assert "prompt_cache_key" not in sent
    assert "prompt_cache_retention" not in sent


# ---------------------------------------------------------------------------
# Codex REST cache-affinity headers — session-id / thread-id (issue #378)
# ---------------------------------------------------------------------------


def _create_codex_session_cfg(events, *, model="gpt-5.5", **adapter_kw):
    """Build a Codex session through the adapter with extra config kwargs."""
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        **adapter_kw,
    )
    adapter._client = FakeClient(events)
    return adapter.create_chat(
        model,
        "system prompt",
        tools=[_function_schema()],
        force_tool_call=True,
        thinking="high",
    )


def test_codex_bare_adapter_omits_session_thread_headers():
    """A bare adapter (no per-agent identity passed down) sends no headers.

    This is the test/standalone path: when nothing supplies the agent path
    (the host wiring normally does), the adapter cannot distinguish agents and
    must not collapse them onto one session/thread, so it stays silent.
    """
    session = _create_codex_session([_completed()], model="gpt-5.5")

    session.send("please answer via tool")

    sent = session._client.responses.kwargs[0]
    headers = sent.get("extra_headers") or {}
    assert "session-id" not in headers
    assert "thread-id" not in headers
    # prompt_cache_key behavior is untouched.
    assert sent["prompt_cache_key"] == "lingtai-codex:gpt-5.5:v1"


def test_codex_sends_stable_headers_from_session_anchor():
    session = _create_codex_session_cfg(
        [_completed(), _completed()],
        model="gpt-5.5",
        codex_session_anchor="/agents/alice/init.json",
    )

    session.send("first")
    session.send("second")

    h0 = session._client.responses.kwargs[0]["extra_headers"]
    h1 = session._client.responses.kwargs[1]["extra_headers"]
    # Present, UUID-shaped, and stable across requests of the same session.
    assert h0["session-id"] and h0["thread-id"]
    assert _is_uuid(h0["session-id"]) and _is_uuid(h0["thread-id"])
    assert h0 == h1
    # prompt_cache_key still rides alongside (not broken by the headers).
    assert session._client.responses.kwargs[0]["prompt_cache_key"] == "lingtai-codex:gpt-5.5:v1"


def test_codex_headers_differ_for_different_agents():
    a = _create_codex_session_cfg(
        [_completed()], codex_session_anchor="/agents/alice/init.json"
    )
    b = _create_codex_session_cfg(
        [_completed()], codex_session_anchor="/agents/bob/init.json"
    )

    a.send("x")
    b.send("x")

    ha = a._client.responses.kwargs[0]["extra_headers"]
    hb = b._client.responses.kwargs[0]["extra_headers"]
    assert ha["session-id"] != hb["session-id"]
    assert ha["thread-id"] != hb["thread-id"]


def test_codex_thread_id_varies_by_thread_salt_session_id_stable():
    """Same agent, different thread salt (last API call id) -> same session, new thread.

    The adapter is salt-source-agnostic: it varies thread-id by whatever salt
    string it receives, while session-id stays anchored to the agent path.
    """
    salt0 = _create_codex_session_cfg(
        [_completed()],
        codex_session_anchor="/agents/alice/init.json",
        codex_thread_salt="api_1000aaaa",
    )
    salt1 = _create_codex_session_cfg(
        [_completed()],
        codex_session_anchor="/agents/alice/init.json",
        codex_thread_salt="api_2000bbbb",
    )

    salt0.send("x")
    salt1.send("x")

    h0 = salt0._client.responses.kwargs[0]["extra_headers"]
    h1 = salt1._client.responses.kwargs[0]["extra_headers"]
    assert h0["session-id"] == h1["session-id"]  # session stable across salts
    assert h0["thread-id"] != h1["thread-id"]  # thread changes per salt


def test_codex_rest_omits_previous_response_id_with_call_id_thread():
    """Codex REST stays stateless: no previous_response_id even with headers on.

    The thread-id is derived from a last-call-id-shaped salt; that must not pull
    in any server-side response chaining.
    """
    session = _create_codex_session_cfg(
        [_completed()],
        model="gpt-5.5",
        codex_session_anchor="/agents/alice/init.json",
        codex_thread_salt="api_2000bbbb",
    )

    session.send("please answer via tool")

    sent = session._client.responses.kwargs[0]
    assert "previous_response_id" not in sent
    assert sent.get("store") is False
    assert sent["extra_headers"]["thread-id"]  # call-id-derived thread still sent
    # prompt_cache_key behavior preserved alongside the stateless REST contract.
    assert sent["prompt_cache_key"] == "lingtai-codex:gpt-5.5:v1"


def test_codex_explicit_session_id_used_verbatim():
    explicit = "11111111-2222-3333-4444-555555555555"
    session = _create_codex_session_cfg(
        [_completed()], codex_session_id=explicit
    )

    session.send("x")

    headers = session._client.responses.kwargs[0]["extra_headers"]
    assert headers["session-id"] == explicit
    assert _is_uuid(headers["thread-id"])


def test_codex_explicit_session_id_wins_over_anchor():
    explicit = "11111111-2222-3333-4444-555555555555"
    session = _create_codex_session_cfg(
        [_completed()],
        codex_session_id=explicit,
        codex_session_anchor="/agents/alice/init.json",
    )

    session.send("x")

    assert session._client.responses.kwargs[0]["extra_headers"]["session-id"] == explicit


def test_codex_session_headers_can_be_set_directly_on_session():
    """The session accepts session_id/thread_id directly (adapter-independent)."""
    session = CodexResponsesSession(
        client=FakeClient([_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        prompt_cache_key="custom-key:v2",
        session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        thread_id="ffffffff-0000-1111-2222-333333333333",
    )

    session.send("hi")

    sent = session._client.responses.kwargs[0]
    assert sent["extra_headers"]["session-id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert sent["extra_headers"]["thread-id"] == "ffffffff-0000-1111-2222-333333333333"
    # prompt_cache_key still sent independently.
    assert sent["prompt_cache_key"] == "custom-key:v2"


def test_codex_bare_session_omits_headers():
    """A directly-constructed session with no ids sends no header (bare/test path)."""
    session = CodexResponsesSession(
        client=FakeClient([_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )

    session.send("hi")

    assert "extra_headers" not in session._client.responses.kwargs[0]


# ---------------------------------------------------------------------------
# Manifest config seam — per-agent identity flows factory -> adapter (#378).
# This is the internal override / testing escape hatch; the default path
# (agent path + last ledgered API call id) is covered in the section after this one.
# ---------------------------------------------------------------------------


def test_manifest_config_keys_pass_through_to_provider_defaults():
    """codex_session_id/anchor/thread_salt survive the manifest->defaults map."""
    import lingtai  # noqa: F401  (registers adapters / loads service module)
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    d = build_provider_defaults_from_manifest_llm(
        {
            "provider": "codex",
            "codex_session_anchor": "/agents/alice/init.json",
            "codex_thread_salt": "explicit-salt",
        },
        max_rpm=0,
    )
    assert d["codex"]["codex_session_anchor"] == "/agents/alice/init.json"
    assert d["codex"]["codex_thread_salt"] == "explicit-salt"

    # No codex config and no working_dir -> nothing leaks (historical None).
    assert build_provider_defaults_from_manifest_llm({"provider": "codex"}, max_rpm=0) is None


def test_codex_factory_builds_adapter_with_per_agent_ids():
    """The registered codex factory wires manifest config into resolved ids."""
    from unittest import mock

    import lingtai  # noqa: F401
    from lingtai.llm.service import LLMService

    with mock.patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls:
        mgr_cls.return_value.get_access_token.return_value = "fake-token"

        svc = LLMService(
            provider="codex",
            model="gpt-5.5",
            provider_defaults={
                "codex": {
                    "codex_session_anchor": "/agents/alice/init.json",
                    "codex_thread_salt": "2026-06-03T00:00:00Z",
                }
            },
        )
        sid, tid = svc.get_adapter("codex")._resolve_codex_ids("gpt-5.5")
        assert _is_uuid(sid) and _is_uuid(tid) and sid != tid

        # No config -> the safe default: no per-agent identity, no headers.
        svc2 = LLMService(provider="codex", model="gpt-5.5")
        assert svc2.get_adapter("codex")._resolve_codex_ids("gpt-5.5") == (None, None)


# ---------------------------------------------------------------------------
# Default wiring — agent path + last ledgered API call id passed down automatically (#378,
# thread salt switched from last molt time to last ledgered API call id per Jason's #392
# follow-up). The legacy _latest_molt_time helper still exists and is unit-
# tested below, but it no longer feeds the Codex thread salt.
# ---------------------------------------------------------------------------


def _write_molt_summary(working_dir, *, count, ts, created_at):
    """Write a system/summaries/molt_<count>_<ts>.md like the molt machinery.

    ``count`` only shapes the on-disk filename/frontmatter (the real format
    written by _snapshots._write_molt_summary); the thread salt is the
    ``created_at`` last-molt time, never the count.
    """
    summaries = working_dir / "system" / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    (summaries / f"molt_{count}_{ts}.md").write_text(
        f"---\nmolt_count: {count}\ncreated_at: {created_at}\n---\n\nbody",
        encoding="utf-8",
    )


def test_latest_molt_time_reads_created_at_from_newest_summary(tmp_path):
    from lingtai.llm.service import _latest_molt_time

    _write_molt_summary(tmp_path, count=1, ts=1000, created_at="2026-01-01T00:00:00Z")
    _write_molt_summary(tmp_path, count=2, ts=2000, created_at="2026-06-01T12:00:00Z")

    # Newest by filename ts wins, and we read its frontmatter created_at.
    assert _latest_molt_time(tmp_path) == "2026-06-01T12:00:00Z"


def test_latest_molt_time_falls_back_to_filename_ts(tmp_path):
    from datetime import datetime, timezone

    from lingtai.llm.service import _latest_molt_time

    summaries = tmp_path / "system" / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    # No frontmatter created_at -> fall back to the filename unix ts.
    (summaries / "molt_3_1750000000.md").write_text("no frontmatter", encoding="utf-8")

    expected = datetime.fromtimestamp(1750000000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assert _latest_molt_time(tmp_path) == expected


def test_latest_molt_time_falls_back_to_agent_json_created_at(tmp_path):
    from lingtai.llm.service import _latest_molt_time

    # No molt summaries yet -> use .agent.json created_at (birth-stable thread).
    (tmp_path / ".agent.json").write_text(
        json.dumps({"created_at": "2026-05-05T05:05:05Z"}), encoding="utf-8"
    )
    assert _latest_molt_time(tmp_path) == "2026-05-05T05:05:05Z"


def test_latest_molt_time_none_when_no_source(tmp_path):
    from lingtai.llm.service import _latest_molt_time

    assert _latest_molt_time(tmp_path) is None


def _write_token_ledger(working_dir, entries):
    """Write logs/token_ledger.jsonl entries in chronological order."""
    logs = working_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    (logs / "token_ledger.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ledger_entry(api_call_id=None, *, source="main", cached=0):
    entry = {
        "ts": "2026-06-19T00:00:00Z",
        "input": 10,
        "output": 2,
        "thinking": 0,
        "cached": cached,
        "source": source,
    }
    if api_call_id is not None:
        entry["api_call_id"] = api_call_id
    return entry


def test_default_wiring_injects_agent_path_and_last_call_salt(tmp_path):
    """Codex defaults: session anchor = resolved init.json path; salt = last API call id."""
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    _write_token_ledger(tmp_path, [_ledger_entry("api_1000aaaa")])

    d = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )
    assert d["codex"]["codex_session_anchor"] == str((tmp_path / "init.json").resolve())
    assert d["codex"]["codex_thread_salt"] == "api_1000aaaa"


def test_default_wiring_uses_birth_salt_before_first_call(tmp_path):
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    # No token ledger yet -> stable "birth" salt.
    d = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )
    assert d["codex"]["codex_thread_salt"] == "birth"


def test_default_wiring_only_applies_to_codex(tmp_path):
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    d = build_provider_defaults_from_manifest_llm(
        {"provider": "openai"}, max_rpm=0, working_dir=tmp_path
    )
    # Non-codex providers get no codex identity injected (None when otherwise empty).
    assert d is None


def test_manifest_salt_overrides_default_last_call_id(tmp_path):
    """Explicit manifest config wins (internal override / testing escape hatch)."""
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    _write_token_ledger(tmp_path, [_ledger_entry("api_1000aaaa")])

    d = build_provider_defaults_from_manifest_llm(
        {
            "provider": "codex",
            "codex_session_anchor": "/custom/anchor",
            "codex_thread_salt": "override-salt",
        },
        max_rpm=0,
        working_dir=tmp_path,
    )
    assert d["codex"]["codex_session_anchor"] == "/custom/anchor"
    assert d["codex"]["codex_thread_salt"] == "override-salt"


def test_default_wiring_session_anchor_differs_by_agent_path(tmp_path):
    """Different agent working dirs get different session anchors."""
    import lingtai  # noqa: F401
    from lingtai.llm.openai.adapter import _codex_session_id
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    alice = tmp_path / "alice"
    bob = tmp_path / "bob"
    alice.mkdir()
    bob.mkdir()
    _write_token_ledger(alice, [_ledger_entry("api_2000bbbb")])
    _write_token_ledger(bob, [_ledger_entry("api_2000bbbb")])

    da = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=alice
    )["codex"]
    db = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=bob
    )["codex"]

    assert da["codex_session_anchor"] != db["codex_session_anchor"]
    assert _codex_session_id(da["codex_session_anchor"]) != _codex_session_id(
        db["codex_session_anchor"]
    )


def test_latest_call_id_reads_newest_main_api_call_id(tmp_path):
    from lingtai.llm.service import _latest_call_id

    _write_token_ledger(
        tmp_path,
        [
            _ledger_entry("api_1000aaaa", source="main"),
            _ledger_entry("api_2000bbbb", source="main"),
        ],
    )

    assert _latest_call_id(tmp_path) == "api_2000bbbb"


def test_latest_call_id_prefers_newest_main_over_newer_non_main(tmp_path):
    from lingtai.llm.service import _latest_call_id

    _write_token_ledger(
        tmp_path,
        [
            _ledger_entry("api_main", source="main"),
            _ledger_entry("api_tc_wake", source="tc_wake"),
            _ledger_entry("api_soul", source="soul"),
        ],
    )

    assert _latest_call_id(tmp_path) == "api_main"


def test_latest_call_id_falls_back_to_newest_any_api_call_id(tmp_path):
    from lingtai.llm.service import _latest_call_id

    _write_token_ledger(
        tmp_path,
        [
            _ledger_entry("api_daemon", source="daemon"),
            {**_ledger_entry("api_untagged"), "source": None},
        ],
    )

    assert _latest_call_id(tmp_path) == "api_untagged"


def test_latest_call_id_none_when_no_ledger(tmp_path):
    from lingtai.llm.service import _latest_call_id

    assert _latest_call_id(tmp_path) is None


def test_latest_call_id_none_when_no_api_call_id_present(tmp_path):
    from lingtai.llm.service import _latest_call_id

    _write_token_ledger(tmp_path, [_ledger_entry(None), _ledger_entry(None, source="tc_wake")])
    assert _latest_call_id(tmp_path) is None


def test_default_wiring_thread_salt_is_last_call_id_not_molt_time(tmp_path):
    """Codex defaults: thread salt is the last API call id; molt time is irrelevant."""
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    # A molt summary exists, but it must NOT be the thread salt anymore.
    _write_molt_summary(tmp_path, count=1, ts=1000, created_at="2026-06-01T12:00:00Z")
    _write_token_ledger(tmp_path, [_ledger_entry("api_2000bbbb")])

    d = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )
    assert d["codex"]["codex_session_anchor"] == str((tmp_path / "init.json").resolve())
    assert d["codex"]["codex_thread_salt"] == "api_2000bbbb"
    assert d["codex"]["codex_thread_salt"] != "2026-06-01T12:00:00Z"


def test_default_wiring_no_molt_time_dependency_for_salt(tmp_path):
    """Changing only the molt time (call id fixed) must not change the salt."""
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    _write_token_ledger(tmp_path, [_ledger_entry("api_5000eeee")])

    _write_molt_summary(tmp_path, count=1, ts=1000, created_at="2026-06-01T00:00:00Z")
    salt0 = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )["codex"]["codex_thread_salt"]

    _write_molt_summary(tmp_path, count=2, ts=2000, created_at="2026-07-01T00:00:00Z")
    salt1 = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )["codex"]["codex_thread_salt"]

    assert salt0 == salt1 == "api_5000eeee"


def test_default_wiring_last_call_id_affects_thread_id_session_id_stable(tmp_path):
    """Same agent path, two different last API call ids -> same session, new thread."""
    import lingtai  # noqa: F401
    from lingtai.llm.openai.adapter import _codex_session_id, _codex_thread_id
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    _write_token_ledger(tmp_path, [_ledger_entry("api_1000aaaa")])
    d0 = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )["codex"]

    _write_token_ledger(tmp_path, [_ledger_entry("api_9000zzzz")])
    d1 = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )["codex"]

    sid0 = _codex_session_id(d0["codex_session_anchor"])
    sid1 = _codex_session_id(d1["codex_session_anchor"])
    assert sid0 == sid1  # session-id stable across calls (same agent path)

    tid0 = _codex_thread_id(sid0, d0["codex_thread_salt"])
    tid1 = _codex_thread_id(sid1, d1["codex_thread_salt"])
    assert tid0 != tid1  # thread-id rotates with the last API call id


def test_default_wiring_uses_birth_salt_when_no_calls_yet(tmp_path):
    """No token ledger yet -> stable birth salt so the thread is constant."""
    import lingtai  # noqa: F401
    from lingtai.llm.service import build_provider_defaults_from_manifest_llm

    d = build_provider_defaults_from_manifest_llm(
        {"provider": "codex"}, max_rpm=0, working_dir=tmp_path
    )
    assert d["codex"]["codex_thread_salt"] == "birth"


# ---------------------------------------------------------------------------
# Codex cache-affinity ids ride with usage so token_ledger.jsonl can record them
# (Jason's follow-up to #392). This is not an events.jsonl event.
# ---------------------------------------------------------------------------


def test_codex_usage_extra_carries_cache_affinity_ids_for_token_ledger():
    """Usage metadata exposes the actual sent ids for token-ledger writes."""
    session = CodexResponsesSession(
        client=FakeClient([_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        prompt_cache_key="custom-key:v2",
        session_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        thread_id="ffffffff-0000-1111-2222-333333333333",
    )

    result = session.send("hi")

    sent = session._client.responses.kwargs[0]
    headers = sent["extra_headers"]
    assert result.usage.extra == {
        "codex_session_id": headers["session-id"],
        "codex_thread_id": headers["thread-id"],
    }
    # Token-ledger metadata is intentionally small/non-secret: no prompt body or
    # prompt_cache_key value rides in the usage extra payload.
    blob = json.dumps(result.usage.extra, default=str)
    assert "custom-key" not in blob
    assert "prompt_cache_key" not in blob
    assert "input" not in result.usage.extra and "messages" not in result.usage.extra


def test_codex_usage_extra_empty_when_no_cache_affinity_headers():
    """Bare/test Codex sessions without ids add no token-ledger id fields."""
    session = CodexResponsesSession(
        client=FakeClient([_completed()]),
        model="gpt-5.5",
        instructions="system prompt",
        tools=None,
        tool_choice=None,
        extra_kwargs={},
    )

    result = session.send("hi")

    assert "extra_headers" not in session._client.responses.kwargs[0]
    assert result.usage.extra == {}


def test_token_ledger_entry_merges_usage_extra(tmp_path):
    """BaseAgent token-ledger writes preserve safe provider metadata."""
    from types import SimpleNamespace as _SimpleNamespace

    from lingtai_kernel.base_agent import BaseAgent
    from lingtai_kernel.llm.base import UsageMetadata

    class _Workdir:
        def write_manifest(self, manifest):
            pass

    agent = _SimpleNamespace(
        _working_dir=tmp_path,
        _workdir=_Workdir(),
        agent_name="agent",
        get_chat_state=lambda: {"messages": []},
        _build_manifest=lambda: {},
        _write_status_snapshot=lambda: None,
        _last_usage=UsageMetadata(
            input_tokens=10,
            output_tokens=2,
            thinking_tokens=1,
            cached_tokens=8,
            extra={
                "codex_session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "codex_thread_id": "ffffffff-0000-1111-2222-333333333333",
            },
        ),
        _session=_SimpleNamespace(_model="gpt-5.5"),
        service=_SimpleNamespace(model="fallback", _base_url="https://chatgpt.com/backend-api/codex"),
    )

    BaseAgent._save_chat_history(agent)

    entry = json.loads((tmp_path / "logs" / "token_ledger.jsonl").read_text())
    assert entry["source"] == "main"
    assert entry["codex_session_id"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert entry["codex_thread_id"] == "ffffffff-0000-1111-2222-333333333333"
    assert entry["input"] == 10 and entry["cached"] == 8


def _is_uuid(value: str) -> bool:
    import uuid as _uuid

    try:
        _uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False
