# Peer-Send (Normal Delivery)

> **Protocol:** mail-protocol / send / peer-send

---

## What

Peer-send is the **normal** delivery path — sending mail to another agent in the same `.lingtai/` network (or, via `abs` mode, to an agent at an arbitrary absolute path on the same machine). It is the path self-send **diverges from** and the default for any non-self address.

The journey: `EmailManager` → outbox → `Mailman` thread → `FilesystemMailService.send()` → recipient's `inbox/`.

---

## Contract

### End-to-end flow

```
EmailManager.send()                           [core/email/__init__.py]
  ├─ dedup gate (see mail-protocol/send/dedup)
  ├─ build base_payload + identity card
  ├─ for each recipient:
  │     ├─ _persist_to_outbox() → outbox/{uuid}/message.json
  │     └─ spawn Mailman thread (skip_sent=True)
  └─ write sent/{uuid}/message.json (wrapper-level archive)

Mailman thread                                [intrinsics/mail.py:317-358]
  ├─ sleep(delay)
  ├─ address is NOT self → falls through to:
  │     agent._mail_service.send(address, payload, mode)
  ├─ cleanup: delete outbox entry (skip_sent=True)
  └─ on failure: inject bounce notification into agent inbox

FilesystemMailService.send()                  [services/mail.py:131-209]
  ├─ resolve address (peer mode: sibling dir, abs mode: literal path)
  ├─ handshake: is_agent() + is_alive()
  │     ├─ .agent.json must exist
  │     └─ .agent.heartbeat must be fresh (< 2s) OR agent is human
  ├─ create recipient/inbox/{uuid}/
  ├─ inject _mailbox_id + received_at
  ├─ handle attachments (copy + rewrite paths)
  ├─ atomic write: message.json.tmp → os.replace() → message.json
  └─ return None (success) or error string (failure)
```

### Address resolution

| Mode | Resolution | Use case |
|---|---|---|
| `peer` (default) | `resolve_address(name, .lingtai/)` → sibling directory | Same-network delivery |
| `abs` | `Path(address)` as-is | Cross-network, same machine |

### Handshake

Two checks gate delivery to `abs`/`peer` targets. Both must pass or the transport returns an error (which the Mailman converts to a bounce notification).

1. **`is_agent(dir)`** — `{dir}/.agent.json` exists
2. **`is_alive(dir)`** — `.agent.heartbeat` is fresh (mtime < 2 seconds) OR the agent is human (`admin == null`, always considered alive)

**Failure semantics are soft** — the Mailman logs the error and injects a bounce notification, but the message is NOT retried (at-most-once).

### Attachments

1. Each path in `message["attachments"]` must exist and be readable.
2. Files are **copied** (not moved) into `recipient/inbox/{uuid}/attachments/`.
3. Paths inside the message payload are **rewritten** to the recipient-local copy.

If any attachment is missing, the entire send fails with an error string.

### vs self-send

| Aspect | Peer-send | Self-send |
|---|---|---|
| Transport | `FilesystemMailService.send()` | `_persist_to_inbox()` direct |
| Handshake | `.agent.json` + heartbeat | Skipped |
| Atomic write | Yes (`tmp` → `os.replace()`) | No (`write_text()` directly) |
| Attachments | Copied + rewritten | N/A (own filesystem) |
| Polling latency | Up to 0.5 s | Zero (direct `_wake_nap`) |

---

## Source

All references are to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Wrapper send entry + outbox persist | `lingtai/core/email/__init__.py` | 862–874 |
| Wrapper sent-record write | `lingtai/core/email/__init__.py` | 876–892 |
| Mailman thread (routing) | `lingtai_kernel/intrinsics/mail.py` | 317–358 |
| Mailman non-self branch → transport | `lingtai_kernel/intrinsics/mail.py` | 338–340 |
| FilesystemMailService.send() | `lingtai_kernel/services/mail.py` | 131–209 |
| resolve_address | `lingtai_kernel/handshake.py` | 13–22 |
| is_agent | `lingtai_kernel/handshake.py` | 25–27 |
| is_alive | `lingtai_kernel/handshake.py` | 39–55 |
| Attachment copy + rewrite | `lingtai_kernel/services/mail.py` | 180–194 |
| Atomic write | `lingtai_kernel/services/mail.py` | 198–205 |
| Bounce notification | `lingtai_kernel/intrinsics/mail.py` | 360–371 |

---

## Related

| Sibling leaf | Relationship |
|---|---|
| `mail-protocol/send/self-send` | The shortcut that peer-send is the "default" of — self-send bypasses transport entirely |
| `mail-protocol/send/dedup` | Dedup gate runs before any Mailman spawn; peer-send subject to same rules |
| `mail-protocol/send/atomic-write` | The tmp-then-rename mechanism peer-send uses for inbox delivery |
| `mail-protocol/receive/polling-listener` | The polling listener detects peer-sent messages (up to 0.5 s latency) |
| `mail-protocol/receive/handshake` | The is_agent + is_alive checks that gate delivery |
