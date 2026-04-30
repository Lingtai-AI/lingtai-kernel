# Wake Mechanisms

> **Subsystem:** core / wake-mechanisms
> **Layer:** Runtime agent lifecycle

---

## What

When an agent is napping (ASLEEP), three paths wake it — all converge on `threading.Event.set()` on the nap-wake event. The main loop is blocked on `inbox.get(timeout=1.0)` in ASLEEP state; a new message unblocks it → clear `_asleep` → set ACTIVE → reset uptime.

---

## Contract

### `_wake_nap(reason)` — the shared primitive

- **Location:** `base_agent.py` line 621
- **Mechanism:** `self._nap_wake.set()` (`threading.Event`)
- **Effect:** Causes `Event.wait(timeout)` to return immediately

### Three wake paths

**Path 1 — Self-send direct** (zero latency)
- Mailman detects `recipient == self`
- Writes `message.json` directly to `inbox/<uuid>/`
- Calls `_wake_nap("mail_arrived")` inline
- Source: `intrinsics/mail.py` lines 233-245, 334-336

**Path 2 — Polling listener** (≤0.5 s latency)
- Daemon thread polls `inbox/` at 0.5 s intervals
- Scans for new `message.json` not in in-memory `_seen` set
- Fires `on_message` callback → `_wake_nap("mail_arrived")`
- Source: `services/mail.py` / `base_agent.py` line 525

**Path 3 — MCP inbox poller** (≤0.5 s latency)
- Daemon thread polls `.mcp_inbox/<mcp-name>/*.json` at 0.5 s
- Validates event schema, dispatches to agent inbox
- If `wake` field is `true` (default): calls `_wake_nap("mcp_event")`
- Source: `core/mcp/inbox.py` lines 128, 242-278

### ASLEEP wake sequence

1. Message lands in `self.inbox`
2. `inbox.get(timeout=1.0)` returns (was blocked at line 913)
3. Clear `_asleep` flag → set ACTIVE → reset uptime anchor (lines 922-928)
4. Normal turn processing begins

### Non-wake scenarios

- `wake=false` in MCP event: delivered but no wake — seen on next natural poll
- `system(sleep)` / `system(lull)`: ASLEEP; wake only via message
- `system(suspend)`: SUSPENDED (process death) — mail is no-op until `cpr`

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|------|------|---------|
| `_wake_nap()` definition | `base_agent.py` | 621 |
| `_nap_wake` (Event init) | `base_agent.py` | ~77 |
| ASLEEP branch in `_run_loop()` | `base_agent.py` | 905-928 |
| Wake sequence | `base_agent.py` | 922-928 |
| `_on_normal_mail()` callback | `base_agent.py` | 525 |
| Wake from mail callback | `base_agent.py` | 545 |
| Self-send detection | `intrinsics/mail.py` | 233-245 |
| Self-send wake call | `intrinsics/mail.py` | 334-336 |
| MCP poller wake call | `core/mcp/inbox.py` | 128 |
| MCP poller class | `core/mcp/inbox.py` | 242-278 |

---

## Related

| Sibling leaf | Relationship |
|--------------|-------------|
| `mail-protocol/send/self-send` | Path 1 — direct `_wake_nap` |
| `mail-protocol/receive/polling-listener` | Path 2 — 0.5 s poll |
| `mcp-protocol/licc/events` | Path 3 — MCP inbox poller |
| `core/molt-protocol` | Post-molt agent is IDLE |
| `core/runtime-loop` | ASLEEP handling in turn loop |
