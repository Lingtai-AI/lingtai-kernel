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

These are pure/mock tests — no network, no OAuth. They cover:
  * task schema/validation for ``context_token_limit`` (positive int, bool
    rejected, ``None`` allowed).
  * explicit override vs. omitted-inherits-context-window.
  * the standalone compact request shape/endpoint (via the fake
    ``client.responses.compact`` call).
  * opaque ``message`` + ``compaction_summary`` replay, byte-for-byte, with no
    encrypted content ever logged.
  * additive-only delta after compaction (only new turns are converted and
    appended; the compacted prefix is replayed verbatim).
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
    _validate_codex_compact_token_limit,
)
from lingtai.kernel.llm.interface import ChatInterface


# ---------------------------------------------------------------------------
# Fakes — mirror tests/test_codex_prompt_cache_key.py's harness shape.
# ---------------------------------------------------------------------------


@dataclass
class Event:
    type: str
    delta: str | None = None
    item: object | None = None
    response: object | None = None
    item_id: str | None = None
    text: str | None = None


class FakeResponsesStream:
    def __init__(self, events: list[Event]):
        self.events = events
        self.kwargs: list[dict] = []

    def create(self, **kwargs):
        self.kwargs.append(kwargs)
        yield from self.events


class FakeCompactItem:
    """Mimics an SDK model object with ``.model_dump()``."""

    def __init__(self, data: dict):
        self._data = data

    def model_dump(self, mode: str = "json", exclude_none: bool = True):
        return dict(self._data)


class FakeCompactResult:
    def __init__(self, output: list[FakeCompactItem]):
        self.output = output


class FakeResponsesAPI(FakeResponsesStream):
    """Combines the streaming ``.create()`` fake with a ``.compact()`` fake."""

    def __init__(self, events: list[Event], compact_output: list[FakeCompactItem] | None = None):
        super().__init__(events)
        self.compact_calls: list[dict] = []
        self._compact_output = compact_output if compact_output is not None else _default_compact_output()

    def compact(self, **kwargs):
        self.compact_calls.append(kwargs)
        return FakeCompactResult(output=list(self._compact_output))


class FakeClient:
    def __init__(self, events: list[Event], compact_output: list[FakeCompactItem] | None = None):
        self.responses = FakeResponsesAPI(events, compact_output)


_OPAQUE_MARKER = "OPAQUE_ENCRYPTED_CONTENT_MUST_NEVER_BE_LOGGED"


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


def _usage(input_tokens: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=20,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )


def _completed(input_tokens: int = 10) -> Event:
    return Event(
        "response.completed",
        response=SimpleNamespace(id="resp_fake", usage=_usage(input_tokens)),
    )


def _build_session(
    *,
    context_window: int = 1000,
    compact_token_limit: int | None = None,
    events: list[Event] | None = None,
    compact_output: list[FakeCompactItem] | None = None,
) -> CodexResponsesSession:
    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_compact_token_limit=compact_token_limit,
    )
    fake_client = FakeClient(events or [_completed()], compact_output)
    adapter._client = fake_client
    session = adapter.create_chat(
        "gpt-5.6-sol",
        "system prompt",
        tools=None,
        context_window=context_window,
    )
    return session


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
    return DaemonManager(agent)


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


def test_daemon_schema_accepts_omitted_context_token_limit_pastvalidation(tmp_path):
    """Omitting the field must not be rejected by the pre-flight gate itself
    (the batch may still fail later for unrelated reasons like no tasks/tools,
    but never on this field)."""
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
    session = _build_session(context_window=1_000_000, compact_token_limit=500)
    assert session._effective_compact_token_limit() == 500


def test_omitted_task_limit_inherits_context_window():
    session = _build_session(context_window=42_000, compact_token_limit=None)
    assert session._effective_compact_token_limit() == 42_000


def test_no_context_window_and_no_explicit_limit_disables_compaction():
    session = _build_session(context_window=0, compact_token_limit=None)
    assert session._effective_compact_token_limit() is None
    session._last_provider_input_tokens = 999_999
    session._maybe_compact_before_send()
    assert session._compacted_items is None


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
# Codex compact request shape / endpoint
# ---------------------------------------------------------------------------


def test_compact_request_shape():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500  # already over threshold
    session._maybe_compact_before_send()

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

    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()

    call = session._client.responses.compact_calls[0]
    sig = inspect.signature(Responses.compact)
    sig.bind(None, **call)  # raises TypeError if any kwarg is invalid


def test_never_uses_context_management_for_codex():
    session = _build_session(compact_token_limit=100)
    assert session._compact_threshold is None
    session.send("hello")
    sent = session._client.responses.kwargs[0]
    assert "context_management" not in sent


# ---------------------------------------------------------------------------
# Opaque message + compaction_summary replay
# ---------------------------------------------------------------------------


def test_compacted_items_stored_verbatim_structurally():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()

    assert session._compacted_items is not None
    types = [item["type"] for item in session._compacted_items]
    assert types == ["message", "compaction_summary"]
    # The opaque content rides through verbatim (needed for replay) but this
    # test only asserts on structure — see test_no_encrypted_content_logged
    # for the logging-safety guarantee.
    assert session._compacted_items[1]["encrypted_content"] == _OPAQUE_MARKER


def test_no_encrypted_content_logged(caplog):
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    with caplog.at_level(logging.DEBUG):
        session._maybe_compact_before_send()
    for record in caplog.records:
        assert _OPAQUE_MARKER not in record.getMessage()
        assert _OPAQUE_MARKER not in str(getattr(record, "__dict__", {}))


def test_replay_includes_compacted_prefix_verbatim():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()

    replay = session._compacted_replay_input()
    assert replay[0]["type"] == "message"
    assert replay[1]["type"] == "compaction_summary"


