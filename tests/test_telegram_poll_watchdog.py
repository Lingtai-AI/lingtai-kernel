"""Poll-liveness watchdog + bounded blocking calls for ``TelegramAccount``.

Regression cover for the silent multi-day channel death: after a brief host
network interruption the poll thread stopped calling ``getUpdates`` for good
while the process still looked healthy -- it retried nothing, logged nothing,
and nothing observed the thread.  Every test here injects its own request
function, so none of them touch the network.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

import pytest

from lingtai.mcp_servers.telegram.account import (
    TelegramAccount,
    TelegramPollTimeoutError,
)

LOGGER = "lingtai.mcp_servers.telegram.account"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_account(**overrides: Any) -> TelegramAccount:
    """Account with no state dir (writes nothing) and a snappy poll interval."""
    params: dict[str, Any] = {
        "alias": "watchdog-test",
        "bot_token": "123456:TEST-TOKEN",
        "allowed_users": None,
        "poll_interval": 0.01,
    }
    params.update(overrides)
    return TelegramAccount(**params)


def _install(acct: TelegramAccount, request: Callable[..., dict]) -> None:
    """Replace the single outbound call (``_request``) with a fake."""
    acct._request = request  # type: ignore[method-assign]


def _idle_request(method: str, hard_timeout: float | None = None, **kwargs: Any) -> dict:
    """A healthy bot: getMe/setMyCommands answer, getUpdates returns no news."""
    if method == "getUpdates":
        return []
    return {"username": "watchdog_test_bot"}


class _ExitedThread:
    """Stands in for a poll thread that died without setting the stop event."""

    name = "telegram-poll-watchdog-test-exited"

    def is_alive(self) -> bool:
        return False


# ---------------------------------------------------------------------------
# Bounded blocking path
# ---------------------------------------------------------------------------


def test_a_wedged_call_raises_instead_of_hanging() -> None:
    acct = _make_account()
    called = threading.Event()
    release = threading.Event()

    def wedged() -> dict:
        called.set()
        release.wait(timeout=30.0)
        return {"ok": True}

    started = time.monotonic()
    try:
        with pytest.raises(TelegramPollTimeoutError) as excinfo:
            acct._call_with_deadline(wedged, 0.2, "getUpdates")
    finally:
        release.set()
    elapsed = time.monotonic() - started

    assert called.is_set(), "the call was never started"
    assert elapsed < 5.0, f"the deadline took {elapsed:.1f}s to fire"
    assert "getUpdates" in str(excinfo.value)
    assert "0.2" in str(excinfo.value)
    # An ordinary exception on purpose: the poll loop's existing retry path
    # catches it, so a hard deadline turns a hang into a logged retry.
    assert isinstance(excinfo.value, TimeoutError)


def test_request_hard_timeout_bounds_the_whole_request_body() -> None:
    acct = _make_account()
    release = threading.Event()
    seen: dict[str, Any] = {}

    def wedged_once(method: str, **kwargs: Any) -> dict:
        seen["method"] = method
        seen["kwargs"] = kwargs
        release.wait(timeout=30.0)
        return {}

    acct._request_once = wedged_once  # type: ignore[method-assign]
    try:
        with pytest.raises(TelegramPollTimeoutError):
            acct._request("getUpdates", hard_timeout=1.0, json={"timeout": 30})
    finally:
        release.set()

    assert seen == {"method": "getUpdates", "kwargs": {"json": {"timeout": 30}}}


def test_request_without_hard_timeout_is_unchanged() -> None:
    """Non-poll callers keep their existing unbounded, kwarg-forwarding path."""
    acct = _make_account()
    seen: dict[str, Any] = {}

    def record(method: str, **kwargs: Any) -> dict:
        seen["method"] = method
        seen["kwargs"] = kwargs
        return {"ok": True}

    acct._request_once = record  # type: ignore[method-assign]
    assert acct._request("sendMessage", json={"chat_id": 1}) == {"ok": True}
    assert seen == {"method": "sendMessage", "kwargs": {"json": {"chat_id": 1}}}


def test_poll_request_timeout_defaults_and_overrides() -> None:
    assert _make_account()._poll_request_timeout == 90.0
    assert _make_account(poll_request_timeout=12.5)._poll_request_timeout == 12.5


# ---------------------------------------------------------------------------
# Last-successful-poll evidence
# ---------------------------------------------------------------------------


def test_successful_poll_refreshes_the_last_success_timestamp() -> None:
    acct = _make_account(poll_interval=0.0)
    calls: list[float | None] = []

    def one_cycle(method: str, hard_timeout: float | None = None, **kwargs: Any) -> list:
        calls.append(hard_timeout)
        acct._stop_event.set()  # one cycle is enough
        return []

    _install(acct, one_cycle)
    acct._last_poll_success = None
    started = time.monotonic()
    acct._poll_loop()  # driven synchronously for determinism

    assert calls == [acct._poll_request_timeout], "poll request was not bounded"
    assert acct._last_poll_success is not None
    assert started <= acct._last_poll_success <= time.monotonic()


def test_failed_poll_does_not_refresh_the_last_success_timestamp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    acct = _make_account(poll_interval=0.0)
    stamp = time.monotonic() - 1234.0
    acct._last_poll_success = stamp

    def failing(method: str, hard_timeout: float | None = None, **kwargs: Any) -> list:
        acct._stop_event.set()
        raise TelegramPollTimeoutError("getUpdates exceeded its hard deadline of 90s")

    _install(acct, failing)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        acct._poll_loop()  # must swallow it, not propagate

    assert acct._last_poll_success == stamp
    assert "Telegram poll error (watchdog-test)" in caplog.text


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------


def test_poll_watchdog_timeout_defaults_and_overrides() -> None:
    assert _make_account()._watchdog_timeout == 300.0
    assert _make_account(poll_watchdog_timeout=42.0)._watchdog_timeout == 42.0


def test_watchdog_rebuilds_a_wedged_poll_thread(caplog: pytest.LogCaptureFixture) -> None:
    """A poll thread stuck in a call that never returns is replaced."""
    acct = _make_account(poll_watchdog_timeout=0.2, poll_request_timeout=30.0)
    acct._watchdog_interval = 0.05
    wedged = threading.Event()

    def wedged_getupdates(method: str, hard_timeout: float | None = None, **kwargs: Any) -> Any:
        if method == "getUpdates":
            wedged.wait(timeout=30.0)  # never returns, never refreshes the stamp
            return []
        return {"username": "watchdog_test_bot"}

    _install(acct, wedged_getupdates)
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        acct.start()
        try:
            first = acct._poll_thread
            assert first is not None and first.is_alive()
            # Assert the durable post-condition, not an instantaneous sample:
            # wait for a *live* replacement.  A just-spawned poll thread can be
            # caught mid-retirement (the watchdog's next tick then repairs it),
            # which is a scheduling observation rather than a lost channel.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and (
                acct._poll_thread is first or not acct._poll_thread.is_alive()
            ):
                time.sleep(0.02)
            assert acct._poll_thread is not first, "watchdog did not rebuild the poll thread"
            assert acct._poll_thread.is_alive()
            assert first in acct._abandoned_poll_threads
            assert "Telegram poll watchdog (watchdog-test)" in caplog.text
            assert "no successful getUpdates for" in caplog.text
            assert "restarting poll thread" in caplog.text
            assert f"abandoning poll thread '{first.name}'" in caplog.text
        finally:
            wedged.set()
            acct.stop()


def test_watchdog_rebuilds_when_the_poll_thread_is_gone(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dead thread is replaced even when the stall threshold is not reached."""
    acct = _make_account(poll_interval=0.01)  # default threshold: 300s
    _install(acct, _idle_request)
    stub = _ExitedThread()
    acct._poll_thread = stub  # type: ignore[assignment]
    acct._last_poll_success = time.monotonic()

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        acct._watchdog_tick()
    try:
        assert acct._poll_thread is not stub
        assert acct._poll_thread.is_alive()
        assert "poll thread 'telegram-poll-watchdog-test-exited' exited" in caplog.text
        assert "restarting poll thread" in caplog.text
    finally:
        acct.stop()


