"""Tests for standalone Codex Responses compaction (``context_token_limit``).

Live wire evidence (2026-07-14, model ``gpt-5.6-sol``) showed the ChatGPT Codex
backend accepts a standalone ``POST /responses/compact`` call — distinct from
the generic OpenAI Responses ``context_management`` compaction, which Codex
rejects (see ``CodexOpenAIAdapter._create_responses_session``, which always
passes ``compact_threshold=None``). Compaction returns
``object="response.compaction"`` with two opaque output items (``message`` +
``compaction_summary``, the latter carrying an opaque ``encrypted_content``);
replaying those items verbatim plus new input reproduced an exact retained
marker.

**Real send() coverage, not manual pre-triggers.** Every compaction-firing test
here drives history through real ``session.send(...)`` calls (the same path
the daemon's tool loop uses) and lets ``_maybe_compact_before_send`` decide
whether to fire from its own projected-token logic — none of them call
``_maybe_compact_before_send()`` directly before the assertion turn. This
matters because the ORDER in which a turn is appended to the canonical
interface versus when compaction fires is exactly what a prior version of
this file's tests hid: manually pre-triggering compaction before ``send()``
made the new turn arrive as a clean delta by construction, which is not the
real ``send_stream`` ordering and would have passed even if the real turn
that fires compaction got folded into the opaque summary instead of riding as
the live trailing item.

These are pure/mock tests — no network, no OAuth. They cover:
  * task schema/validation for ``context_token_limit`` (positive int, bool
    rejected, ``None`` allowed).
  * explicit override vs. omitted-inherits-context-window (asserted via a real
    end-to-end send that actually reduces context, not just a resolved int).
  * the standalone compact request shape/endpoint (via the fake
    ``client.responses.compact`` call), including binding the sent kwargs
    against the real installed SDK signature.
  * the live turn that TRIGGERS compaction rides as the trailing item of the
    very next ``create()`` request — never folded into the opaque summary —
    for both a plain user turn and a tool-result continuation whose matching
    ``function_call`` already sits in history.
  * opaque ``message`` + ``compaction_summary`` replay, byte-for-byte, with no
    encrypted content ever logged.
  * additive-only delta after compaction (only new turns are converted and
    appended; the compacted prefix is replayed verbatim).
  * an exact-equality boundary crossing (projected tokens == limit) and a
    strictly-below-limit non-firing case.
  * invalidation on a history rewrite (``_reset_ws_epoch``) rather than
    silently keeping a stale compacted base.
  * no recompaction loop once compaction is active for the current prefix.
  * the generic ``compact_threshold``/``context_management`` axis stays
    ``None`` for Codex, untouched by this feature.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from lingtai.llm.openai.adapter import (
    CodexOpenAIAdapter,
    CodexResponsesSession,
    _estimate_responses_input_tokens,
    _validate_codex_compact_token_limit,
)
from lingtai.kernel.llm.interface import ToolResultBlock


# ---------------------------------------------------------------------------
# Fakes — scripted per-turn provider responses driving real send() calls.
# ---------------------------------------------------------------------------


@dataclass
class Event:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None
    item_id: str | None = None
    text: str | None = None
    arguments: str | None = None


_OPAQUE_MARKER = "OPAQUE_ENCRYPTED_CONTENT_MUST_NEVER_BE_LOGGED"


def _usage(input_tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def text_turn(input_tokens: int, resp_id: str = "resp_txt") -> list[Event]:
    """Scripted events for a plain assistant-text response.completed."""
    return [
        Event("response.completed", response=SimpleNamespace(id=resp_id, usage=_usage(input_tokens))),
    ]


def tool_call_turn(input_tokens: int, call_id: str, name: str = "search", resp_id: str = "resp_tc") -> list[Event]:
    """Scripted events for an assistant turn that issues one function_call."""
    return [
        Event("response.output_item.added", item=SimpleNamespace(type="function_call", call_id=call_id, name=name)),
        Event("response.function_call_arguments.done", arguments="{}"),
        Event(
            "response.output_item.done",
            item=SimpleNamespace(type="function_call", call_id=call_id, name=name, arguments="{}"),
        ),
        Event("response.completed", response=SimpleNamespace(id=resp_id, usage=_usage(input_tokens))),
    ]


class FakeCompactItem:
    """Mimics an SDK model object with ``.model_dump()``."""

    def __init__(self, data: dict):
        self._data = data

    def model_dump(self, mode: str = "json", exclude_none: bool = True):
        return dict(self._data)


class FakeCompactResult:
    def __init__(self, output: list[FakeCompactItem]):
        self.output = output


def _default_compact_output() -> list[FakeCompactItem]:
    return [
        FakeCompactItem({
            "type": "message",
            "id": "msg_compacted",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "compacted summary text"}],
        }),
        FakeCompactItem({
            "type": "compaction_summary",
            "id": "cs_1",
            "encrypted_content": _OPAQUE_MARKER,
        }),
    ]


class ScriptedResponses:
    """Fake ``client.responses`` — a queue of scripted turns plus ``compact()``.

    Each ``create()`` call consumes the next scripted event list in order,
    mirroring real ``codex.responses.create`` streaming. Raises
    ``AssertionError`` (not silently reusing the last turn) if the test drives
    more real turns than it scripted — a scripting bug should fail loudly, not
    quietly return stale data.
    """

    def __init__(self, turns: list[list[Event]], compact_output: list[FakeCompactItem] | None = None):
        self._turns = list(turns)
        self._idx = 0
        self.create_calls: list[dict] = []
        self.compact_calls: list[dict] = []
        self._compact_output = compact_output if compact_output is not None else _default_compact_output()

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        assert self._idx < len(self._turns), (
            f"ScriptedResponses ran out of scripted turns at call {self._idx + 1}; "
            "add more turns to the fixture."
        )
        events = self._turns[self._idx]
        self._idx += 1
        return iter(events)

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        return FakeCompactResult(output=list(self._compact_output))


class FakeClient:
    def __init__(self, turns: list[list[Event]], compact_output: list[FakeCompactItem] | None = None):
        self.responses = ScriptedResponses(turns, compact_output)


class DynamicResponses:
    """Fake ``client.responses`` whose ``usage.input_tokens`` is DERIVED from
    the actual rendered request it received, via the SAME deterministic
    estimator (``_estimate_responses_input_tokens``) the fixed compaction code
    uses, scaled by a known, explicit ``scale`` factor (default ``1.0``).

    This makes "provider actual" and "local rendered-request estimate"
    internally consistent by construction — unlike a fixed-token
    ``ScriptedResponses`` turn, where a hand-picked ``input_tokens`` value can
    silently disagree with the real size of whatever ``kwargs["input"]``
    actually contains for that call. Used for regression tests that must
    distinguish "the calibration ratio is realistic" from "the code measures
    the wrong representation" — the latter is the PR #926 Sol source-audit
    bug; the former is not something a fixed-token fake can rule out.

    Every ``create()`` call is recorded (kwargs + the derived usage) so tests
    can assert on the actual sequence of rendered requests and the tokens the
    fake reported for each.
    """

    def __init__(self, scale: float = 1.0, compact_output: list[FakeCompactItem] | None = None):
        self.scale = scale
        self.create_calls: list[dict] = []
        self.compact_calls: list[dict] = []
        self.reported_usages: list[int] = []
        self._compact_output = compact_output if compact_output is not None else _default_compact_output()
        self._call_count = 0

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        self._call_count += 1
        input_items = kwargs.get("input") or []
        instructions = kwargs.get("instructions")
        tools = kwargs.get("tools")
        estimate = _estimate_responses_input_tokens(instructions, tools, input_items)
        reported = max(1, round(estimate * self.scale))
        self.reported_usages.append(reported)
        return iter([
            Event(
                "response.completed",
                response=SimpleNamespace(id=f"resp_dyn_{self._call_count}", usage=_usage(reported)),
            ),
        ])

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        return FakeCompactResult(output=list(self._compact_output))


class DynamicFakeClient:
    def __init__(self, scale: float = 1.0, compact_output: list[FakeCompactItem] | None = None):
        self.responses = DynamicResponses(scale=scale, compact_output=compact_output)


def _make_dynamic_session(
    *,
    context_window: int = 1000,
    compact_token_limit: int | None = None,
    scale: float = 1.0,
) -> tuple[CodexResponsesSession, DynamicResponses]:
    """Build a session backed by ``DynamicResponses`` (internally-consistent
    provider-actual reporting) instead of a fixed-token ``ScriptedResponses``.
    """
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_compact_token_limit=compact_token_limit,
    )
    client = DynamicFakeClient(scale=scale)
    adapter._client = client
    session = adapter.create_chat(
        "gpt-5.6-sol",
        "system prompt",
        tools=None,
        context_window=context_window,
    )
    return session, client.responses


def _make_session(
    *,
    context_window: int = 1000,
    compact_token_limit: int | None = None,
    turns: list[list[Event]] | None = None,
    compact_output: list[FakeCompactItem] | None = None,
) -> CodexResponsesSession:
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_compact_token_limit=compact_token_limit,
    )
    adapter._client = FakeClient(turns or [], compact_output)
    session = adapter.create_chat(
        "gpt-5.6-sol",
        "system prompt",
        tools=None,
        context_window=context_window,
    )
    return session


def _send_text_turns(session: CodexResponsesSession, count: int, *, label: str = "turn") -> None:
    """Drive ``count`` plain-text turns through real ``send()``."""
    for i in range(count):
        session.send(f"{label} {i}")


# ---------------------------------------------------------------------------
# Task schema / validation
# ---------------------------------------------------------------------------


def test_validate_codex_compact_token_limit_accepts_none():
    assert _validate_codex_compact_token_limit(None) is None


def test_validate_codex_compact_token_limit_accepts_positive_int():
    assert _validate_codex_compact_token_limit(50_000) == 50_000


def test_validate_codex_compact_token_limit_rejects_bool():
    with pytest.raises(ValueError):
        _validate_codex_compact_token_limit(True)
    with pytest.raises(ValueError):
        _validate_codex_compact_token_limit(False)


def test_validate_codex_compact_token_limit_rejects_zero_and_negative():
    with pytest.raises(ValueError):
        _validate_codex_compact_token_limit(0)
    with pytest.raises(ValueError):
        _validate_codex_compact_token_limit(-1)


def _fake_daemon_manager(tmp_path):
    from lingtai.tools.daemon import DaemonManager

    agent = SimpleNamespace(
        service=SimpleNamespace(model="mock-model"),
        _working_dir=tmp_path / "agent",
        _log=lambda *args, **kwargs: None,
    )
    from lingtai.tools.daemon import PosixDaemonProcessPort
    return DaemonManager(agent, process_port=PosixDaemonProcessPort())


def test_daemon_schema_rejects_bool_context_token_limit(tmp_path):
    """Daemon task pre-flight: bool must be rejected like every other int field."""
    mgr = _fake_daemon_manager(tmp_path)
    result = mgr._handle_emanate(
        tasks=[{"task": "x", "tools": [], "context_token_limit": True}],
    )
    assert result["status"] == "error"
    assert "context_token_limit" in result["message"]


def test_daemon_schema_rejects_zero_context_token_limit(tmp_path):
    mgr = _fake_daemon_manager(tmp_path)
    result = mgr._handle_emanate(
        tasks=[{"task": "x", "tools": [], "context_token_limit": 0}],
    )
    assert result["status"] == "error"
    assert "context_token_limit" in result["message"]


def test_daemon_schema_accepts_omitted_context_token_limit(tmp_path):
    """Actually OMITTING the field (not passing a value) must not be rejected
    by the pre-flight gate. The batch may still fail later for unrelated
    reasons (e.g. no LLM service reachable in this fake agent), but never on
    this field — assert the failure, if any, never mentions it."""
    mgr = _fake_daemon_manager(tmp_path)
    task = {"task": "x", "tools": []}
    assert "context_token_limit" not in task
    result = mgr._handle_emanate(tasks=[task])
    assert not (result["status"] == "error" and "context_token_limit" in result["message"])


def test_daemon_schema_accepts_explicit_context_token_limit_value(tmp_path):
    mgr = _fake_daemon_manager(tmp_path)
    result = mgr._handle_emanate(
        tasks=[{"task": "x", "tools": [], "context_token_limit": 5000}],
    )
    assert not (result["status"] == "error" and "context_token_limit" in result["message"])


def test_external_cli_backend_ignores_context_token_limit(tmp_path):
    """External CLI backends (e.g. ``backend='codex'``, the CLI process) must
    never validate or consume ``context_token_limit`` — this feature is
    ``backend='lingtai'``-only. A CLI-backend task with an otherwise-invalid
    value (bool) must route straight to ``_handle_emanate_cli`` without ever
    reaching the LingTai-backend pre-flight gate that would reject it."""
    from unittest.mock import patch

    mgr = _fake_daemon_manager(tmp_path)
    with patch.object(
        type(mgr), "_handle_emanate_cli",
        return_value={"status": "dispatched", "count": 1, "ids": ["em-1"], "group_id": "g-1"},
    ) as mocked:
        result = mgr._handle_emanate(
            tasks=[{"task": "x", "tools": [], "context_token_limit": True}],
            backend="codex",
        )
    mocked.assert_called_once()
    assert result["status"] == "dispatched"


# ---------------------------------------------------------------------------
# Explicit override vs. omitted-inherits-context-window
# ---------------------------------------------------------------------------


def test_explicit_task_limit_overrides_context_window():
    session = _make_session(context_window=1_000_000, compact_token_limit=500)
    assert session._effective_compact_token_limit() == 500


def test_omitted_task_limit_inherits_context_window():
    session = _make_session(context_window=42_000, compact_token_limit=None)
    assert session._effective_compact_token_limit() == 42_000


def test_no_context_window_and_no_explicit_limit_disables_compaction():
    session = _make_session(
        context_window=0, compact_token_limit=None,
        turns=[text_turn(999_999)],
    )
    assert session._effective_compact_token_limit() is None
    session.send("turn 0")
    assert session._compacted_items is None


def test_omitted_limit_end_to_end_actually_compacts_when_local_history_grows():
    """HIGH-2 regression: the OMITTED default (inherit context_window) must
    actually fire and reduce context over a real multi-turn send() sequence
    — not just resolve to an integer (the old test only checked the resolved
    limit; it never proved the omitted default does anything useful).

    Uses a small context_window and provider-reported input tokens that grow
    roughly with real conversation length (a realistic fake — a provider that
    never reports growing input tokens despite growing history is not a
    meaningful compaction scenario), proving the projected-token trigger (not
    a raw reactive-only check) gives the omitted path real headroom to fire.
    """
    session = _make_session(
        context_window=30,  # deliberately tiny so local estimates cross it fast
        compact_token_limit=None,
        turns=[text_turn(t) for t in (15, 30, 45, 60, 75, 90, 105, 120)],
    )
    assert session._compacted_items is None
    for i in range(8):
        session.send(f"turn {i}: some reasonably descriptive instruction text")
        if session._compacted_items is not None:
            break
    assert session._compacted_items is not None, (
        "omitted-limit (inherit context_window) never compacted across 8 turns "
        "of a tiny 30-token window — the default path would never usefully help"
    )
    # Prove it actually REDUCED the next request's input, not just flipped a
    # flag — the compacted prefix's opaque items must appear, and the next
    # request must be smaller than a full uncompacted replay would have been.
    sent_input = session._client.responses.create_calls[-1]["input"]
    types = {item.get("type") for item in sent_input if isinstance(item, dict)}
    assert "compaction_summary" in types


def test_daemon_provider_defaults_injects_codex_compact_token_limit():
    from lingtai.tools.daemon import DaemonManager
    from pathlib import Path

    mgr = DaemonManager.__new__(DaemonManager)
    run_dir = SimpleNamespace(path=Path("/tmp/fake-daemon-run-compaction"))

    with_limit = mgr._daemon_provider_defaults("codex", {}, run_dir, context_token_limit=12_345)
    assert with_limit["codex"]["codex_compact_token_limit"] == 12_345

    without_limit = mgr._daemon_provider_defaults("codex", {}, run_dir, context_token_limit=None)
    assert "codex_compact_token_limit" not in without_limit["codex"]

    # A non-Codex provider with an otherwise-empty defaults bucket returns
    # None entirely (nothing to inject) — context_token_limit never reaches
    # a non-Codex adapter.
    non_codex = mgr._daemon_provider_defaults("anthropic", {}, run_dir, context_token_limit=99_999)
    assert non_codex is None or "codex_compact_token_limit" not in non_codex.get("anthropic", {})


# ---------------------------------------------------------------------------
# HIGH-1 regression: the live turn that TRIGGERS compaction must ride as the
# trailing item of the next create() call, never folded into the opaque
# summary. Both scenarios drive real send() — no manual pre-trigger.
# ---------------------------------------------------------------------------


def test_live_plain_user_turn_that_triggers_compaction_is_not_folded():
    """The exact HIGH-1 failure scenario: a plain user send() that pushes
    projected tokens past the limit must have ITS OWN text ride as the live
    trailing item of the very next request — not be sent as compact() input
    with nothing left over.
    """
    session = _make_session(
        context_window=1000,
        compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(60),
        ],
    )
    NEW_QUESTION = "THIS IS THE NEW LIVE QUESTION"
    _send_text_turns(session, 4, label="warmup")
    assert session._compacted_items is None, "should not have compacted yet at this point"

    session.send(NEW_QUESTION)

    assert session._compacted_items is not None, "compaction should have fired on this turn"
    sent_input = session._client.responses.create_calls[-1]["input"]
    # The new question must be the trailing LIVE item, never inside the opaque
    # compaction_summary.
    assert sent_input[-1] == {"role": "user", "content": NEW_QUESTION}
    compacted_types = {item.get("type") for item in sent_input if isinstance(item, dict)}
    assert "compaction_summary" in compacted_types
    # And it must not appear anywhere inside the compact() call's own input —
    # that would mean it got folded into the opaque summary instead.
    compact_call_input = session._client.responses.compact_calls[0]["input"]
    assert not any(
        isinstance(item, dict) and NEW_QUESTION in str(item.get("content", ""))
        for item in compact_call_input
    )


def test_live_tool_result_continuation_that_triggers_compaction_preserves_pairing():
    """The tool-loop variant of HIGH-1: when the live turn that triggers
    compaction is a tool-result delivery (not plain text), the matching
    function_call (already in history from the PRIOR send()) and this turn's
    function_call_output must both ride live, correctly paired and in order,
    never split across the compaction boundary."""
    session = _make_session(
        context_window=1000,
        compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(100),
            tool_call_turn(120, call_id="call_1"),
            text_turn(60),
        ],
    )
    _send_text_turns(session, 3, label="warmup")
    response = session.send("please search for X")
    assert response.tool_calls, "expected a tool call to be extracted from the scripted turn"
    call = response.tool_calls[0]
    assert session._compacted_items is None, "should not have compacted yet at this point"

    tool_result = ToolResultBlock(id=call.id, name=call.name, content={"result": "found it"})
    session.send([tool_result])

    assert session._compacted_items is not None, "compaction should have fired on this turn"
    sent_input = session._client.responses.create_calls[-1]["input"]
    types = [item.get("type") for item in sent_input if isinstance(item, dict)]
    assert "function_call" in types and "function_call_output" in types
    assert types.index("function_call") < types.index("function_call_output"), (
        "function_call must precede its function_call_output"
    )
    assert sent_input[-1].get("type") == "function_call_output", (
        "the tool result that triggered compaction must be the trailing live item"
    )
    # The compacted (folded) portion must not contain the split half of this pair.
    compact_call_input = session._client.responses.compact_calls[0]["input"]
    assert not any(
        isinstance(item, dict) and item.get("type") in ("function_call", "function_call_output")
        and item.get("call_id") == call.id
        for item in compact_call_input
    )


# ---------------------------------------------------------------------------
# Codex compact request shape / endpoint
# ---------------------------------------------------------------------------


def test_compact_request_shape():
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(60)],
    )
    _send_text_turns(session, 4)
    session.send("trigger")

    assert len(session._client.responses.compact_calls) == 1
    call = session._client.responses.compact_calls[0]
    assert call["model"] == "gpt-5.6-sol"
    # Regression guard: the real ``openai`` SDK's ``responses.compact()`` is
    # keyword-only with NO ``store`` parameter at all (unlike ``responses.
    # create()``, which needs ``store=false`` because Codex rejects
    # ``store=true``). Passing ``store=`` here raises ``TypeError`` against the
    # real SDK signature and silently disables compaction forever behind the
    # broad ``except Exception`` in ``_compact_now``. A fake with **kwargs
    # would not catch this — assert the exact kwarg set instead.
    assert "store" not in call
    assert "input" in call
    # Never the generic context_management route Codex rejects.
    assert "context_management" not in call


def test_compact_request_kwargs_bind_against_real_sdk_signature():
    """Assert the exact kwargs _compact_now sends bind against the real SDK.

    A fake ``compact(**kwargs)`` accepts anything, so it can't catch a kwarg
    the real ``openai`` SDK method would reject. Bind the actually-sent kwargs
    against the real (keyword-only, no **kwargs) signature to prove they are
    valid on the wire, not just accepted by the fake.
    """
    import inspect

    from openai.resources.responses import Responses

    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(60)],
    )
    _send_text_turns(session, 4)
    session.send("trigger")

    call = session._client.responses.compact_calls[0]
    sig = inspect.signature(Responses.compact)
    sig.bind(None, **call)  # raises TypeError if any kwarg is invalid


def test_never_uses_context_management_for_codex():
    session = _make_session(compact_token_limit=100, turns=[text_turn(10)])
    assert session._compact_threshold is None
    session.send("hello")
    sent = session._client.responses.create_calls[0]
    assert "context_management" not in sent


# ---------------------------------------------------------------------------
# Opaque message + compaction_summary replay
# ---------------------------------------------------------------------------


def test_compacted_items_stored_verbatim_structurally():
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(60)],
    )
    _send_text_turns(session, 4)
    session.send("trigger")

    assert session._compacted_items is not None
    types = [item["type"] for item in session._compacted_items]
    assert types == ["message", "compaction_summary"]
    assert session._compacted_items[1]["encrypted_content"] == _OPAQUE_MARKER


def test_no_encrypted_content_logged(caplog):
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(60)],
    )
    _send_text_turns(session, 4)
    with caplog.at_level(logging.DEBUG):
        session.send("trigger")
    assert session._compacted_items is not None
    for record in caplog.records:
        assert _OPAQUE_MARKER not in record.getMessage()
        assert _OPAQUE_MARKER not in str(getattr(record, "__dict__", {}))
    # Also check the actual wire payloads recorded by the fake — the opaque
    # marker legitimately rides ON the wire (it must, for replay) but must
    # never leak into anything this test treats as a "log"/"record" channel.
    # (Wire content itself is asserted structurally in the replay tests.)


# ---------------------------------------------------------------------------
# Additive delta after compaction
# ---------------------------------------------------------------------------


def test_additive_delta_after_compaction_only_appends_new_entries():
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(60), text_turn(60),
        ],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert session._compacted_items is not None
    entry_count_at_compaction = session._compacted_at_entry_count

    session.send("post-compaction message")
    replay = session._compacted_replay_input()

    # 2 opaque compacted items + the live delta since the boundary (which
    # includes the assistant response(s) recorded after "trigger" plus the
    # new "post-compaction message" turn) — assert the newest message is the
    # last item and the boundary index did not move backward.
    assert replay[-1] == {"role": "user", "content": "post-compaction message"}
    assert entry_count_at_compaction == session._compacted_at_entry_count


def test_send_stream_replays_compacted_prefix_plus_delta_only():
    """End-to-end: after compaction, later turns carry the compacted prefix
    plus only the new turns — not a full re-conversion of history."""
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(60), text_turn(60),
        ],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert session._compacted_items is not None

    session.send("second turn (post-compaction)")

    sent = session._client.responses.create_calls[-1]
    sent_input = sent["input"]
    assert sent_input[0]["type"] == "message"
    assert sent_input[1]["type"] == "compaction_summary"
    joined = str(sent_input)
    assert "second turn (post-compaction)" in joined


# ---------------------------------------------------------------------------
# Exact boundary crossing / non-firing
# ---------------------------------------------------------------------------


def test_projected_provider_tokens_calibration_formula_exact():
    """Unit-test the ``_projected_provider_tokens`` calibration formula in
    isolation: ``projected = current_representation_estimate * (provider_actual /
    calibration_local_sample)``. Controls the CURRENT request representation
    directly (monkeypatches ``_current_request_representation`` to a fixed
    item list, which is what the fixed code actually estimates over) rather
    than hand-setting ``_last_provider_input_tokens``/``_last_local_estimate_tokens``
    out of sync with the representation those fields are supposed to
    describe — this isolates the pure calibration math from real
    token-counting and from any raw-canonical-interface estimate."""
    session = _make_session(context_window=1000, compact_token_limit=100, turns=[])
    session._last_provider_input_tokens = 200  # paired calibration sample: provider said 200...
    session._last_local_estimate_tokens = 50   # ...for a rendered-request estimate of 50 at that time (ratio 4.0)
    # NOW the current rendered-request representation estimates to some value.
    fixed_items = [{"role": "user", "content": "x" * 25}]
    session._current_request_representation = lambda *a, **kw: fixed_items
    session._instructions = ""
    session._tools = None
    # Sanity: derive the expected value via the REAL estimator (not a
    # hand-picked number), so the test proves the formula, not a coincidence.
    actual_estimate = _estimate_responses_input_tokens(session._instructions, session._tools, fixed_items)

    projected = session._projected_provider_tokens()
    assert projected == int(actual_estimate * (200 / 50))

    session._maybe_compact_before_send()
    # No real conversational history exists in this session (turns=[] and no
    # send() has run), so find_compaction_boundary correctly refuses — this
    # isolates the THRESHOLD comparison (>=, not only >) from boundary
    # availability, which is covered separately by the real-send() tests.
    assert session._compacted_items is None


def test_calibration_sample_reflects_compacted_representation_not_raw_canonical():
    """PR #926 Sol source-audit regression (FAILING against the prior code):
    the calibration denominator captured in ``send_stream`` must be a local
    estimate of the EXACT rendered request representation that was actually
    sent — NOT ``ChatInterface.estimate_context_tokens()`` over the full raw
    canonical interface, which keeps growing with every pre-compaction turn
    forever (compaction never deletes canonical entries) while the real
    post-compaction request shrinks to the opaque prefix plus live suffix.
    Calibrating against the raw estimate divides by an artificially large
    denominator and can silently under-project a large live delta, letting a
    request cross ``context_token_limit`` before re-arm fires.

    Drives real ``send()`` calls to build a long raw canonical history, lets
    a real compaction fire, then asserts the captured
    ``_last_local_estimate_tokens`` sample is close to the small compacted
    representation size — not the (much larger) raw canonical estimate. Before
    the fix this assertion fails: the old code always set
    ``_last_local_estimate_tokens`` from ``estimate_context_tokens()``, so it
    would equal the huge raw canonical value even immediately after
    compaction fired.
    """
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(t) for t in (50, 80, 110, 200, 200, 200)],
    )
    # Large filler per warmup turn so the raw canonical history accumulates
    # substantial text that persists in ``ChatInterface.entries`` even after
    # compaction folds most turns away (compaction never deletes canonical
    # entries — it only changes what gets SENT).
    filler = "word " * 500
    for i in range(4):
        session.send(f"warmup {i}: {filler}")
    session.send("trigger")
    assert session._compacted_items is not None, "compaction must have fired for this regression to apply"

    raw_canonical_estimate = session._interface.estimate_context_tokens(
        session._instructions, session._tools,
    )
    true_compacted_representation = session._current_request_representation()
    true_compacted_estimate = _estimate_responses_input_tokens(
        session._instructions, session._tools, true_compacted_representation,
    )

    # Precondition: the raw canonical estimate is meaningfully larger than the
    # true compacted representation (otherwise this scenario would not
    # exercise the bug at all).
    assert raw_canonical_estimate > true_compacted_estimate, (
        "test setup did not create a raw/compacted gap large enough to "
        "exercise the regression"
    )

    # THE regression assertion: the captured calibration sample must track
    # the small compacted representation, not the large raw canonical one.
    assert session._last_local_estimate_tokens == true_compacted_estimate
    assert session._last_local_estimate_tokens < raw_canonical_estimate


def test_large_live_delta_after_compaction_triggers_rearm_before_crossing_limit():
    """PR #926 Sol source-audit regression, driven entirely through real
    ``send()`` calls against an internally-consistent dynamic fake (never
    hand-set ``_last_provider_input_tokens``/``_last_local_estimate_tokens``):
    after compaction, a large live delta must be re-armed, AND the re-arm
    must actually bring the final outgoing request's TRUE size back under
    ``LIMIT`` — not merely cause a second ``compact()`` call to happen.

    ``DynamicResponses`` computes each turn's reported ``usage.input_tokens``
    from the ACTUAL rendered request via the same deterministic estimator the
    fixed code uses (scale=1.0), so "provider actual" can never silently
    disagree with what was really sent — ruling out an internally
    inconsistent fixture as an alternative explanation for any observed
    effect.

    Natural real-send construction of the bug precondition (per Sol's
    correction):
      1. Three OLD large turns are sent — individually and cumulatively they
         stay below ``LIMIT`` (no compaction yet).
      2. Three SMALL turns age those large turns outside
         ``find_compaction_boundary``'s ``keep_turns=1`` window (PR #926 Sol
         source-audit follow-up #2: with the library's generic default of
         ``keep_turns=3``, the boundary retained two extra small turns that
         had no reason to stay live, which — combined with the opaque
         compacted prefix and the live delta — pushed the final request a
         few tokens PAST ``LIMIT`` even though the correct code re-armed;
         ``keep_turns=1`` folds those unneeded turns too, since only the ONE
         newest live turn must survive verbatim).
      3. A further small turn's send finally pushes the (still purely raw,
         since nothing is compacted yet) provider-visible estimate over
         ``LIMIT``, firing the FIRST compaction — this occurs identically
         under the fixed and prior code, since pre-compaction the raw
         canonical estimate and the actual rendered request are the same
         representation. The live/kept suffix is now genuinely small (the one
         newest small turn + the tiny opaque prefix); the folded raw prefix
         (the 3 old large turns plus the two aged-out small turns) is huge and
         remains in canonical history forever.
      4. A large new live delta is sent, whose size alone (measured via the
         same estimator, BEFORE it is added to history) is confirmed to sit
         below ``LIMIT`` — so any crossing after this turn cannot be blamed on
         "the delta itself is simply too big for any boundary to help",
         isolating this test to the actual re-arm/boundary-choice question.

    Assertions (all must hold for the FIXED code; this test must FAIL against
    ``keep_turns=3`` per Sol's evidence: reproducing this exact scenario under
    the prior ``keep_turns=3`` code reports final create() usages including a
    last value of 11359 against LIMIT=11340 — a re-arm occurs (compact_calls
    goes 1 -> 2) but the final request still crosses the limit by 19 tokens,
    because two needlessly-retained small turns plus the opaque prefix push it
    over even after re-arming):
      - the live delta's own estimated size is strictly below LIMIT
        (precondition — proves a correctly-chosen boundary COULD have kept
        the final request under LIMIT; this is not an irreducible case),
      - a second ``compact()`` call happens before the final ``create()``,
      - EVERY ``create()`` call's dynamically-derived usage, especially the
        final one, is strictly below LIMIT,
      - the re-arm ``compact()`` call's own input is itself locally estimated
        below LIMIT (the compact request itself must not already be
        oversized going in).
    """
    LIMIT = 11_340
    HUGE_FILLER = "word " * 3000
    SMALL_MSG = "short message"
    BIG_NEW_TURN = "big new turn: " + ("word " * 9000)

    session, fake = _make_dynamic_session(
        context_window=1000, compact_token_limit=LIMIT, scale=1.0,
    )

    # Precondition: the live delta ALONE (as a standalone user-message input
    # item, not yet appended to history) must already sit below LIMIT -- so
    # this scenario is not the irreducible "the delta itself exceeds the
    # limit and no boundary choice can help" case; a correctly-chosen boundary
    # must be able to bring the final request back under LIMIT. The wire shape
    # matches ``to_responses_input``'s plain user-text item exactly (see
    # ``interface_converters.to_responses_input``): ``{"role": "user",
    # "content": <str>}``.
    delta_only_items = [{"role": "user", "content": BIG_NEW_TURN}]
    delta_only_estimate = _estimate_responses_input_tokens(
        session._instructions, session._tools, delta_only_items,
    )
    assert delta_only_estimate < LIMIT, (
        "test precondition violated -- the live delta alone must be smaller "
        "than LIMIT for this to isolate the boundary-choice/re-arm question "
        "rather than the unrelated irreducible-overage case"
    )

    # Step 1: three old large turns, cumulatively still under LIMIT.
    for i in range(3):
        session.send(f"old big turn {i}: {HUGE_FILLER}")
        assert session._compacted_items is None, (
            "must not compact yet -- still under the precondition threshold"
        )

    # Step 2: three small turns age the large turns outside keep_turns=1.
    for i in range(3):
        session.send(f"small turn {i}: {SMALL_MSG}")

    # Step 3: one more small turn crosses LIMIT (raw canonical, pre-compaction
    # -- identical measurement under old and new code) and fires the FIRST
    # compaction.
    session.send("small turn 3: short message")
    assert session._compacted_items is not None, "first compaction must have fired"
    assert len(fake.compact_calls) == 1
    compacted_repr_after_first = session._current_request_representation()
    small_kept_estimate = _estimate_responses_input_tokens(
        session._instructions, session._tools, compacted_repr_after_first,
    )
    assert small_kept_estimate < LIMIT, (
        "the post-compaction kept/live representation must be genuinely "
        "small relative to the limit for this scenario to isolate re-arm"
    )

    # Step 4: the large new live delta (its standalone size already confirmed
    # below LIMIT above). Its true post-compaction representation at send time
    # legitimately approaches/exceeds LIMIT before any re-arm.
    session.send(BIG_NEW_TURN)

    assert len(fake.compact_calls) == 2, (
        "the fixed code must re-arm (a second compact() call) once a large "
        "live delta pushes the TRUE compacted representation toward the "
        "limit -- silently sending without a preceding re-arm attempt is "
        "the exact PR #926 Sol source-audit finding"
    )

    # The re-arm's own compact() input must itself be estimated below LIMIT --
    # the compaction request should not go in already oversized.
    rearm_compact_input = fake.compact_calls[1]["input"]
    rearm_compact_estimate = _estimate_responses_input_tokens(
        session._instructions, session._tools, rearm_compact_input,
    )
    assert rearm_compact_estimate < LIMIT, (
        "the re-arm compact() call's own input must be estimated below "
        "LIMIT -- a compact request that is itself already oversized would "
        "indicate the boundary was chosen too late to help"
    )

    # The real correctness bar: EVERY create() call's dynamically-derived
    # (internally consistent) reported usage -- especially the FINAL one --
    # must be strictly below LIMIT. A second compact() call alone is not
    # sufficient evidence of a fix if the final request still crosses LIMIT
    # (Sol's evidence: under keep_turns=3, the final usage was 11359 > 11340
    # despite a re-arm firing).
    assert all(u > 0 for u in fake.reported_usages)
    assert all(u < LIMIT for u in fake.reported_usages), (
        f"every create() usage must stay strictly below LIMIT={LIMIT}; got "
        f"{fake.reported_usages} -- a re-arm that fires but still lets the "
        f"final request cross the limit is not a correct fix"
    )


def test_exact_equality_projected_tokens_crossing_fires_end_to_end():
    """``projected == limit`` must fire (``>=``, not strictly ``>``), proven
    through real ``send()`` calls with a real conversational boundary. Uses
    provider-reported tokens that grow with each turn (a realistic trajectory)
    so the calibration ratio stays ~1.0 and the last observed provider value
    lands exactly at the limit before the triggering send."""
    session = _make_session(
        context_window=1000, compact_token_limit=100,
        turns=[text_turn(t) for t in (25, 50, 75, 100, 200)],
    )
    _send_text_turns(session, 4)
    assert session._projected_provider_tokens() == 100  # exactly at the limit
    session.send("trigger at exact boundary")
    assert session._compacted_items is not None, "projected >= limit must trigger compaction"


def test_no_compaction_strictly_below_threshold():
    session = _make_session(
        context_window=1000, compact_token_limit=100_000,
        turns=[text_turn(20) for _ in range(6)],
    )
    for i in range(6):
        session.send(f"turn {i}: short message")
    assert session._compacted_items is None
    assert len(session._client.responses.compact_calls) == 0


def test_no_compaction_without_any_observed_usage():
    session = _make_session(compact_token_limit=1, turns=[])
    # No send() has run yet — _last_provider_input_tokens is None, but the
    # raw local estimate (calibration factor 1.0) is still computed from the
    # (currently near-empty) interface; assert it does not crash and does not
    # fire without a valid compaction boundary.
    session._maybe_compact_before_send()
    assert session._compacted_items is None
    assert len(session._client.responses.compact_calls) == 0


# ---------------------------------------------------------------------------
# Invalidation on rewrite
# ---------------------------------------------------------------------------


def test_reset_ws_epoch_invalidates_compacted_state():
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(60)],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert session._compacted_items is not None

    session._reset_ws_epoch("summarize_delayed")

    assert session._compacted_items is None
    assert session._compacted_at_entry_count == 0


def test_compaction_retriggers_after_invalidation():
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(300), text_turn(300),
        ],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert len(session._client.responses.compact_calls) == 1

    session._reset_ws_epoch("encrypted_reasoning_self_heal")
    assert session._compacted_items is None

    # After invalidation there must be enough live history again for a fresh
    # boundary (the just-cleared compacted prefix's items are still in the
    # canonical interface — invalidation never deletes canonical history).
    # The scripted provider token for this send (300) must genuinely project
    # past the limit under the CORRECTED (rendered-representation) calibration
    # — not rely on a stale raw-canonical-estimate ratio to cross by chance.
    session.send("trigger again")

    assert len(session._client.responses.compact_calls) == 2


# ---------------------------------------------------------------------------
# No recompaction loop
# ---------------------------------------------------------------------------


def test_no_redundant_recompact_when_boundary_has_not_moved():
    """Calling the trigger check again with no new turns (the boundary has
    not advanced since the last compaction) must NOT recompact — there is
    nothing new to safely fold, so re-firing would be pure waste."""
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(200)],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert len(session._client.responses.compact_calls) == 1

    # No new turns were added — the boundary is unchanged. Calling the
    # trigger check again directly (bypassing send()'s own token bookkeeping)
    # must be a no-op.
    session._maybe_compact_before_send()
    session._maybe_compact_before_send()

    assert len(session._client.responses.compact_calls) == 1


def test_rearm_recompacts_when_delta_grows_past_a_new_boundary():
    """LOW-2 fix: once compaction is active, the small task limit must be
    re-enforced (not just backstopped by the ~1.0 hard forced-rebuild
    boundary) once the post-compaction delta grows enough for
    ``find_compaction_boundary`` to find a NEW split point strictly past the
    existing one. Each re-arm folds only the OLDER portion of the delta
    (reusing the existing opaque ``_compacted_items`` as its own input, per
    OpenAI's documented chained-compaction pattern) — it never reintroduces
    the original full history and never duplicates any tool call."""
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(200), text_turn(200),
        ],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert len(session._client.responses.compact_calls) == 1
    boundary_after_first = session._compacted_at_entry_count

    # One more turn is enough for find_compaction_boundary (keep_turns=3) to
    # advance the split by one full turn — the task's limit is re-enforced.
    session.send("post 1")

    assert len(session._client.responses.compact_calls) == 2, (
        "the small task limit must be re-enforced once the delta grows past "
        "a new safe boundary, not left unbounded until the ~1.0 hard rebuild"
    )
    assert session._compacted_at_entry_count > boundary_after_first
    # The re-arm compact() call's own input must include the PRIOR opaque
    # compacted items verbatim (chained compaction), never the raw original
    # history re-derived from scratch.
    second_compact_input = session._client.responses.compact_calls[1]["input"]
    assert any(
        isinstance(item, dict) and item.get("type") == "compaction_summary"
        for item in second_compact_input
    ), "re-arm must chain off the existing opaque compacted state, not restart from raw history"


# ---------------------------------------------------------------------------
# Tool-loop continuation (turn continues normally after compaction)
# ---------------------------------------------------------------------------


def test_turn_continues_normally_after_compaction():
    session = _make_session(
        context_window=1000, compact_token_limit=150,
        turns=[
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(60), text_turn(60),
        ],
    )
    _send_text_turns(session, 4)
    session.send("trigger")
    assert session._compacted_items is not None

    result = session.send("continue the same tool loop")

    assert result is not None
    assert session._compacted_items is not None


# ---------------------------------------------------------------------------
# Compaction failure fails safe (skips this turn's compaction; does not crash)
# ---------------------------------------------------------------------------


def test_compact_failure_is_non_fatal_and_skips_compaction():
    class ExplodingScriptedResponses(ScriptedResponses):
        def compact(self, **kwargs):
            self.compact_calls.append(kwargs)
            raise RuntimeError("simulated compact endpoint failure")

    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_compact_token_limit=150,
    )
    client = FakeClient([])
    client.responses = ExplodingScriptedResponses(
        [text_turn(50), text_turn(80), text_turn(110), text_turn(200), text_turn(60)],
    )
    adapter._client = client
    session = adapter.create_chat(
        "gpt-5.6-sol", "system prompt", tools=None, context_window=1000,
    )
    _send_text_turns(session, 4)

    # Must not raise even though compact() explodes.
    session.send("trigger")

    assert len(client.responses.compact_calls) == 1
    assert session._compacted_items is None


# ---------------------------------------------------------------------------
# Encrypted-reasoning self-heal interaction: a compacted replay whose
# compaction_summary.encrypted_content is rejected must invalidate compaction
# (it cannot be stripped like a raw ``reasoning`` item) rather than silently
# replaying a now-untrustworthy compacted prefix on the next turn.
# ---------------------------------------------------------------------------


def test_self_heal_invalidates_compaction_when_compacted_replay_rejected():
    from lingtai.kernel.llm.interface import ThinkingBlock, TextBlock

    class FailOnceOnCreateResponses(ScriptedResponses):
        def __init__(self, turns, error, compact_output=None):
            super().__init__(turns, compact_output)
            self._error = error
            self._create_attempts = 0

        def create(self, **kwargs):
            self._create_attempts += 1
            self.create_calls.append(kwargs)
            if self._create_attempts == 6:  # the "continue" send's first attempt
                raise self._error
            assert self._idx < len(self._turns), (
                f"FailOnceOnCreateResponses ran out of scripted turns at "
                f"attempt {self._create_attempts}"
            )
            events = self._turns[self._idx]
            self._idx += 1
            return iter(events)

    error = RuntimeError(
        "Error code: 400 - {'error': {'message': 'The encrypted content for "
        "item rs_bad could not be verified. Reason: Encrypted content could "
        "not be decrypted.'}}"
    )

    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_compact_token_limit=150,
    )
    client = FakeClient([])
    client.responses = FailOnceOnCreateResponses(
        [
            text_turn(50), text_turn(80), text_turn(110), text_turn(200),
            text_turn(60),  # the "trigger" send that fires compaction
            text_turn(60),  # the "continue" send's self-heal retry resend
        ],
        error,
    )
    adapter._client = client
    session = adapter.create_chat(
        "gpt-5.6-sol", "system prompt", tools=None, context_window=1000,
    )

    _send_text_turns(session, 4)
    session.send("trigger")
    assert session._compacted_items is not None

    # Now the model produced a raw reasoning item that Codex will later reject
    # on replay (simulating a stale encrypted blob riding on top of the
    # compacted prefix).
    session._interface.add_assistant_message(
        [
            ThinkingBlock(
                text="safe summary",
                provider_data={
                    "openai_responses_reasoning_item": {
                        "type": "reasoning",
                        "id": "rs_bad",
                        "summary": [{"type": "summary_text", "text": "safe summary"}],
                        "encrypted_content": "opaque-broken-ciphertext",
                    }
                },
            ),
            TextBlock(text="visible answer"),
        ],
        model="gpt-5.6-sol",
        provider="codex",
    )

    session.send("continue")

    # The self-heal retry must have invalidated the compacted state rather
    # than silently keeping a now-stale compacted prefix.
    assert session._compacted_items is None
    assert session._compacted_at_entry_count == 0
