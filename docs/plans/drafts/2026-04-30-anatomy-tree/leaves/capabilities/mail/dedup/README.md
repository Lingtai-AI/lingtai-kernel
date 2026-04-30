# Dedup — Duplicate Message Defense

> **Capability:** email
> **Also at:** `pilot-leaf/mail-protocol/send/dedup/` (protocol-level, no test.md)

---

## What

The dedup gate prevents runaway auto-reply loops where two agents endlessly reply with identical payloads. It sits in the `EmailManager` wrapper (capability layer), **before** any Mailman thread is spawned. Once a message passes the gate, delivery proceeds without further dedup checks.

The mechanism: an in-memory counter tracks consecutive identical messages per recipient address. The count reaches the threshold → the send is blocked with a warning.

---

## Contract

### Data structure

```python
# EmailManager.__init__  (core/email/__init__.py:240-241)
self._last_sent: dict[str, tuple[str, int]] = {}  # addr → (text, count)
self._dup_free_passes = 2
```

### Gate logic (lines 804-824)

For each address in `(to + cc + bcc)`:
- If `_last_sent[addr]` exists AND message text matches AND count ≥ `_dup_free_passes` (2)
  → return `{"status": "blocked", "warning": "Identical message already sent to: ..."}`

If **any one** recipient triggers the gate, the **entire send** is aborted — no recipients receive anything.

### Counter update after delivery (lines 894-900)

For each recipient address:
- Same text as previous → increment count
- Different text or no prior entry → reset to `(text, 1)`

### Bypass: scheduled sends

When `args.get("_schedule")` is truthy, the gate is skipped entirely. Scheduled messages represent intentional recurring communication, not accidental loops.

### Lifecycle

- **In-memory only** — `_last_sent` is a Python dict, not persisted to disk.
- **Agent-lifetime scope** — cleared on restart (fresh `EmailManager` instance).
- **No cross-agent awareness** — each agent tracks its own sends only.

---

## Source

All references: `lingtai-kernel/src/`

| What | File | Line(s) |
|---|---|---|
| `_last_sent` + `_dup_free_passes` init | `lingtai/core/email/__init__.py` | 240-241 |
| Gate check logic | `lingtai/core/email/__init__.py` | 804-824 |
| Counter update after delivery | `lingtai/core/email/__init__.py` | 894-900 |
| Scheduled-send bypass (`_schedule` flag) | `lingtai/core/email/__init__.py` | 806-808 |

---

## Related

| Leaf | Relationship |
|---|---|
| `pilot-leaf/mail-protocol/send/dedup` | Protocol-level version of same leaf; includes architectural note on UUID unlinking |
| `mail/peer-send` | Peer-send subjects to the same gate; aborted sends produce no outbox entry |
| `mail/scheduling` | Scheduled sends bypass the gate via the `_schedule` flag |
