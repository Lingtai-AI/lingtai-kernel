# Self-Send

> **Protocol:** mail-protocol / send / self-send
> **Stage:** 5 (of the delivery lifecycle)

---

## What

When an agent sends mail to itself (address matches its own working directory name, full path, or mail-service address), the Mailman thread **bypasses the filesystem transport entirely** and writes directly to its own inbox. This is the self-send shortcut.

Self-send exists for two reasons:

1. **Zero-latency wake.** External delivery depends on the polling listener (0.5 s interval). Self-send calls `_wake_nap("mail_arrived")` directly in the Mailman thread, breaking any active nap immediately.
2. **No handshake overhead.** The normal transport validates `.agent.json` existence and heartbeat freshness on the recipient directory. Self-send skips all of this — the agent already knows it exists.

Self-send is the mechanism behind the "Mail as Time Machine" patterns: memory anchors (immediate self-send), time capsules (self-send with delay), and self-addressed scheduled messages.

---

## Contract

### Detection

`_is_self_send(agent, address)` returns `True` when the delivery address matches any of:

| Match type | Check |
|---|---|
| Directory name (relative) | `address == agent._working_dir.name` |
| Full path (legacy absolute) | `address == str(agent._working_dir)` |
| Mail-service address | `address == agent._mail_service.address` (if service configured) |

The check is case-sensitive and exact — no normalization, no fuzzy match.

### Delivery

When self-send is detected inside `_mailman()`:

1. **Skip transport** — `FilesystemMailService.send()` is never called.
2. **Write to inbox** — `_persist_to_inbox(agent, payload)` creates `mailbox/inbox/{uuid}/message.json`. Unlike the normal transport path which uses atomic write (`tmp` → `os.replace()`), self-send writes directly via `path.write_text()` — there is no polling listener to race.
3. **Wake immediately** — `agent._wake_nap("mail_arrived")` is called, setting the nap Event. If the agent is napping, it wakes on the next iteration.
4. **Archive** — the outbox entry is either moved to `sent/` (if `skip_sent=False`) or deleted (if `skip_sent=True`; the EmailManager wrapper always sets `skip_sent=True`).

### Message format

The self-sent `message.json` matches the standard received schema. The `received_at` timestamp is injected at write time by `_persist_to_inbox()`. The `identity` field contains the sender's manifest (which for self-send is the agent's own manifest).

### Layer split: two sent records

The EmailManager wrapper (capability layer) and the Mailman thread (intrinsic layer) each handle sent-archival independently:

- **Wrapper** (`core/email/__init__.py:876–892`) — always writes `sent/{wrapper_uuid}/message.json` before dispatching to Mailman. This is the "one email" view: one record per send action, regardless of recipient count.
- **Mailman** (`intrinsics/mail.py:350–355`) — for self-send with `skip_sent=True` (which the wrapper always sets), deletes the outbox entry. No second sent record from this layer.

Net result: a self-send produces **one** inbox entry (Mailman UUID) and **one** sent entry (wrapper UUID), with different UUIDs. These UUIDs are **not cross-referenced** — see `mail-protocol/send/dedup` "Architectural note" for details on the unlinking and its implications.

### What self-send does NOT do

- Does not trigger the polling listener's `_seen` set tracking (the listener won't pick it up — it's already in the inbox before the listener scans).
- Does not validate handshake (`.agent.json`, heartbeat).
- Does not copy attachments (self-send has no attachment path rewriting — the paths already point to the agent's own filesystem).
- Does not use atomic write for inbox delivery (`write_text()` directly, no `tmp` → `os.replace()`).

---

## Source

All references are to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Self-send detection | `lingtai_kernel/intrinsics/mail.py` | 233-245 (`_is_self_send`) |
| Self-send branch in Mailman | `lingtai_kernel/intrinsics/mail.py` | 334-336 |
| Direct inbox write | `lingtai_kernel/intrinsics/mail.py` | 248-264 (`_persist_to_inbox`) |
| Wake-nap implementation | `lingtai_kernel/base_agent.py` | 626-629 (`_wake_nap`) |
| Manifest construction | `lingtai_kernel/base_agent.py` | 1477-1499 (`_build_manifest`) |
| Outbox persistence + Mailman spawn | `lingtai/core/email/__init__.py` | 862-874 |
| Outbox cleanup (skip_sent) | `lingtai_kernel/intrinsics/mail.py` | 350-355 |
| Inbox directory helper | `lingtai_kernel/intrinsics/mail.py` | 119-121 (`_inbox_dir`) |
| Wrapper sent-record write | `lingtai/core/email/__init__.py` | 876-892 |

---

## Related

| Sibling leaf | Relationship |
|---|---|
| `mail-protocol/send/peer-send` | The normal delivery path self-send diverges from |
| `mail-protocol/send/dedup` | Dedup gate runs before the Mailman thread is spawned — applies to self-send too. Also documents the UUID unlinking between sent/inbox/outbox layers |
| `mail-protocol/receive/polling-listener` | Self-send bypasses the polling listener; the message lands before the next poll cycle |
| `mail-protocol/send/atomic-write` | Normal delivery uses atomic write; self-send does not (no race to protect against) |
| `mail-protocol/wake/nap-break` | `_wake_nap` is the shared wake mechanism; self-send calls it directly instead of via the polling listener callback |
