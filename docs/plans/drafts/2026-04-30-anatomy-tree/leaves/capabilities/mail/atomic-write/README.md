# Atomic Write — Inbox Delivery Safety

> **Capability:** email (and mail intrinsic)
> **Also at:** `pilot-leaf/mail-protocol/send/atomic-write/` (protocol-level, no test.md)

---

## What

When `FilesystemMailService` delivers a message to a recipient's inbox, it uses a **two-phase write** to prevent the polling listener from reading a half-written `message.json`. This applies to peer-send, abs-send, and the pseudo-agent outbox claim path. Self-send is exempt (direct `write_text()`).

The polling listener scans every 0.5 s for `message.json`. Without atomic write, a mid-write scan would parse truncated JSON — silent data corruption.

---

## Contract

### Mechanism (services/mail.py:198-207)

```
tmp_path = msg_dir / "message.json.tmp"
final_path = msg_dir / "message.json"

tmp_path.write_text(json.dumps(message, ...))
os.replace(str(tmp_path), str(final_path))   # atomic on POSIX
```

1. **Phase 1 — write `.tmp`**: Full JSON serialized to `message.json.tmp`. Listener ignores this file.
2. **Phase 2 — atomic rename**: `os.replace()` renames `.tmp` → `message.json`. On POSIX this is atomic — no instant where `message.json` exists but is incomplete.

After rename, `.tmp` no longer exists (renamed, not copied).

### Also used in schedule writes (core/email/__init__.py:599-613)

The `_write_schedule()` helper uses the same tmp+replace pattern for `schedule.json`:
```python
fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
os.write(fd, json.dumps(record, ...).encode())
os.close(fd)
os.replace(tmp, str(path))
```

### Also used in read-tracking (intrinsics/mail.py:174-181)

`_save_read_ids()` uses the same pattern for `read.json`:
```python
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(sorted(ids)))
os.replace(str(tmp), str(path))
```

### Where it's used

| Location | File | Pattern |
|---|---|---|
| Peer/abs inbox delivery | `services/mail.py:198-207` | tmp + os.replace |
| Pseudo-agent outbox claim | `services/mail.py:326-331` | tmp + os.replace |
| Schedule record writes | `core/email/__init__.py:599-613` | mkstemp + os.replace |
| Read tracking updates | `intrinsics/mail.py:174-181` | tmp + os.replace |

### What it protects against

| Race | Protected? |
|---|---|
| Polling listener reads mid-write | Yes — listener only triggers on `message.json`, never on `.tmp` |
| Process crash during write | Partial `.tmp` orphaned, no `message.json` — message lost (at-most-once by design) |

### What it does NOT protect against

- **Crash after `.tmp` write, before rename**: `.tmp` becomes orphan. No reconciliation on restart.
- **Non-POSIX**: `os.replace()` atomicity depends on POSIX semantics. LingTai targets POSIX.

---

## Source

All references: `lingtai-kernel/src/`

| What | File | Line(s) |
|---|---|---|
| Atomic write (peer delivery) | `lingtai_kernel/services/mail.py` | 198-207 |
| Polling listener (consumer) | `lingtai_kernel/services/mail.py` | 230-247 |
| Pseudo-agent claim atomic write | `lingtai_kernel/services/mail.py` | 326-331 |
| Schedule record atomic write | `lingtai/core/email/__init__.py` | 599-613 |
| Read tracking atomic write | `lingtai_kernel/intrinsics/mail.py` | 174-181 |

---

## Related

| Leaf | Relationship |
|---|---|
| `pilot-leaf/mail-protocol/send/atomic-write` | Protocol-level leaf with more detail on race conditions |
| `mail/peer-send` | Peer-send is the primary caller of atomic write |
| `mail/scheduling` | Schedule record writes use the same pattern |
