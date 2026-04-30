# Agent State Machine

## What

Every LingTai agent is always in exactly one of five lifecycle states. The state machine governs how agents transition between processing, waiting, sleeping, and death — and determines what peers can do to them at any moment.

## Contract

### Five States

| State | Mind (LLM) | Body (heartbeat) | Meaning |
|-------|-----------|-------------------|---------|
| **ACTIVE** | working | running | Processing a message or mid-turn |
| **IDLE** | waiting | running | Between turns; soul flow fires here |
| **STUCK** | errored | running | LLM timeout / upstream error (AED retry in progress) |
| **ASLEEP** | paused | running | `system(sleep)` or stamina expired; listeners stay alive |
| **SUSPENDED** | off | off | Process death; only working directory on disk remains |

### Valid Transitions

```
ACTIVE  --(completed)-----------> IDLE
ACTIVE  --(timeout/exception)---> STUCK
IDLE    --(inbox message)-------> ACTIVE
STUCK   --(AED recovery)--------> ACTIVE   (session reset, fresh run loop)
STUCK   --(AED timeout)---------> ASLEEP   (exhausted retries)
ACTIVE  --(sleep signal)--------> ASLEEP
IDLE    --(sleep signal)--------> ASLEEP
IDLE    --(stamina expired)-----> ASLEEP
ASLEEP  --(inbox message)-------> ACTIVE   (wake from sleep)
ASLEEP  --(.suspend/SIGINT)----> SUSPENDED (process exits)
SUSPENDED --(lingtai run)-------> IDLE     (reconstructed from working dir)
```

### What Triggers Each State

- **→ ACTIVE**: Inbox message arrives (mail, system notification, soul whisper).
- **→ IDLE**: Message processing completes successfully.
- **→ STUCK**: LLM call throws an exception (timeout, 429, 500, network error). AED (Automatic Error Detection) catches it and enters retry.
- **→ ASLEEP**: (1) Explicit `system(sleep)` or `system(lull)` from peer. (2) `.sleep` signal file detected by heartbeat. (3) Stamina timer expires. (4) AED retries exhausted.
- **→ SUSPENDED**: (1) `.suspend` signal file detected by heartbeat. (2) `.refresh` signal file (triggers restart, not true suspension). (3) SIGINT.

### Heartbeat Interaction

The heartbeat is a 1-second daemon thread (`_heartbeat_loop`) that runs in ALL living states (everything except SUSPENDED). It:

1. **Writes** `.agent.heartbeat` file with current timestamp (epoch float).
2. **Checks signal files**: `.interrupt` → cancel current LLM call; `.refresh` → clean restart; `.suspend` → SUSPENDED; `.sleep` → ASLEEP.
3. **Monitors stamina**: if uptime exceeds `stamina` config, transitions to ASLEEP.
4. **Monitors STUCK timeout**: if agent has been STUCK longer than `aed_timeout`, transitions to ASLEEP.
5. **On SUSPENDED**: heartbeat thread exits (no write, process dies).

The heartbeat file is the external liveness signal. `is_alive()` in handshake.py checks its freshness (default 2s threshold).

### Five-State Distinction: ASLEEP vs SUSPENDED

**ASLEEP**: Mind paused, body alive. Mail listeners stay open. A new mail message triggers a wake-up → ACTIVE. No `cpr` needed.

**SUSPENDED**: Process dead. Only the working directory on disk remains. Must be resuscitated with `system(cpr)` or `lingtai run <dir>`.

## Source

| Component | File | Lines |
|-----------|------|-------|
| State enum | `src/lingtai_kernel/state.py` | 1-26 |
| `_set_state()` | `src/lingtai_kernel/base_agent.py` | 592-606 |
| `_heartbeat_loop()` | `src/lingtai_kernel/base_agent.py` | 711-888 |
| `_run_loop()` (main loop) | `src/lingtai_kernel/base_agent.py` | 905-1030 |
| AED recovery | `src/lingtai_kernel/base_agent.py` | 944-1014 |
| `is_alive()` | `src/lingtai_kernel/handshake.py` | 39-55 |

## Why

The STUCK state exists as a buffer between transient LLM errors and permanent death — without it, a single 429 or timeout would kill the agent. ASLEEP keeps the body (heartbeat + mail listener) alive so peers can wake it without CPR; SUSPENDED is true death because sometimes you need to reclaim the process. Collapsing ASLEEP and SUSPENDED into one state was tried and abandoned: operators needed to distinguish "resting but reachable" from "dead and gone."

## Related

- **network-discovery**: External agents check liveness via `.agent.heartbeat` file freshness.
- **preset-allowed-gate**: AED exhaustion can trigger auto-fallback to default preset.
- **`system` tool**: `sleep`, `lull`, `suspend`, `cpr` actions manipulate state externally.
- **`daemon-manual` skill**: Emanations have a simplified version of this state machine.
