# Mail / Email — Capability Architecture Index

> **Subsystem:** capabilities / mail
> **Source files:** `lingtai/core/email/__init__.py`, `lingtai_kernel/intrinsics/mail.py`, `lingtai_kernel/services/mail.py`, `lingtai_kernel/handshake.py`

---

## The Full Picture

The email system spans three layers. Understanding which layer owns what prevents confusion when reading source code — the same action (e.g. "send") touches all three, but each layer has distinct responsibilities.

```
┌─────────────────────────────────────────────────────────┐
│  Capability layer   core/email/__init__.py              │
│  EmailManager                                           │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌──────────┐  │
│  │  dedup   │ │scheduling│ │  contacts │ │identity  │  │
│  │  gate    │ │  loop    │ │  manager  │ │injection │  │
│  └────┬─────┘ └────┬─────┘ └───────────┘ └──────────┘  │
│       │             │                                   │
│       ▼             ▼                                   │
│  _send() → outbox/{uuid}/ → spawn Mailman thread        │
│         → sent/{uuid}/    (wrapper archive)              │
├─────────────────────────────────────────────────────────┤
│  Intrinsic layer   intrinsics/mail.py                   │
│  _mailman()                                             │
│  ┌────────────────────────────────────────────┐         │
│  │ self-send?  ──yes──▶ _persist_to_inbox()   │         │
│  │     │                  + _wake_nap()        │         │
│  │     no                                    │         │
│  │     ▼                                     │         │
│  │ mail_service.send()  ──fail──▶ bounce      │         │
│  │     │                         notification │         │
│  │     ok                                    │         │
│  │     ▼                                     │         │
│  │ cleanup outbox                            │         │
│  └────────────────────────────────────────────┘         │
├─────────────────────────────────────────────────────────┤
│  Transport layer   services/mail.py                     │
│  FilesystemMailService                                  │
│  ┌────────────────────────────────────────────┐         │
│  │ resolve_address()                          │         │
│  │ is_agent() + is_alive()  ◀── handshake     │         │
│  │ create inbox/{uuid}/                       │         │
│  │ inject _mailbox_id + received_at           │         │
│  │ copy attachments + rewrite paths           │         │
│  │ atomic write: .tmp → os.replace()          │         │
│  │ wake recipient polling listener            │         │
│  └────────────────────────────────────────────┘         │
└─────────────────────────────────────────────────────────┘
```

## Leaf Map

| Leaf | What it covers | Layer | Lines |
|---|---|---|---|
| [`mailbox-core/`](mailbox-core/) | Directory layout, creation semantics, field schema, molt survival | Foundation (all layers) | 78 |
| [`dedup/`](dedup/) | In-memory duplicate message gate (`_last_sent` counter, `_dup_free_passes=2`) | Capability | 71 |
| [`atomic-write/`](atomic-write/) | `tmp` → `os.replace()` pattern across 4 call sites | Transport + capability | 95 |
| [`peer-send/`](peer-send/) | Full send journey: wrapper → outbox → Mailman → transport → inbox | All three | 99 |
| [`identity-card/`](identity-card/) | `_build_manifest()` at send, `_inject_identity()` at read | Capability + intrinsic | 97 |
| [`scheduling/`](scheduling/) | Recurring dispatch: `schedule.json`, at-most-once, state machine, startup reconciliation | Capability | 100 |

### Also relevant (protocol-level pilot leaves, no test.md)

These exist at `pilot-leaf/mail-protocol/send/` and cover the same mechanics from the protocol perspective:

- `pilot-leaf/mail-protocol/send/self-send/` — self-send shortcut (with test.md)
- `pilot-leaf/mail-protocol/send/dedup/` — protocol-level dedup + UUID unlinking note
- `pilot-leaf/mail-protocol/send/atomic-write/` — race condition analysis
- `pilot-leaf/mail-protocol/send/peer-send/` — protocol-level peer delivery

## One Send, Three Lanes

A single `email(action="send")` call forks into parallel work across threads:

```
EmailManager._send()                     [main thread]
  │
  ├─ 1. dedup gate ──────── ┐
  ├─ 2. identity card ───── ┤
  ├─ 3. per-recipient:      │
  │     _persist_to_outbox()│
  │     spawn Mailman() ────┼──── [daemon thread per recipient]
  │                         │       ├─ sleep(delay)
  │                         │       ├─ self? → _persist_to_inbox + wake
  │                         │       └─ peer? → FilesystemMailService.send()
  │                         │                  ├─ handshake
  │                         │                  ├─ atomic write to inbox
  │                         │                  └─ cleanup outbox
  ├─ 4. sent/{uuid}/ ──────┘  (wrapper archive, independent UUID)
  └─ 5. return to agent
```

The **wrapper UUID** (step 4) and the **Mailman UUID** (step 3, for the actual inbox entry) are independent. There is no cross-reference field. Content matching is the only way to correlate sent ↔ received.

## Key Design Decisions

1. **At-most-once, not exactly-once.** Scheduling increments `sent` before send. A crash between persist and dispatch loses one message by design. Retries would require a delivery-confirmation protocol that doesn't exist.

2. **Atomic write everywhere except self-send.** Self-send writes directly to inbox (`write_text()`) because there's no concurrent polling listener to race against — the Mailman thread calls `_wake_nap()` directly.

3. **In-memory dedup.** `_last_sent` is a Python dict, not persisted. Cleared on restart. This is intentional — dedup protects against runaway reply loops within a single agent lifetime, not cross-restart idempotency.

4. **Startup reconciliation for schedules.** All non-completed schedules are flipped to `inactive` on startup. No schedule auto-resumes. The agent must deliberately reactivate.

5. **Identity card is the sender's full manifest.** Both mail intrinsic and email capability call `agent._build_manifest()`. The receive side (`_inject_identity`) only surfaces a curated subset (name, id, language, location, is_human) in check/read summaries.

## Maintenance Tools

Two scripts in the parent directory (`../../`) keep this documentation honest:

| Script | Purpose | Usage |
|--------|---------|-------|
| `verify-source-refs.py` | Check every `## Source` table: file existence, line bounds, identifier presence (±10 line tolerance) | `python3 verify-source-refs.py leaves/capabilities/mail/ src` |
| `mail-arch-diagram.py` | Generate living Mermaid architecture diagram from source AST | `python3 mail-arch-diagram.py src > mail-arch-diagram.md` |

Run `verify-source-refs.py` after any source change to catch line-number drift.
Run `mail-arch-diagram.py` to regenerate the diagram when functions/classes are added or moved.
Generated output: [`mail-arch-diagram.md`](mail-arch-diagram.md)
