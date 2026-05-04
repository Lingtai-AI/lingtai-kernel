# Proposal: LLM-hang watchdog

> **Status:** discussion / design proposal. Not yet implemented.
> **Motivated by:** live incident 2026-05-03 22:00–22:45 (codex-gpt5.5 hung in `_session.send()` for 45 minutes while reporting `idle` state)

## Problem

When the LLM API becomes unresponsive, the agent enters a pathological state:

1. `_send()` in `llm_utils.py` retries every `_LLM_WARN_INTERVAL` (20s), logging warnings.
2. After `retry_timeout` (300s), it raises `TimeoutError` → AED fires.
3. AED retries up to `max_aed_attempts` times, each taking another 300s.
4. Total time before the agent sleeps: up to `300 × max_aed_attempts` seconds (default: 900s = 15 min).

During this entire window:
- The agent reports `idle` (the state machine only transitions to `STUCK` *inside* the AED retry loop, which only runs after the 300s timeout).
- The heartbeat thread keeps ticking (`.agent.heartbeat` is fresh).
- The human sees a "ready" agent that isn't responding.
- Inbox messages pile up unread.

**Root cause #1: No visibility.** The `idle` state covers "waiting for input" AND "stuck in LLM call." The TUI/portal can't distinguish them.

**Root cause #2: No escalation threshold.** The retry machinery has no concept of "this has been going on too long — tell someone." It just keeps retrying until `max_aed_attempts` is exhausted.

**Root cause #3 (suspected): Missing HTTP-layer read timeout.** The `_SubmitFn` sets `chat._request_timeout = retry_timeout` (300s) on the *SDK level*, but this may not translate to a socket-level read timeout in the underlying httpx/aiohttp client. If the server holds the connection open (e.g., Codex proxy issues), the SDK timeout may not fire, and the 300s watchdog in `_send` never triggers because `future.result(timeout=wait)` keeps succeeding on the 20s poll interval (the future hasn't completed, but it also hasn't timed out — it's just waiting on the socket).

## Goal

A watchdog layer that:

1. **Detects hang independently of the retry machinery.** Tracks cumulative wall-clock time spent in `_session.send()`, not just retry count.
2. **Transitions to `STUCK` with a structured event** so the TUI can show "LLM API unresponsive for 2+ minutes."
3. **Surfaces to the human** via `.status.json` (already consumed by TUI) and optionally a mailbox notification.
4. **Backs off retries** after the watchdog trips, rather than retrying at the same cadence indefinitely.
5. **Does not replace existing retry/AED.** The watchdog is a visibility layer; AED remains the recovery mechanism.

---

## Design

### Where it hooks in

The watchdog lives in `base_agent/turn.py` at the `_handle_request` call site, wrapping the `agent._session.send(content)` call. This is the only place where a synchronous LLM call blocks the main loop for an unbounded time.

```
_handle_request(agent, msg)
    content = agent._pre_request(msg)
    ...
    response = agent._session.send(content)   ← watchdog wraps this
    agent._last_usage = response.usage
    ...
```

### Mechanism

**Thread-based watchdog timer.** Before calling `agent._session.send(content)`, arm a background timer thread. If the send returns before the threshold, disarm it. If the timer fires, escalate.

```python
# In base_agent/turn.py

_LLM_HANG_THRESHOLD_SECONDS = 120.0  # 2 minutes of unresponsive LLM
_LLM_HANG_EVENT_NAME = "llm_hang_detected"

def _handle_request(agent, msg: Message) -> None:
    ...
    # Arm watchdog
    hang_timer = threading.Timer(
        _LLM_HANG_THRESHOLD_SECONDS,
        _on_llm_hang,
        args=(agent,),
    )
    hang_timer.daemon = True
    hang_timer.start()

    try:
        response = agent._session.send(content)
    finally:
        hang_timer.cancel()

    # Rest of method unchanged
    ...


def _on_llm_hang(agent) -> None:
    """Watchdog callback: LLM has been unresponsive for too long."""
    from .state import AgentState

    agent._log(_LLM_HANG_EVENT_NAME,
               seconds=_LLM_HANG_THRESHOLD_SECONDS,
               state=agent._state.value)

    # Transition to STUCK if not already
    if agent._state not in (AgentState.STUCK, AgentState.ASLEEP, AgentState.SUSPENDED):
        agent._set_state(AgentState.STUCK, reason="LLM API unresponsive")

    # Write to .status.json for TUI visibility
    try:
        status = agent.status()
        status["runtime"]["llm_hang"] = {
            "detected_at": time.time(),
            "threshold_seconds": _LLM_HANG_THRESHOLD_SECONDS,
        }
        (agent._working_dir / ".status.json").write_text(
            json.dumps(status, ensure_ascii=False, indent=2)
        )
    except Exception:
        pass
```

