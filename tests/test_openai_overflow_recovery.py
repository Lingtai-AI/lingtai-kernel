"""Tests for OpenAIChatSession context-overflow fail-loud propagation.

When a provider returns 400 with context_length_exceeded, the kernel has no
license to silently discard historical canonical entries to fix that — only
an explicit ``summarize`` replacement may replace a historical tool-result
body (see ``lingtai.tools.system.summarize`` and the provider-context
rebuild/replay invariant in ``lingtai.llm.interface_converters``). The
overflow error is classified/logged for diagnostics and re-raised
unconditionally: no trim, no retry with a shortened wire, no fake success
notice. It propagates into the existing AED over-window recovery path.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import openai

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


# ---------------------------------------------------------------------------
# No trimming machinery exists anymore
# ---------------------------------------------------------------------------


def test_trim_context_one_round_no_longer_exists():
    session = _make_session(client=MagicMock())
    assert not hasattr(session, "_trim_context_one_round")
    assert not hasattr(session, "_OVERFLOW_MAX_ROUNDS")
    assert not hasattr(session, "_OVERFLOW_DROP_FRACTION")
    assert not hasattr(session, "_inject_overflow_notice")


# ---------------------------------------------------------------------------
# Recovery wrapper: classify, never trim, always propagate
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


def test_overflow_error_propagates_without_retry_or_trim():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    session = _make_session(client=MagicMock(), interface=iface)
    pre_entries = list(iface._entries)

    attempts = {"n": 0}
    def do_call():
        attempts["n"] += 1
        raise _make_overflow_error()

    try:
        session._run_with_overflow_recovery(do_call)
        raise AssertionError("expected the overflow error to propagate")
    except openai.BadRequestError:
        pass

    # Exactly one attempt — no retry with a shortened wire.
    assert attempts["n"] == 1
    # Canonical history is byte-identical: nothing was trimmed.
    assert iface._entries == pre_entries
    assert len(iface._entries) == len(pre_entries)


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
# End-to-end via send(): full preservation, no fake success
# ---------------------------------------------------------------------------


def test_send_propagates_overflow_preserves_full_history_no_fake_success():
    iface = ChatInterface()
    iface.add_system("sys")
    _seed_history(iface, n_pairs=20)
    pre_entries = list(iface._entries)

    client = MagicMock()
    client.chat.completions.create.side_effect = _make_overflow_error()
    session = _make_session(client=client, interface=iface)

    try:
        session.send("a brand new question")
        raise AssertionError("expected the overflow error to propagate")
    except openai.BadRequestError:
        pass

    # Only one call was ever made — no shortened-wire retry.
    assert client.chat.completions.create.call_count == 1
    # No [kernel] molt/recovery notice is injected — there was no recovery,
    # only a truthful terminal failure.
    for entry in iface._entries:
        for b in entry.content:
            if isinstance(b, TextBlock) and b.text.startswith("[kernel]"):
                raise AssertionError("unexpected [kernel] notice — no fake success")
    # The failed user message is reverted; canonical history is unchanged
    # (full preservation — no partial/trimmed state left behind).
    assert iface._entries == pre_entries


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
