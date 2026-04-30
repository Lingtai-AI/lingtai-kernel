# Atomic Write

> **Protocol:** mail-protocol / send / atomic-write

---

## What

When `FilesystemMailService` delivers a message to a recipient's inbox, it uses a **two-phase write** to prevent the polling listener from reading a half-written `message.json`. This is the atomic write pattern.

The polling listener (which scans every 0.5 s) looks for `{uuid}/message.json` as its trigger. If the sender wrote directly to `message.json` and the listener happened to scan mid-write, it would parse a truncated JSON file — a silent data corruption. The atomic write prevents this by ensuring the listener only ever sees a complete file.

---

## Contract

### Mechanism

```
tmp_path = msg_dir / "message.json.tmp"
final_path = msg_dir / "message.json"

tmp_path.write_text(json.dumps(message, ...))
os.replace(str(tmp_path), str(final_path))   # atomic on POSIX
```

1. **Phase 1 — write to `.tmp`**: the full JSON payload is serialized and written to `message.json.tmp`. The polling listener ignores this file (it only looks for `message.json`).
2. **Phase 2 — atomic rename**: `os.replace()` renames `.tmp` to `message.json` in a single filesystem operation. On POSIX, this is atomic — there is no instant where `message.json` exists but is incomplete.

After `os.replace()` returns, `message.json.tmp` no longer exists (it was renamed, not copied).

### When it's used

- **`FilesystemMailService.send()`** — all external delivery (peer-send, abs-send) uses atomic write.
- **Not used by** `_persist_to_inbox()` (self-send) — self-send writes directly with `write_text()` because there is no polling listener to race against (the self-send path calls `_wake_nap()` directly in the same thread).

### What protects against

| Race | Without atomic write | With atomic write |
|---|---|---|
| Polling listener reads mid-write | Truncated JSON → parse error or silent corruption | Listener sees `message.json` only after rename — always complete |
| Process crash during write | Partial `message.json` exists — listener may try to parse | `.tmp` file left behind — listener ignores it; orphan cleanup is NOT automatic |

### What it does NOT protect against

- **Process crash after `.tmp` write, before rename**: the `.tmp` file becomes an orphan. There is no reconciliation on restart — the message is lost (at-most-once semantics by design).
- **Non-POSIX filesystems**: `os.replace()` is atomic on POSIX (Linux, macOS); on Windows it may not be, though LingTai's primary targets are POSIX.

---

## Source

| What | File | Line(s) |
|---|---|---|
| Atomic write implementation | `lingtai_kernel/services/mail.py` | 198–207 |
| `.tmp` path construction | `lingtai_kernel/services/mail.py` | 199 |
| `os.replace()` call | `lingtai_kernel/services/mail.py` | 205 |
| Polling listener (consumer side) | `lingtai_kernel/services/mail.py` | 230–239 |

---

## Related

| Sibling leaf | Relationship |
|---|---|
| `mail-protocol/send/peer-send` | Peer-send is the caller; atomic write is the mechanism |
| `mail-protocol/send/self-send` | Self-send does NOT use atomic write (writes directly to inbox) |
| `mail-protocol/receive/polling-listener` | The listener is the reason atomic write exists — it scans for `message.json` |