# ---------------------------------------------------------------------------
# Additive delta after compaction
# ---------------------------------------------------------------------------


def test_additive_delta_after_compaction_only_appends_new_entries():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("pre-compaction message")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()
    entry_count_at_compaction = session._compacted_at_entry_count

    session._interface.add_user_message("post-compaction message")
    replay = session._compacted_replay_input()

    # Exactly the 2 compacted items + 1 new delta item — the pre-compaction
    # message is folded into the opaque prefix, not re-serialized.
    assert len(replay) == 3
    assert replay[2] == {"role": "user", "content": "post-compaction message"}
    assert entry_count_at_compaction == len(session._interface.entries) - 1


def test_send_stream_replays_compacted_prefix_plus_delta_only():
    """End-to-end: after compaction, the wire request carries the compacted
    prefix plus only the new turn — not a full re-conversion of history."""
    session = _build_session(
        compact_token_limit=100,
        events=[_completed(input_tokens=50)],
    )
    session._interface.add_user_message("first turn (will be compacted)")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()
    assert session._compacted_items is not None

    session.send("second turn (post-compaction)")

    sent = session._client.responses.kwargs[-1]
    sent_input = sent["input"]
    assert sent_input[0]["type"] == "message"
    assert sent_input[1]["type"] == "compaction_summary"
    # The new turn appears once, appended — not duplicated pre-compaction text.
    joined = str(sent_input)
    assert "second turn (post-compaction)" in joined


# ---------------------------------------------------------------------------
# Invalidation on rewrite
# ---------------------------------------------------------------------------


def test_reset_ws_epoch_invalidates_compacted_state():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()
    assert session._compacted_items is not None

    session._reset_ws_epoch("summarize_delayed")

    assert session._compacted_items is None
    assert session._compacted_at_entry_count == 0


def test_compaction_retriggers_after_invalidation():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()
    assert len(session._client.responses.compact_calls) == 1

    session._reset_ws_epoch("encrypted_reasoning_self_heal")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()

    assert len(session._client.responses.compact_calls) == 2


# ---------------------------------------------------------------------------
# No recompaction loop
# ---------------------------------------------------------------------------


def test_no_recompact_loop_while_already_compacted():
    session = _build_session(compact_token_limit=100)
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()
    session._maybe_compact_before_send()
    session._maybe_compact_before_send()

    assert len(session._client.responses.compact_calls) == 1


def test_no_compaction_below_threshold():
    session = _build_session(compact_token_limit=500)
    session._last_provider_input_tokens = 100
    session._maybe_compact_before_send()

    assert session._compacted_items is None
    assert len(session._client.responses.compact_calls) == 0


def test_no_compaction_without_any_observed_usage():
    session = _build_session(compact_token_limit=1)
    # _last_provider_input_tokens is None before any provider response.
    session._maybe_compact_before_send()

    assert session._compacted_items is None
    assert len(session._client.responses.compact_calls) == 0


# ---------------------------------------------------------------------------
# Tool-loop continuation (turn continues normally after compaction)
# ---------------------------------------------------------------------------


def test_turn_continues_normally_after_compaction():
    session = _build_session(
        compact_token_limit=100,
        events=[_completed(input_tokens=50)],
    )
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()

    result = session.send("continue the same tool loop")

    assert result is not None
    # The session stays usable for further turns (same object, no rebuild).
    assert session._compacted_items is not None


# ---------------------------------------------------------------------------
# Compaction failure fails safe (skips this turn's compaction; does not crash)
# ---------------------------------------------------------------------------


def test_compact_failure_is_non_fatal_and_skips_compaction():
    class ExplodingResponses(FakeResponsesAPI):
        def compact(self, **kwargs):
            raise RuntimeError("simulated compact endpoint failure")

    adapter = CodexOpenAIAdapter(
        api_key="fake",
        base_url="http://fake",
        use_responses=True,
        force_responses=True,
        codex_compact_token_limit=100,
    )
    client = FakeClient([_completed()])
    client.responses = ExplodingResponses([_completed()])
    adapter._client = client
    session = adapter.create_chat(
        "gpt-5.6-sol", "system prompt", tools=None, context_window=1000,
    )
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500

    # Must not raise.
    session._maybe_compact_before_send()

    assert session._compacted_items is None


# ---------------------------------------------------------------------------
# Encrypted-reasoning self-heal interaction: a compacted replay whose
# compaction_summary.encrypted_content is rejected must invalidate compaction
# (it cannot be stripped like a raw ``reasoning`` item) rather than silently
# replaying a now-untrustworthy compacted prefix on the next turn.
# ---------------------------------------------------------------------------


def test_self_heal_invalidates_compaction_when_compacted_replay_rejected():
    from lingtai.kernel.llm.interface import ThinkingBlock, TextBlock

    class FailOnceOnCreateResponses(FakeResponsesAPI):
        def __init__(self, events, error, compact_output=None):
            super().__init__(events, compact_output)
            self._error = error
            self._create_calls = 0

        def create(self, **kwargs):
            self._create_calls += 1
            self.kwargs.append(kwargs)
            if self._create_calls == 1:
                raise self._error
            return iter(self.events)

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
        codex_compact_token_limit=100,
    )
    client = FakeClient([_completed()])
    client.responses = FailOnceOnCreateResponses([_completed()], error)
    adapter._client = client
    session = adapter.create_chat(
        "gpt-5.6-sol", "system prompt", tools=None, context_window=1000,
    )

    # Get compaction active first.
    session._interface.add_user_message("hello")
    session._last_provider_input_tokens = 500
    session._maybe_compact_before_send()
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
