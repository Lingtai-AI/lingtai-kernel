"""Tests for OpenAIChatSession context-overflow auto-recovery.

When a provider returns 400 with context_length_exceeded, the adapter
trims the oldest ~10% of non-system entries and retries — up to
_OVERFLOW_MAX_ROUNDS times — then injects a [kernel] notice telling the
agent to molt soon.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest

from lingtai.llm.openai.adapter import OpenAIChatSession
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)


def _make_raw_response(content="ok"):
    msg = SimpleNamespace(content=content, tool_calls=[])
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(
        choices=[choice],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=5,
            completion_tokens_details=None,
            prompt_tokens_details=None,
        ),
    )


def _make_overflow_error(msg="This model's maximum context length is 128000 tokens"):
    """Construct an openai.BadRequestError mimicking context-length overflow."""
    body = {"error": {"message": msg, "code": "context_length_exceeded"}}
    request = MagicMock()
    response = MagicMock(status_code=400)
    return openai.BadRequestError(message=msg, response=response, body=body)


def _make_session(client, interface=None):
    if interface is None:
        interface = ChatInterface()
        interface.add_system("you are helpful")
    return OpenAIChatSession(
        client=client,
        model="gpt-test",
        interface=interface,
        tools=None,
        tool_choice=None,
        extra_kwargs={},
        client_kwargs={},
    )


def _seed_history(iface: ChatInterface, n_pairs: int = 10) -> None:
    """Add n user/assistant text-only pairs to a fresh interface."""
    for i in range(n_pairs):
        iface.add_user_message(f"q{i}")
        iface.add_assistant_message(
            [TextBlock(text=f"a{i}")],
            model="gpt-test",
            provider="openai",
        )


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detects_canonical_openai_overflow_code():
    err = _make_overflow_error()
    assert OpenAIChatSession._is_context_overflow_error(err) is True


def test_detects_message_only_overflow_compat_provider():
    err = openai.BadRequestError(
        message="prompt is too long for this model's context window",
        response=MagicMock(status_code=400),
        body={"error": {"message": "prompt is too long"}},
    )
    assert OpenAIChatSession._is_context_overflow_error(err) is True


def test_detects_generic_api_error_with_strong_overflow_phrase():
    err = openai.APIError(
        message="provider rejected request: input exceeds the context window",
        request=MagicMock(),
        body={},
    )
    assert OpenAIChatSession._is_context_overflow_error(err) is True


def _make_bad_request_message_only(msg):
    """BadRequestError with no canonical body code — message match only."""
    return openai.BadRequestError(
        message=msg,
        response=MagicMock(status_code=400),
        body={"error": {"message": msg}},
    )


@pytest.mark.parametrize("msg", [
    "Input tokens exceed 200000. The configured limit is 128000 tokens.",
    "input tokens exceed 262144, the configured limit is 65536 tokens",
    "INPUT TOKENS EXCEED 131072 TOKENS; CONFIGURED LIMIT 65536 TOKENS",
])
def test_detects_bad_request_message_only_strong_input_token_limit_phrase(msg):
    """Evidence-backed strong family: exact sanitized wording, a different
    numeric limit, and mixed case — no canonical body code."""
    assert OpenAIChatSession._is_context_overflow_error(
        _make_bad_request_message_only(msg)
    ) is True


@pytest.mark.parametrize("msg", [
    "output tokens exceed the configured limit",
    "input tokens exceed the configured rate limit",
    "requests exceed the configured rate limit",
    "input tokens exceed the configured quota",
    "tokens exceed the configured limit",
    "input tokens exceed 128000",
])
def test_rejects_strong_phrase_adversarial_negatives_bad_request_and_generic(msg):
    """Output-token, quota/rate, and same-prefix rate wording must NOT be
    classified — either class would trigger destructive trim/retry."""
    assert OpenAIChatSession._is_context_overflow_error(
        _make_bad_request_message_only(msg)
    ) is False
    err = openai.APIError(message=msg, request=MagicMock(), body={})
    assert OpenAIChatSession._is_context_overflow_error(err) is False


def test_detects_generic_api_error_strong_input_token_limit_phrase():
    """Sol-preferred contract: the observed exception class is unconfirmed,
    so the same strong family is one additional narrow generic-APIError
    case (never a copy of the broad BadRequest tuple)."""
    err = openai.APIError(
        message="provider rejected request: input tokens exceed 200000, "
                "the configured limit is 128000 tokens",
        request=MagicMock(),
        body={},
    )
    assert OpenAIChatSession._is_context_overflow_error(err) is True


def test_weak_generic_context_text_remains_false():
    err = openai.APIError(
        message="context window service is temporarily unavailable",
        request=MagicMock(),
        body={},
    )
    assert OpenAIChatSession._is_context_overflow_error(err) is False
    err2 = openai.APIError(
        message="the configured limit was updated for your account",
        request=MagicMock(),
        body={},
    )
    assert OpenAIChatSession._is_context_overflow_error(err2) is False


def test_does_not_detect_generic_api_error_for_weak_context_text():
    err = openai.APIError(
        message="context window service is temporarily unavailable",
        request=MagicMock(),
        body={},
    )
    assert OpenAIChatSession._is_context_overflow_error(err) is False


def test_does_not_detect_unrelated_400():
    err = openai.BadRequestError(
        message="invalid tool schema",
        response=MagicMock(status_code=400),
        body={"error": {"message": "invalid tool schema"}},
    )
    assert OpenAIChatSession._is_context_overflow_error(err) is False


def test_does_not_detect_non_bad_request():
    err = RuntimeError("network down")
    assert OpenAIChatSession._is_context_overflow_error(err) is False


def test_does_not_detect_runtime_error_with_strong_overflow_phrase():
    err = RuntimeError("input exceeds the context window")
    assert OpenAIChatSession._is_context_overflow_error(err) is False


# ---------------------------------------------------------------------------
# Trimming
# ---------------------------------------------------------------------------


def test_trim_drops_at_least_one_oldest_entry():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=10)
    session = _make_session(client=MagicMock(), interface=iface)
    pre = len(iface._entries)
    dropped = session._trim_context_one_round()
    assert dropped >= 1
    assert len(iface._entries) == pre - dropped
    assert iface._entries[0].role == "system"  # system preserved


def test_trim_preserves_tool_call_result_pair():
    iface = ChatInterface()
    iface.add_system("sys")
    # Build a layout where the front-half cut would land mid-pair.
    # Many small entries followed by an assistant[tool_call] -> user[tool_result]
    # pair near the cut point.
    for i in range(8):
        iface.add_user_message(f"q{i}")
        iface.add_assistant_message(
            [TextBlock(text=f"a{i}")],
            model="gpt-test",
            provider="openai",
        )
    # Insert a tool_call/result pair early so the 10% cut hits inside it.
    iface.add_assistant_message(
        [ToolCallBlock(id="tc1", name="search", args={})],
        model="gpt-test",
        provider="openai",
    )
    iface.add_tool_results([ToolResultBlock(id="tc1", name="search", content="result")])
    # Tail
    iface.add_user_message("recent")
    iface.add_assistant_message([TextBlock(text="ok")], model="gpt-test", provider="openai")

    session = _make_session(client=MagicMock(), interface=iface)
    session._trim_context_one_round()

    # Verify: every remaining ToolCallBlock id has a matching ToolResultBlock,
    # and every remaining ToolResultBlock id has a matching ToolCallBlock.
    call_ids = set()
    result_ids = set()
    for e in iface._entries:
        for b in e.content:
            if isinstance(b, ToolCallBlock):
                call_ids.add(b.id)
            elif isinstance(b, ToolResultBlock):
                result_ids.add(b.id)
    assert call_ids == result_ids, (
        f"mismatch — calls={call_ids}, results={result_ids}"
    )


def test_trim_returns_zero_when_only_system_present():
    iface = ChatInterface()
    iface.add_system("sys")
    session = _make_session(client=MagicMock(), interface=iface)
    assert session._trim_context_one_round() == 0


def test_trim_returns_zero_when_single_conversation_entry():
    iface = ChatInterface()
    iface.add_system("sys")
    iface.add_user_message("only")
    session = _make_session(client=MagicMock(), interface=iface)
    assert session._trim_context_one_round() == 0


# ---------------------------------------------------------------------------
# Recovery wrapper
# ---------------------------------------------------------------------------


def test_recovery_no_overflow_passes_through():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=5)
    session = _make_session(client=MagicMock(), interface=iface)

    calls = {"n": 0}
    def do_call():
        calls["n"] += 1
        return "result"

    result, dropped, rounds = session._run_with_overflow_recovery(do_call)
    assert result == "result"
    assert dropped == 0
    assert rounds == 0
    assert calls["n"] == 1


def test_recovery_succeeds_after_one_trim():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    session = _make_session(client=MagicMock(), interface=iface)

    attempts = {"n": 0}
    def do_call():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _make_overflow_error()
        return "result"

    result, dropped, rounds = session._run_with_overflow_recovery(do_call)
    assert result == "result"
    assert rounds == 1
    assert dropped >= 1


def test_recovery_succeeds_after_generic_api_error_strong_phrase():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    session = _make_session(client=MagicMock(), interface=iface)

    attempts = {"n": 0}
    err = openai.APIError(
        message="provider rejected request: input exceeds the context window",
        request=MagicMock(),
        body={},
    )

    def do_call():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise err
        return "result"

    result, dropped, rounds = session._run_with_overflow_recovery(do_call)
    assert result == "result"
    assert attempts["n"] == 2
    assert rounds == 1
    assert dropped >= 1


def test_recovery_succeeds_after_bad_request_strong_input_token_limit_phrase():
    """Message-only BadRequest (no canonical code) on the new phrase must
    drive an actual trim/retry, not just a boolean match."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    session = _make_session(client=MagicMock(), interface=iface)

    attempts = {"n": 0}
    err = _make_bad_request_message_only(
        "Input tokens exceed 200000. The configured limit is 128000 tokens."
    )

    def do_call():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise err
        return "result"

    result, dropped, rounds = session._run_with_overflow_recovery(do_call)
    assert result == "result"
    assert attempts["n"] == 2
    assert rounds == 1
    assert dropped >= 1


