"""The poll loop fires a periodic idle tick (issue #111 wiring).

The notification reconcile only helps if it runs independently of new inbound
messages — otherwise it cannot recover the post-molt stall, which by definition
happens when no fresh message arrives. The account poll loop therefore invokes
an `on_idle_tick` callback once per poll cycle (including the common no-update
long-poll return), which the manager wires to `reconcile_notifications`.
"""
from __future__ import annotations

import threading

from lingtai.mcp_servers.telegram.account import TelegramAccount


def _make_account(on_idle_tick) -> TelegramAccount:
    return TelegramAccount(
        alias="main",
        bot_token="x:y",
        allowed_users=None,
        poll_interval=0.0,
        on_message=None,
        state_dir=None,
        commands=[],
        on_idle_tick=on_idle_tick,
    )


def test_poll_loop_calls_idle_tick_after_each_cycle():
    """One getUpdates cycle (no updates) fires the idle tick exactly once,
    then the loop stops cleanly."""
    ticks: list[int] = []
    acct = _make_account(lambda: ticks.append(1))

    # Stub the network call: return no updates, then signal stop so the loop
    # exits after a single cycle.
    def _fake_request(method, **kwargs):
        acct._stop_event.set()
        return []

    acct._request = _fake_request  # type: ignore[assignment]
    acct._poll_loop()

    assert ticks == [1]


def test_idle_tick_failure_does_not_break_poll_loop():
    """A raising idle tick is swallowed — the poll loop must never die on a
    best-effort reconcile failure."""
    calls: list[int] = []

    def _boom():
        calls.append(1)
        raise RuntimeError("reconcile blew up")

    acct = _make_account(_boom)

    def _fake_request(method, **kwargs):
        acct._stop_event.set()
        return []

    acct._request = _fake_request  # type: ignore[assignment]
    # Should not raise.
    acct._poll_loop()

    assert calls == [1]


def test_idle_tick_optional():
    """on_idle_tick defaults to None and the loop runs without it."""
    acct = TelegramAccount(
        alias="main",
        bot_token="x:y",
        allowed_users=None,
        poll_interval=0.0,
        on_message=None,
        state_dir=None,
        commands=[],
    )

    def _fake_request(method, **kwargs):
        acct._stop_event.set()
        return []

    acct._request = _fake_request  # type: ignore[assignment]
    # No callback wired — must not raise.
    acct._poll_loop()
