# Boot Verification

## What

After spawning an avatar process, the parent must confirm the child actually
started successfully. Without boot verification, the parent's LLM would see
"ok" even if the child crashed 50ms later. The verification is a synchronous
poll loop that waits for one of three outcomes: heartbeat, exit, or timeout.

## Contract

### Wait loop (`_wait_for_boot`, lines 318-352)

Polls at `_BOOT_POLL_INTERVAL` (0.1s) for up to `_BOOT_WAIT_SECS` (5.0s).
Three outcomes:

| Outcome | Condition | Return value |
|---------|-----------|--------------|
| **ok** | `.agent.heartbeat` file appears | `("ok", None)` |
| **failed** | Process exits (poll returns non-None) | `("failed", "<stderr tail>")` |
| **slow** | Deadline hit, process still alive | `("slow", None)` |

### Heartbeat file check (line 330)

- File: `<working_dir>/.agent.heartbeat`
- Check: `heartbeat.is_file()` — existence only, content not inspected.
- Written by the child's `_heartbeat_loop()` (base_agent.py:718-719) on its first
  tick (within 1 second of boot).

### Exit detection (lines 335-350)

- Uses `proc.poll()` — returns exit code if process terminated.
- On exit: reads `logs/spawn.stderr` (capped at 2000 bytes) for diagnostic output.

### Ledger recording (lines 267-278)

- `boot_status` field: one of `"ok"`, `"failed"`, `"slow"`.
- `boot_error` field: present only for `"failed"`, contains stderr tail.

### Result returned to caller (lines 281-316)

- `failed` → `{"error": "avatar 'X' failed to boot: ...", "address": ..., "pid": ...}`
- `slow` → `{"status": "ok", ..., "warning": "avatar still booting after 5s — check .agent.heartbeat"}`
- `ok` → `{"status": "ok", "address": ..., "agent_name": ..., "type": ..., "pid": ...}`

### Constants

| Name | Value | Source line |
|------|-------|-------------|
| `_BOOT_WAIT_SECS` | `5.0` | 493 |
| `_BOOT_POLL_INTERVAL` | `0.1` | 494 |

### Error capture (lines 500-536)

- Stderr redirected to `logs/spawn.stderr` via Popen's `stderr=` parameter.
- On failure, last 2000 bytes of stderr are read and included in the error message.

## Source

| What | File | Line(s) |
|------|------|---------|
| `_wait_for_boot` | `src/lingtai/core/avatar/__init__.py` | 318-352 |
| `_BOOT_WAIT_SECS` / `_BOOT_POLL_INTERVAL` | same | 493-494 |
| `boot_status` ledger field | same | 269-278 |
| Result construction (ok/slow/failed) | same | 281-316 |
| Child writes `.agent.heartbeat` | `src/lingtai_kernel/base_agent.py` | 718-719 |
| Heartbeat loop thread start | same | 686-696 |

## Related

- `handshake-files` — what `.agent.heartbeat` contains and how liveness is determined.
- `spawn` — what launches the child process being verified.