def test_recovery_succeeds_after_generic_api_error_strong_input_token_limit_phrase():
    """Generic APIError carrying the strong family also trims and retries —
    the Sol-preferred contract for the unconfirmed exception class."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    session = _make_session(client=MagicMock(), interface=iface)

    attempts = {"n": 0}
    err = openai.APIError(
        message="provider rejected request: input tokens exceed 200000, "
                "the configured limit is 128000 tokens",
        request=MagicMock(),
        body={},
    )

    def do_call():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise err
        return "result"

    result, dropped, rounds = session._run_with_overflow_recovery(do_call)
    assert result == "result"
    assert attempts["n"] == 2
    assert rounds == 1
    assert dropped >= 1


def test_recovery_passthrough_for_weak_generic_context_text():
    """Weak generic text (both clauses absent or only one present) must
    NOT trim or retry — the original exception propagates untouched."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    session = _make_session(client=MagicMock(), interface=iface)

    weak_messages = [
        "context window service is temporarily unavailable",
        "the configured limit was updated for your account",
        "input tokens exceed the configured rate limit",
        "input tokens exceed 128000",
    ]
    for weak in weak_messages:
        err = openai.APIError(message=weak, request=MagicMock(), body={})
        attempts = {"n": 0}

        def do_call():
            attempts["n"] += 1
            raise err

        with pytest.raises(openai.APIError) as ei:
            session._run_with_overflow_recovery(do_call)
        assert ei.value is err
        assert attempts["n"] == 1