### What `_set_state(STUCK)` triggers

The existing `_set_state` already handles `STUCK`:
- Clears `_idle` event (agent no longer reports `is_idle`).
- Cancels soul timer (no more consultation fires while stuck).
- Writes manifest with `state: "stuck"` to `.agent.json`.
- Logs `agent_state` event.

The heartbeat loop already detects `STUCK` and starts the AED timeout clock (`_aed_start`). If the agent remains stuck for `aed_timeout` (360s), it transitions to `ASLEEP`.

### Recovery

The watchdog fires → `_set_state(STUCK)` → heartbeat starts AED clock → existing AED machinery handles retries and eventual sleep.

**Key insight:** the watchdog doesn't need its own recovery path. It piggybacks on the existing AED → ASLEEP → wake-from-inbox cycle. What it adds is *early detection* (120s instead of 300s) and *visibility* (the TUI sees `state: "stuck"` immediately).

### Interaction with `_session.send()` timeout

The existing `_send()` in `llm_utils.py` has its own 300s timeout. The watchdog (120s) fires *before* that timeout. When the watchdog fires:

1. `_set_state(STUCK)` is called (sets `_cancel_event` via AED path).
2. The `future.result(timeout=wait)` loop in `_send()` continues.
3. If the send eventually succeeds (or times out at 300s), AED handles it.
4. The watchdog timer is cancelled in the `finally` block when `_session.send()` returns.

If the send is genuinely hung (never returns), the watchdog timer thread lives as a daemon — it dies with the process. No leak.

### Spurious-fire risk

The 120s threshold is conservative. Modern thinking models (GLM-5.1, DeepSeek V4, Anthropic extended-thinking) routinely take 60–180s for high-context turns. A 120s threshold would fire on legitimately slow but successful calls.

**Mitigation options:**

1. **Higher threshold (180s).** Safe for current models but delays detection.
2. **Progressive warning.** At 60s: log `llm_slow_response`. At 120s: log `llm_hang_detected`, set `STUCK`. At 180s: enqueue mailbox notification. This gives the TUI intermediate signals without false-alarming.
3. **Context-aware threshold.** If the agent's context usage is >80%, expect slower responses and increase the threshold. This requires reading `_session.get_context_pressure()` before arming the timer.

**Recommendation:** option 2 (progressive warning) with the `STUCK` transition at 120s. The `STUCK` state is not destructive — it just signals the TUI. The agent continues retrying normally. If the response arrives at 130s, `_set_state(IDLE)` in the `finally` block restores normal operation. False positives are cheap (brief TUI flash); missed hangs are expensive (45 minutes of silence).

### Interaction with streaming

For streaming responses, `_session.send()` returns immediately (the response object accumulates chunks). The watchdog would fire on streaming calls even when the stream is progressing (because the `send()` returned). This is a non-issue — the watchdog only fires if `send()` itself blocks.

However, `_send_streaming()` in `session.py` calls `send_with_timeout_stream()` which blocks on `future.result()`. Same behavior as non-streaming. The watchdog works for both paths.

---

## Root cause investigation: HTTP-layer read timeout

### Current state

`_SubmitFn.__call__()` sets `chat._request_timeout = retry_timeout` (300s) before submitting to the thread pool. The SDK adapter reads this and passes it to the HTTP client.

**Gap:** The `_request_timeout` attribute is set on the `chat` object (the `ChatSession` ABC). The actual HTTP client (httpx, aiohttp) receives this as a `timeout` parameter. But httpx distinguishes between:

