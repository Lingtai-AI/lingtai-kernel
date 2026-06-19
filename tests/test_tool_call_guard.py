"""Focused unit tests for the ``ToolCallGuard`` failure posture.

The composable guard layer is **fail-open at wiring/collection time but
fail-closed at check-evaluation time**. Wiring-side fail-open is covered by
``tests/test_wrapper_guard_wiring.py`` (a manifest provider that raises while
*assembling* the guard leaves a safe pass-through). The complementary
fail-*closed* contract — a host-supplied check that raises *while judging a
proposal* is coerced to a deny, not skipped — is exercised at the executor
integration level in ``tests/test_tool_executor.py``
(``test_tool_call_guard_check_exception_denies_without_crashing_parallel_batch``).

These tests pin that fail-closed contract directly on ``ToolCallGuard.evaluate``
so a future host wiring custom checks has an explicit, dependency-free statement
of the invariant: a buggy gatekeeper must not become an open gate.
"""
from __future__ import annotations

from lingtai.kernel.tool_call_guard import (
    GuardDecision,
    ToolCallGuard,
    ToolProposal,
)


def _proposal(tool_name: str = "any") -> ToolProposal:
    return ToolProposal(tool_name=tool_name, tool_args={})


# --- baseline: empty chain is a clean pass-through (no posture surfaced) ------


def test_empty_chain_is_pass_through():
    decision = ToolCallGuard().evaluate(_proposal())
    assert decision.allowed is True
    assert decision.approval_mode == "pass_through"


# --- fail-closed: a check that RAISES while judging denies the call -----------


def test_check_that_raises_is_coerced_to_deny():
    """A host-supplied check raising during evaluation fails CLOSED.

    The exception is turned into a deny (never a silent allow/skip): a broken
    gatekeeper blocks the call it was judging rather than letting it through.
    """

    def boom(proposal):
        raise ValueError("gatekeeper exploded")

    decision = ToolCallGuard([boom]).evaluate(_proposal("scary"))

    assert decision.allowed is False
    assert decision.action == "deny"
    assert decision.severity == "error"
    # The firing check is attributed by name, and the exception type is recorded
    # so a host can see *which* check failed and *why* it was coerced to deny.
    assert decision.check_name == "boom"
    assert decision.metadata["exception_type"] == "ValueError"
    assert "ValueError" in decision.reason
    assert "gatekeeper exploded" in decision.reason


def test_raising_check_short_circuits_before_later_checks_run():
    """The deny from a raising check short-circuits the chain.

    A later check that *would* allow can never override the fail-closed deny from
    an earlier raising check — the gate stays shut.
    """
    later_ran = []

    def boom(proposal):
        raise RuntimeError("first check broke")

    def allow_everything(proposal):
        later_ran.append(proposal.tool_name)
        return GuardDecision.allow(check_name="allow_everything")

    decision = ToolCallGuard([boom, allow_everything]).evaluate(_proposal())

    assert decision.allowed is False
    assert decision.metadata["exception_type"] == "RuntimeError"
    assert later_ran == []  # short-circuited; the allowing check never ran


def test_anonymous_lambda_check_exception_gets_indexed_name():
    """A nameless check (lambda) that raises is still attributed (by index)."""
    guard = ToolCallGuard([lambda proposal: (_ for _ in ()).throw(KeyError("x"))])
    decision = guard.evaluate(_proposal())
    assert decision.allowed is False
    # ``<lambda>`` has a ``__name__`` so it is used; the point is it is non-empty.
    assert decision.check_name
    assert decision.metadata["exception_type"] == "KeyError"


# --- contrast: a check that cleanly returns None does NOT deny ----------------


def test_check_returning_none_is_treated_as_allow_not_deny():
    """Returning ``None`` (abstain) is an allow — only *raising* fails closed.

    This is the load-bearing distinction: an abstaining check is not the same as
    a broken one. Abstention passes through; an exception denies.
    """
    abstained = []

    def abstain(proposal):
        abstained.append(proposal.tool_name)
        return None

    decision = ToolCallGuard([abstain]).evaluate(_proposal("read"))
    assert abstained == ["read"]
    assert decision.allowed is True
    assert decision.approval_mode == "pass_through"