def test_send_recovers_from_message_only_strong_input_token_limit_phrase():
    """End-to-end via send(): message-only new phrase overflows once, the
    adapter trims, retries, and injects the kernel notice."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)

    client = MagicMock()
    raw = _make_raw_response()
    client.chat.completions.create.side_effect = [
        _make_bad_request_message_only(
            "input tokens exceed 262144, the configured limit is 65536 tokens"
        ),
        raw,
    ]
    session = _make_session(client=client, interface=iface)

    response = session.send("a brand new question")
    assert response.text == "ok"
    assert client.chat.completions.create.call_count == 2

    found = False
    for entry in iface._entries:
        for b in entry.content:
            if (isinstance(b, TextBlock)
                and b.text.startswith("[kernel]")
                and "molt" in b.text.lower()):
                found = True
                break
    assert found, "expected a [kernel] molt-recommendation notice in interface"


def test_recovery_gives_up_after_max_rounds():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=200)  # plenty to trim
    session = _make_session(client=MagicMock(), interface=iface)

    def do_call():
        raise _make_overflow_error()

    pre = len(iface._entries)
    try:
        session._run_with_overflow_recovery(do_call)
    except openai.BadRequestError as exc:
        # Earlier-round trims are NOT rolled back on the failure path
        # (issue #653): history is shorter, and the loss is recorded on
        # the exception so adapters can surface a notice after their
        # error-revert step.
        assert len(iface._entries) < pre
        total_dropped, rounds = exc._overflow_trim_stats
        assert rounds == session._OVERFLOW_MAX_ROUNDS
        assert total_dropped >= rounds  # at least one entry dropped per round
    else:
        raise AssertionError("expected BadRequestError after max rounds")


def test_recovery_attaches_trim_stats_when_trim_stalls():
    """The 'cannot trim further' terminal path also records earlier trims."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=3)  # few entries: trimming stalls quickly
    session = _make_session(client=MagicMock(), interface=iface)

    def do_call():
        raise _make_overflow_error()

    with pytest.raises(openai.BadRequestError) as ei:
        session._run_with_overflow_recovery(do_call)
    total_dropped, rounds = ei.value._overflow_trim_stats
    assert total_dropped > 0
    assert rounds < session._OVERFLOW_MAX_ROUNDS  # stalled, not max rounds