def test_a_superseded_poll_thread_retires_and_drops_its_batch() -> None:
    """A replaced thread must not poll (or dispatch) alongside its successor."""
    acct = _make_account(poll_interval=0.0)
    assert acct._retire_if_superseded(acct._poll_generation) is False
    acct._poll_generation += 1
    assert acct._retire_if_superseded(acct._poll_generation - 1) is True

    handled: list[dict] = []
    acct._process_update = handled.append  # type: ignore[method-assign]

    def superseded_midflight(method: str, hard_timeout: float | None = None, **kwargs: Any) -> list:
        acct._poll_generation += 1  # the watchdog replaced us mid-flight
        return [{"update_id": 7}]

    _install(acct, superseded_midflight)
    acct._poll_loop()
    assert handled == [], "a superseded thread dispatched updates fetched in flight"


def test_watchdog_leaves_a_healthy_poll_thread_alone() -> None:
    acct = _make_account(poll_watchdog_timeout=60.0, poll_interval=0.01)
    _install(acct, _idle_request)
    acct.start()
    try:
        first = acct._poll_thread
        assert first is not None
        time.sleep(0.1)
        acct._watchdog_tick()
        acct._watchdog_tick()
        assert acct._poll_thread is first
        assert acct._abandoned_poll_threads == []
        assert first.is_alive()
    finally:
        acct.stop()


def test_stop_retires_the_watchdog_and_never_restarts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    acct = _make_account(poll_watchdog_timeout=0.05, poll_interval=0.05)
    acct._watchdog_interval = 0.02
    _install(acct, _idle_request)
    acct.start()
    watchdog = acct._watchdog_thread
    poll_thread = acct._poll_thread
    assert watchdog is not None and watchdog.is_alive()
    assert watchdog.name == "telegram-poll-watchdog-watchdog-test"
    assert poll_thread is not None and poll_thread.name == "telegram-poll-watchdog-test"
    live_before = set(threading.enumerate())

    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        acct.stop()
        # Threshold is long exceeded now and the interval has elapsed: a
        # watchdog that survived stop() would restart the poll thread, and a
        # forced tick must not either.
        acct._last_poll_success = time.monotonic() - 10.0
        time.sleep(0.2)
        acct._watchdog_tick()

    assert not watchdog.is_alive()
    assert acct._watchdog_thread is None
    assert acct._poll_thread is None
    assert not poll_thread.is_alive()
    assert "restarting poll thread" not in caplog.text
    spawned_after_stop = [
        t for t in set(threading.enumerate()) - live_before if acct.alias in t.name
    ]
    assert spawned_after_stop == [], f"threads spawned after stop(): {spawned_after_stop}"
