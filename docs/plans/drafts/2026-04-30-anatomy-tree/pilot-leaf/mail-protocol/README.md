# Mail Protocol

> **Protocol:** mail-protocol
> **Status:** Phase 1 pilot — anatomy tree restructure
> **Leaves:** 5 built, 4 reserved
> **Source:** exploded from `lingtai-kernel-anatomy/reference/mail-protocol.md`

---

## Overview

The LingTai mail subsystem is a filesystem-based mailbox with four architectural layers:

```
┌──────────────────────────────────────────────────────┐
│  EmailManager  (core/email/__init__.py, ~1300 lines) │  ← capability layer
│    search, contacts, schedule, CC/BCC, dedup         │
├──────────────────────────────────────────────────────┤
│  Mail intrinsic  (intrinsics/mail.py, ~523 lines)    │  ← kernel intrinsic
│    send / check / read / search / delete              │
├──────────────────────────────────────────────────────┤
│  FilesystemMailService  (services/mail.py, ~374 lines)│  ← transport layer
│    filesystem delivery, polling listener, outbox→sent │
├──────────────────────────────────────────────────────┤
│  Handshake  (handshake.py, ~67 lines)                │  ← address resolution
│    resolve_address, is_agent, is_alive                │
└──────────────────────────────────────────────────────┘
```

---

## Send lifecycle (leaf map)

```
Agent invokes email tool
  │
  ▼
┌─────────┐
│  dedup  │ ← [send/dedup] — content-based loop prevention
└────┬────┘
     │ pass
     ▼
┌──────────────────┐
│  outbox persist  │   _persist_to_outbox()
└────┬─────────────┘
     │
     ├──[ self? ]──┐
     │             ▼
     │     ┌────────────┐
     │     │ self-send  │ ← [send/self-send] — skip transport, write inbox directly
     │     └────┬───────┘
     │          │ _wake_nap("mail_arrived") — zero latency
     │          ▼
     │       inbox ✓
     │
     │ not self
     ▼
┌──────────────┐
│  Mailman     │   daemon thread, one per message
│  thread      │
└────┬─────────┘
     │
     ▼
┌────────────────────────┐
│ FilesystemMailService  │ ← [send/peer-send] — normal delivery path
│   .send()              │
│   ├─ handshake         │ ← [receive/handshake] (reserved)
│   ├─ copy attachments  │
│   └─ atomic write      │ ← [send/atomic-write] — tmp → os.replace()
└────────┬───────────────┘
         │
         ▼
┌──────────────────────┐
│  recipient/inbox/    │
│  {uuid}/message.json │
└────────┬─────────────┘
         │
         ▼
┌──────────────────────┐
│  polling listener    │ ← [receive/polling-listener] — 0.5s scan, Phase 1 + 2
│  _on_mail_received   │
│  _wake_nap           │ ← [wake/nap-break] (reserved)
└──────────────────────┘
```

---

## Leaf index

### Built leaves

| Leaf | Lines | Has test? | Description |
|---|---|---|---|
| [`send/self-send/`](send/self-send/) | 92 | ✅ | Bypass transport, write directly to inbox, wake immediately |
| [`send/peer-send/`](send/peer-send/) | 111 | — | Normal delivery via FilesystemMailService |
| [`send/dedup/`](send/dedup/) | 90 | — | Content-based duplicate message gate |
| [`send/atomic-write/`](send/atomic-write/) | 68 | — | Two-phase tmp→rename to prevent polling races |
| [`receive/polling-listener/`](receive/polling-listener/) | 96 | — | Background daemon scanning inbox + pseudo-agent outboxes |

**Total:** 457 lines across 5 leaves (+ 79-line test + 25-line asset = 561 total)

### Reserved leaves (not yet built)

These names appear in cross-references but the leaves do not exist yet. They are design placeholders — build them when the corresponding concept needs its own spec.

| Planned leaf | Referenced by | What it would cover |
|---|---|---|
| `send/scheduled-send` | dedup | Recurring send with interval + count, at-most-once guarantee, startup reconciliation |
| `receive/handshake` | peer-send | `resolve_address`, `is_agent`, `is_alive` — the checks that gate delivery |
| `receive/pseudo-agent` | polling-listener | Phase 2 claiming — atomic rename outbox→sent for agents without their own poller |
| `wake/nap-break` | self-send, polling-listener | `_wake_nap()` mechanism — `threading.Event.set()`, nap interrupt semantics |

---

## Cross-reference graph

```
                    ┌──────────┐
                    │  dedup   │
                    └──┬───┬──┘
                       │   │
            ┌──────────┘   └──────────┐
            ▼                         ▼
     ┌────────────┐           ┌──────────────┐
     │ self-send  │──────────▶│  peer-send   │
     └─────┬──────┘           └──────┬───────┘
           │                         │
           ▼                         ▼
    ┌──────────────┐        ┌──────────────┐
    │ nap-break *  │        │ atomic-write │
    └──────────────┘        └──────┬───────┘
           │                       │
           └───────────┬───────────┘
                       ▼
              ┌─────────────────┐
              │ polling-listener│
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │ pseudo-agent *  │
              └─────────────────┘

    * = reserved (not yet built)
    ── = cross-reference (bidirectional in Related sections)
```

---

## For newcomers

1. **Start here.** You're reading the index.
2. **Understand the flow.** The lifecycle diagram above traces one message from send to receive.
3. **Dive into a leaf.** Pick any leaf from the table — each has `## What` (plain English), `## Contract` (precise rules), `## Source` (file:line in kernel), and `## Related` (pointers to siblings).
4. **Run a test.** `send/self-send` has a `test.md` — spawn a shallow avatar with it as the prompt, observe the filesystem, verify the contract.

---

## Relationship to the old flat reference

The old `reference/mail-protocol.md` (412 lines, single monolithic file) is the source material for this tree. During Phase 1, both structures coexist. After Phase 4, the flat reference will be deleted and this tree will be the canonical source.

**Migration map** (old sections → new leaves):

| Old section | New leaf |
|---|---|
| § Self-Send Shortcut (Stage 5) | `send/self-send/` |
| § Send (Stage 1) + § Transport (Stage 2) + § Delivery (Stage 3) | `send/peer-send/` + `send/atomic-write/` |
| § Receive (Stage 4) | `receive/polling-listener/` |
| § Identity Card | distributed across send leaves (payload construction) |
| § Scheduled Sending | `send/scheduled-send/` (reserved) |
| § Wake Mechanism | `wake/nap-break/` (reserved) |
| § Advanced Features (CC/BCC, Delay, Attachments, Search) | woven into `send/peer-send/` |