def test_recovery_attaches_no_stats_when_nothing_trimmed():
    """Untrimmable overflow (system only) records no stats -> no notice."""
    iface = ChatInterface()
    iface.add_system("sys")
    session = _make_session(client=MagicMock(), interface=iface)

    def do_call():
        raise _make_overflow_error()

    with pytest.raises(openai.BadRequestError) as ei:
        session._run_with_overflow_recovery(do_call)
    assert getattr(ei.value, "_overflow_trim_stats", None) is None


def test_recovery_reraises_non_overflow_400():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=5)
    session = _make_session(client=MagicMock(), interface=iface)

    err = openai.BadRequestError(
        message="bad tool schema",
        response=MagicMock(status_code=400),
        body={"error": {"message": "bad tool schema"}},
    )
    def do_call():
        raise err

    try:
        session._run_with_overflow_recovery(do_call)
    except openai.BadRequestError as e:
        assert e is err
    else:
        raise AssertionError("expected the original BadRequestError")


# ---------------------------------------------------------------------------
# End-to-end via send()
# ---------------------------------------------------------------------------


def test_send_recovers_and_injects_kernel_notice():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)

    client = MagicMock()
    raw = _make_raw_response()
    # First call raises overflow, second succeeds.
    client.chat.completions.create.side_effect = [
        _make_overflow_error(),
        raw,
    ]
    session = _make_session(client=client, interface=iface)

    response = session.send("a brand new question")
    assert response.text == "ok"
    assert client.chat.completions.create.call_count == 2

    # Find the kernel notice — should be a user-role TextBlock with the
    # [kernel] prefix and a "molt" recommendation.
    found = False
    for entry in iface._entries:
        for b in entry.content:
            if (isinstance(b, TextBlock)
                and b.text.startswith("[kernel]")
                and "molt" in b.text.lower()):
                found = True
                break
    assert found, "expected a [kernel] molt-recommendation notice in interface"