- `connect_timeout` — time to establish connection
- `read_timeout` — time waiting for response bytes
- `write_timeout` — time to send request bytes
- `pool_timeout` — time waiting for a connection from the pool

If the adapter passes `_request_timeout` as a single `timeout` value (not per-phase), httpx may interpret it as `connect_timeout` only, leaving `read_timeout` at its default (5s or None depending on version). This would explain why:

- The connection to the Codex proxy succeeds (connect_timeout met).
- The proxy holds the connection open (no data arrives, read_timeout not enforced).
- `future.result(timeout=20)` in the `_send()` loop returns `TimeoutError` every 20s (the main-thread watchdog), but the actual HTTP connection is still alive and waiting.
- The SDK never times out because the socket is technically active.

**Verification needed:** Check how each adapter (OpenAI, Anthropic, Codex, etc.) translates `_request_timeout` into HTTP client timeout configuration. Grep for `httpx`, `aiohttp`, `requests`, `timeout` in `src/lingtai/llm/adapters/`.

### Proposed fix (if confirmed)

Add explicit `read_timeout` to the adapter's HTTP client configuration:

```python
# In each adapter's _create_client() or equivalent
timeout = httpx.Timeout(
    connect=30.0,
    read=self._request_timeout,  # ← explicit read timeout
    write=30.0,
    pool=10.0,
)
client = httpx.Client(timeout=timeout)
```

This ensures the HTTP connection itself times out when the server stops sending data, regardless of the SDK-level `_request_timeout` attribute.

**This is a separate fix from the watchdog.** The watchdog detects the hang; the read_timeout fix prevents it from happening in the first place. Both are needed.

---

## Interaction with tc-injection-service patch

The watchdog fires `_set_state(STUCK)`, which is the same state the heartbeat loop uses for AED. The tc-injection-service patch moves `_drain_tc_inbox` to `TCInbox.drain_into()`. The watchdog does not interact with drain — it only wraps `_session.send()`. No conflict.

---

## Open questions

1. **Should the watchdog write a signal file (`.hang`) instead of just updating `.status.json`?** Signal files are the established mechanism for inter-process communication (TUI reads them). But `.status.json` is already consumed by the TUI and is updated every turn. Adding a `.hang` file would be a new convention. **Suggested:** use `.status.json` only, with a new `llm_hang` key in the `runtime` section. The TUI already polls this file.

2. **Should the watchdog enqueue a mailbox notification?** This would wake the agent from ASLEEP if it had already transitioned there. But the agent is already in STUCK (not ASLEEP) when the watchdog fires. A mailbox notification would be redundant with the state transition. **Suggested:** no mailbox notification for the watchdog; the state transition is sufficient.

3. **Should the threshold be configurable?** The current proposal uses a module-level constant (120s). Making it a `config.py` field (`llm_hang_threshold`) would let the human tune it per-agent. **Suggested:** start with a constant; make configurable if false positives become a problem.

4. **Should `_handle_tc_wake` also get the watchdog?** It calls `agent._session.send([item.result])` which can also hang. **Suggested:** yes, wrap the send call in `_handle_tc_wake` with the same watchdog. The threshold and callback are the same.

---

## Test plan

- `test_watchdog_fires_on_slow_send`: mock `session.send` to sleep 130s (above threshold), verify `_set_state(STUCK)` is called.
- `test_watchdog_cancels_on_fast_send`: mock `session.send` to return immediately, verify no state change.
- `test_watchdog_cancels_on_normal_send`: mock `session.send` to sleep 50s (below threshold), verify no state change.
- `test_watchdog_state_restored_on_success`: mock `session.send` to sleep 130s then succeed, verify agent transitions back to IDLE/ACTIVE.
- `test_status_includes_llm_hang`: verify `.status.json` contains `llm_hang` key after watchdog fires.

---

## Recommendation

Implement the watchdog first (visibility layer), then investigate the read_timeout fix (prevention layer). The watchdog is ~30 lines of new code in `turn.py`; the read_timeout fix requires auditing each adapter. Both are needed but independent.

The progressive-warning approach (60s slow, 120s stuck) balances detection speed against false positives. The `STUCK` state is non-destructive and reversible, so false positives are cheap.
