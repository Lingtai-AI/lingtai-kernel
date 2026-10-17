"""Tests for the sustained context-pressure streak (channel B).

The molt reminder in ``_meta.agent_meta.agent_state.context.molt`` is not an immediate
recovery-target nudge. Instead it tracks *fresh provider rounds* whose context
usage is at/above the reconstruction threshold (0.85).  The reminder only begins
on the THIRD consecutive high round, so a single spike (or even two) does not
nag the agent before the delayed-summarize reconstruction has had a chance to
relieve pressure.  Duplicate observations of the same provider round must not
advance the streak, and a drop below threshold resets it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from lingtai.kernel.config import AgentConfig
from lingtai.kernel.meta_block import build_molt_context
from lingtai.kernel.session import (
    SessionManager,
    CONTEXT_PRESSURE_HIGH_RATIO,
    CONTEXT_PRESSURE_RECONSTRUCTION_RATIO,
    CONTEXT_PRESSURE_WARN_AFTER_ROUNDS,
)


def make_session_manager(**kw):
    """Self-contained SessionManager factory (mirrors test_session.py)."""
    svc = MagicMock()
    svc.model = "test-model"
    mock_session = MagicMock()
    mock_session.context_window.return_value = 100000
    mock_session.interface.estimate_context_tokens.return_value = 5000
    mock_session.interface.current_system_prompt = "test prompt"
    svc.create_session.return_value = mock_session
    config = kw.get("config", AgentConfig())
    return (
        SessionManager(
            llm_service=svc,
            config=config,
            agent_name="test",
            streaming=kw.get("streaming", False),
            build_system_prompt_fn=lambda: "test prompt",
            build_tool_schemas_fn=lambda: [],
            logger_fn=kw.get("logger_fn", None),
        ),
        svc,
        mock_session,
    )


def test_constants_match_contract():
    assert CONTEXT_PRESSURE_HIGH_RATIO == 0.85
    # The reconstruction ratio is now the 1.0 hard forced-rebuild boundary
    # (CONTEXT_PRESSURE_RECONSTRUCTION_RATIO is a back-compat alias).
    assert CONTEXT_PRESSURE_RECONSTRUCTION_RATIO == 1.0
    assert CONTEXT_PRESSURE_WARN_AFTER_ROUNDS == 3


def _usage(input_tokens):
    from unittest.mock import MagicMock

    return MagicMock(
        input_tokens=input_tokens,
        output_tokens=10,
        thinking_tokens=0,
        cached_tokens=0,
        extra={},
    )


def _response(input_tokens, call_id):
    from unittest.mock import MagicMock

    return MagicMock(
        text="ok",
        tool_calls=[],
        thoughts=[],
        usage=_usage(input_tokens),
        api_call_id=call_id,
    )


def test_track_usage_advances_streak_on_fresh_high_rounds():
    """Each real provider round (one _track_usage call) is a fresh round keyed
    by the incrementing _api_calls counter; three high rounds arm the warning.

    The streak uses the PROVIDER-reported input tokens, not the local estimate:
    estimate_context_tokens is pinned LOW (30000 -> 0.30) yet the provider's
    90000 -> 0.90 still drives the streak."""
    sm, _, mock_session = make_session_manager()
    sm.ensure_session()
    mock_session.context_window.return_value = 100000
    mock_session.interface.estimate_context_tokens.return_value = 30000  # ignored

    agent = SimpleNamespace(_intrinsics={"context"}, _session=sm)
    reminder = sm.context_pressure_reminder
    for i in range(3):
        sm._track_usage(_response(90000, f"call-{i}"))
        assert reminder.streak == i + 1
        assert reminder.last_round_id == i + 1
        assert reminder.last_usage == 0.90
        assert reminder.active is (i == 2)
        before = reminder.snapshot()
        for _ in range(2):
            molt = build_molt_context(agent, 0.90)
            assert molt == reminder.current_molt_context(0.90)
            assert (molt is not None) is (i == 2)
        assert reminder.snapshot() == before

    assert "3 consecutive fresh model calls" in molt


def test_track_usage_uses_provider_input_not_local_estimate():
    """If the local estimate is high but the provider reports low, the streak
    must follow the provider (the reconstruction threshold is provider-based)."""
    sm, _, mock_session = make_session_manager()
    sm.ensure_session()
    mock_session.context_window.return_value = 100000
    mock_session.interface.estimate_context_tokens.return_value = 90000  # high, ignored

    for i in range(4):
        sm._track_usage(_response(20000, f"low-{i}"))  # provider 0.20 -> not high

    assert sm.context_pressure_reminder.streak == 0
    assert sm.context_pressure_reminder.active is False


def test_track_usage_resets_streak_when_pressure_relieved():
    sm, _, mock_session = make_session_manager()
    sm.ensure_session()
    mock_session.context_window.return_value = 100000

    sm._track_usage(_response(90000, "c1"))  # provider 0.90
    sm._track_usage(_response(90000, "c2"))
    assert sm.context_pressure_reminder.streak == 2

    sm._track_usage(_response(30000, "c3"))  # provider 0.30 -> relieved
    assert sm.context_pressure_reminder.streak == 0
    assert sm.context_pressure_reminder.active is False


def test_track_usage_unknown_window_preserves_pressure_state():
    sm, _, mock_session = make_session_manager()
    sm.ensure_session()
    sm._track_usage(_response(90000, "high-1"))
    sm._track_usage(_response(90000, "high-2"))
    reminder = sm.context_pressure_reminder
    assert reminder.streak == 2

    mock_session.context_window.return_value = 0
    sm._track_usage(_response(90000, "unknown-window"))
    assert reminder.streak == 2
    assert reminder.last_round_id == 2
    assert reminder.last_transition_reason == "unknown_usage"

    mock_session.context_window.return_value = 100000
    sm._track_usage(_response(85000, "threshold-high"))
    assert reminder.streak == 3
    assert reminder.last_round_id == 4
    assert reminder.active is True


def test_total_tokens_does_not_add_inclusive_thinking_twice():
    from lingtai.kernel.llm.base import LLMResponse, UsageMetadata
    events = []
    sm, _, _ = make_session_manager(logger_fn=lambda kind, **fields: events.append((kind, fields)))
    sm.ensure_session()
    for index, (incoming, output, thinking, cached) in enumerate(((100, 60, 20, 30), (200, 90, 40, 50))):
        sm._track_usage(LLMResponse(text="ok", api_call_id=f"inclusive-{index}",
            usage=UsageMetadata(input_tokens=incoming, output_tokens=output,
                                thinking_tokens=thinking, cached_tokens=cached)))
    usage = sm.get_token_usage()
    assert usage["total_tokens"] == 450  # 300 input + 150 inclusive output, not +60 thinking.
    assert (usage["input_tokens"], usage["output_tokens"], usage["thinking_tokens"],
            usage["cached_tokens"], usage["ctx_total_tokens"], usage["api_calls"]) == (300, 150, 60, 80, 200, 2)
    rounds = [fields for kind, fields in events if kind == "llm_response"]
    assert [(row["output_tokens"], row["thinking_tokens"]) for row in rounds] == [(60, 20), (90, 40)]
