# Peer-Send — Normal Delivery

> **Capability:** email
> **Also at:** `pilot-leaf/mail-protocol/send/peer-send/` (protocol-level, no test.md)

---

## What

Peer-send is the normal delivery path — sending mail to another agent in the same `.lingtai/` network (or, via `abs` mode, to any absolute path). Self-send **diverges from** this path. Transport: `EmailManager` → outbox → Mailman → `FilesystemMailService.send()` → recipient's inbox.

---

## Contract

### End-to-end flow

```
EmailManager._send()                           [core/email/__init__.py:787-907]
  ├─ dedup gate (see mail/dedup)
  ├─ build base_payload + identity card
  ├─ for each recipient (to + cc + bcc):
  │     ├─ _persist_to_outbox() → outbox/{uuid}/message.json
  │     └─ spawn Mailman thread (skip_sent=True)
  └─ write sent/{uuid}/message.json (wrapper-level archive)

Mailman thread                                 [intrinsics/mail.py:317-358]
  ├─ sleep(delay)
  ├─ address is NOT self → agent._mail_service.send(address, payload, mode)
  ├─ cleanup: delete outbox entry (skip_sent=True)
  └─ on failure: inject bounce notification into agent inbox

FilesystemMailService.send()                   [services/mail.py:131-209]
  ├─ resolve address (peer: sibling dir, abs: literal path)
  ├─ handshake: is_agent() + is_alive()
  │     ├─ .agent.json must exist
  │     └─ .agent.heartbeat must be fresh (< 2s) OR agent is human
  ├─ create recipient/inbox/{uuid}/
  ├─ inject _mailbox_id + received_at
  ├─ handle attachments (copy + rewrite paths)
  ├─ atomic write: message.json.tmp → os.replace() → message.json
  └─ return None (success) or error string
```

### Address resolution (handshake.py:13-22)

| Mode | Resolution | Use case |
|---|---|---|
| `peer` (default) | `resolve_address(name, .lingtai/)` → sibling directory | Same-network delivery |
| `abs` | `Path(address)` as-is | Cross-network, same machine |

### Handshake (handshake.py:25-55)

Two checks gate delivery:
1. **`is_agent(dir)`** — `{dir}/.agent.json` exists
2. **`is_alive(dir)`** — `.agent.heartbeat` mtime < 2 seconds; OR agent is human (`admin == null`, always alive)

Failure → Mailman logs and injects bounce notification into sender's inbox. **No retry** (at-most-once semantics).

### Attachments (services/mail.py:180-194)

Paths must exist on sender's filesystem. Files are **copied** into `recipient/inbox/{uuid}/attachments/`; payload paths are **rewritten** to recipient-local copies. Missing → entire send fails.

### Wrapper-level sent record

The EmailManager writes **one** `sent/{uuid}/message.json` before spawning Mailman — the "one email per action" record, preserving BCC and schedule metadata. The Mailman inbox UUID is independent (see `mail/dedup` "Architectural note").

---

## Source

All references: `lingtai-kernel/src/`

| What | File | Line(s) |
|---|---|---|
| Wrapper send entry + outbox persist | `lingtai/core/email/__init__.py` | 862-874 |
| Wrapper sent-record write | `lingtai/core/email/__init__.py` | 876-892 |
| Mailman thread (routing) | `lingtai_kernel/intrinsics/mail.py` | 317-358 |
| Mailman non-self → transport | `lingtai_kernel/intrinsics/mail.py` | 338-340 |
| FilesystemMailService.send() | `lingtai_kernel/services/mail.py` | 131-209 |
| resolve_address | `lingtai_kernel/handshake.py` | 13-22 |
| is_agent | `lingtai_kernel/handshake.py` | 25-27 |
| is_alive | `lingtai_kernel/handshake.py` | 39-55 |
| Attachment copy + rewrite | `lingtai_kernel/services/mail.py` | 180-194 |
| Atomic write | `lingtai_kernel/services/mail.py` | 198-207 |
| Bounce notification | `lingtai_kernel/intrinsics/mail.py` | 360-371 |
| Identity card injection | `lingtai/core/email/__init__.py` | 850 |

---

## Related

| Leaf | Relationship |
|---|---|
| `pilot-leaf/mail-protocol/send/peer-send` | Protocol-level leaf with more detail on the vs-self-send distinction |
| `mail/dedup` | Gate runs before any Mailman spawn; peer-send subject to same rules |
| `mail/atomic-write` | The tmp+rename mechanism peer-send uses for inbox delivery |
| `mail/identity-card` | Every peer-send carries the sender's identity card |
| `mail/scheduling` | Each scheduled send routes through `_send()` → Mailman → peer-send path |
