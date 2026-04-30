# Polling Listener

> **Protocol:** mail-protocol / receive / polling-listener

---

## What

The polling listener is a background daemon thread in `FilesystemMailService` that detects new messages in an agent's inbox by scanning every 0.5 seconds. It is the **receive side** of all external mail delivery — peer-send, abs-send, and pseudo-agent claiming all rely on it.

Self-send is the only delivery path that **bypasses** the polling listener entirely.

---

## Contract

### Lifecycle

1. **Startup snapshot** — when `listen()` is called, all existing `{uuid}/` directories in `inbox/` are added to the `_seen` set. This prevents re-notification of messages that arrived before the listener started.

2. **Poll loop** — a daemon thread runs `_poll_loop()` in a `while not _poll_stop.is_set()` loop with `wait(0.5)` between iterations.

3. **Shutdown** — `_poll_stop.set()` causes the loop to exit on the next iteration. The thread is joined with a 5-second timeout.

### Two-phase scan

Each poll cycle runs two phases:

**Phase 1 — own inbox:**
```
for each {uuid}/ in own inbox/:
    skip if not a directory
    skip if uuid in _seen
    if message.json exists:
        parse → on_message(payload)
        add uuid to _seen
```

**Phase 2 — pseudo-agent outboxes:**
```
for each subscribed pseudo-agent directory:
    _poll_pseudo_outbox(pseudo_dir, on_message)
```

### Pseudo-agent claiming

Pseudo-agents (agents without their own polling thread, e.g. human-controlled agents) store outgoing messages in their `outbox/`. The listener claims messages addressed to itself via atomic rename:

1. Read `outbox/{uuid}/message.json`.
2. Check if `this service's address` is in the `to` field.
3. If yes:
   - Pre-mark uuid in `_seen` (so Phase 1 won't re-dispatch).
   - Write `message.json` atomically into own `inbox/{uuid}/` (tmp + `os.replace()`).
   - Atomically rename `outbox/{uuid}/` → `sent/{uuid}/` to claim.
   - Dispatch via `on_message(payload)`.
4. If the rename fails (another poller claimed it), delete the speculative inbox copy, clear from `_seen`, and skip.

### Wake mechanism

The `on_message` callback (set by the agent runtime) triggers `_on_mail_received()` → `_on_normal_mail()` → injects a notification into the agent's internal inbox queue and calls `_wake_nap("mail_arrived")`.

For self-send, `_wake_nap` is called directly in the Mailman thread — zero latency. For external delivery, the worst-case latency is **0.5 seconds** (one poll interval).

### `_seen` set

- **Type:** `set[str]` of UUID directory names.
- **Scope:** in-memory only — survives across poll cycles but **not** across agent restarts.
- **On restart:** re-snapshotted from existing `inbox/` contents (startup snapshot).
- **Purpose:** prevents re-notification of already-processed messages.

---

## Source

All references are to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| listen() — startup snapshot + poll loop | `lingtai_kernel/services/mail.py` | 215–258 |
| _seen set initialization | `lingtai_kernel/services/mail.py` | 116, 222–226 |
| 0.5s poll interval | `lingtai_kernel/services/mail.py` | 255 |
| _poll_pseudo_outbox() | `lingtai_kernel/services/mail.py` | 260–284 |
| _on_mail_received callback | `lingtai_kernel/base_agent.py` | 521–528 |
| _on_normal_mail (wake + notify) | `lingtai_kernel/base_agent.py` | 530–545 |
| _wake_nap (shared wake mechanism) | `lingtai_kernel/base_agent.py` | 626–629 |

---

## Related

| Sibling leaf | Relationship |
|---|---|
| `mail-protocol/send/peer-send` | Peer-send is the sender; polling-listener is the receiver |
| `mail-protocol/send/atomic-write` | Atomic write exists because the polling listener could read mid-write |
| `mail-protocol/send/self-send` | Self-send bypasses the polling listener entirely (direct _wake_nap) |
| `mail-protocol/receive/pseudo-agent` | Pseudo-agent claiming is Phase 2 of the polling listener |
