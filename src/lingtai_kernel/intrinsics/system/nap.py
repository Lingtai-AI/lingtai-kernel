"""Nap — pause execution; wakes on incoming message or timeout."""
from __future__ import annotations

import time


# ---------------------------------------------------------------------------
# nap (ported from clock.wait)
# ---------------------------------------------------------------------------

def _nap(agent, args: dict) -> dict:
    max_wait = 300
    seconds = args.get("seconds")
    if seconds is None:
        return {"status": "error", "message": "seconds is required for nap"}

    seconds = float(seconds)
    if seconds < 0:
        return {"status": "error", "message": "seconds must be non-negative"}
    seconds = min(seconds, max_wait)

    agent._log("system_nap_start", seconds=seconds)

    # Clear stale wake signals — only events arriving DURING the nap should wake it.
    agent._nap_wake.clear()
    agent._nap_wake_reason = ""

    def _check_wake(waited: float) -> dict | None:
        if agent._cancel_event.is_set():
            agent._log("system_nap_end", reason="interrupted", waited=waited)
            return {"status": "ok", "reason": "interrupted", "waited": waited}
        if agent._nap_wake.is_set():
            reason = agent._nap_wake_reason or "unknown"
            agent._log("system_nap_end", reason=reason, waited=waited)
            return {"status": "ok", "reason": reason, "waited": waited}
        return None

    poll_interval = 0.5
    t0 = time.monotonic()

    while True:
        waited = time.monotonic() - t0

        result = _check_wake(waited)
        if result:
            return result

        if waited >= seconds:
            agent._log("system_nap_end", reason="timeout", waited=waited)
            return {"status": "ok", "reason": "timeout", "waited": waited}

        remaining = seconds - waited
        sleep_time = min(poll_interval, remaining)

        # Clear right before wait to avoid TOCTOU: if a wake signal arrives
        # between clear and wait, the event is re-set and wait returns immediately.
        agent._nap_wake.clear()
        agent._nap_wake.wait(timeout=sleep_time)