def test_send_passes_through_when_no_overflow():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=3)

    client = MagicMock()
    client.chat.completions.create.return_value = _make_raw_response()
    session = _make_session(client=client, interface=iface)

    pre_len = len(iface._entries)
    response = session.send("hi")
    assert response.text == "ok"
    assert client.chat.completions.create.call_count == 1
    # No kernel notice should be injected when nothing overflowed.
    for entry in iface._entries:
        for b in entry.content:
            if isinstance(b, TextBlock) and b.text.startswith("[kernel]"):
                raise AssertionError("unexpected [kernel] notice in interface")
    # User message was added, assistant reply was recorded — net +2.
    assert len(iface._entries) == pre_len + 2


def _always_overflow(**kw):
    """Side-effect for a client whose every call overflows the context."""
    raise _make_overflow_error()


def _kernel_notices(iface: ChatInterface) -> list[str]:
    return [
        b.text
        for e in iface._entries
        for b in e.content
        if isinstance(b, TextBlock) and b.text.startswith("[kernel]")
    ]


def test_send_terminal_overflow_injects_notice_and_persists_trim():
    """Terminal overflow failure (issue #653): the trimmed entries stay gone,
    but the agent now learns about the loss via a [kernel] notice instead of
    a silent re-raise."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=50)

    client = MagicMock()
    client.chat.completions.create.side_effect = _always_overflow
    session = _make_session(client=client, interface=iface)

    pre = len(iface._entries)
    with pytest.raises(openai.BadRequestError):
        session.send("brand new question")

    # The pending user message was reverted by the adapter's drop_trailing.
    texts = [
        b.text for e in iface._entries for b in e.content
        if isinstance(b, TextBlock)
    ]
    assert "brand new question" not in texts
    # The recovery trims persist (history is shorter than before the send).
    assert len(iface._entries) < pre
    # Exactly one notice about the dropped entries was injected after the
    # revert (a pre-revert injection would have been stripped).
    notices = _kernel_notices(iface)
    assert len(notices) == 1
    assert "dropped" in notices[0].lower()
    assert "molt" in notices[0].lower()


def test_send_stream_terminal_overflow_injects_notice_and_persists_trim():
    """Same guarantee on the streaming path."""
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=50)

    client = MagicMock()
    client.chat.completions.create.side_effect = _always_overflow
    session = _make_session(client=client, interface=iface)

    pre = len(iface._entries)
    with pytest.raises(openai.BadRequestError):
        session.send_stream("brand new streamed question")

    texts = [
        b.text for e in iface._entries for b in e.content
        if isinstance(b, TextBlock)
    ]
    assert "brand new streamed question" not in texts
    assert len(iface._entries) < pre
    notices = _kernel_notices(iface)
    assert len(notices) == 1
    assert "dropped" in notices[0].lower()
    assert "molt" in notices[0].lower()


def test_send_terminal_overflow_untrimmable_no_notice():
    """System-only history: nothing can be trimmed, so no loss occurred and
    no notice is injected — the provider error is re-raised as before."""
    iface = ChatInterface()
    iface.add_system("sys")

    client = MagicMock()
    client.chat.completions.create.side_effect = _always_overflow
    session = _make_session(client=client, interface=iface)

    with pytest.raises(openai.BadRequestError):
        session.send("q")

    assert _kernel_notices(iface) == []
    # System entry only: the pending user message was reverted and nothing
    # else was touched.
    assert len(iface._entries) == 1
    assert iface._entries[0].role == "system"
