# Mailbox Core Structure

> **Subsystem:** capabilities / mail / mailbox-core
> **Layer:** Filesystem layout (structural foundation)

---

## What

Every agent has `mailbox/` in its working directory. Lazy-created: root on `EmailManager.__init__()`, subdirectories on demand. Contains message stores, read tracking, contacts, and schedules.

---

## Contract

### Directory layout

```
<workdir>/mailbox/
├── inbox/<uuid>/message.json       # received (atomic write: .tmp → os.replace)
├── outbox/<uuid>/message.json      # pending send (pre-Mailman)
├── sent/<uuid>/message.json        # sent records (wrapper layer)
├── archive/                        # archived messages
├── read.json                       # {<uuid>: true, ...} read tracking
├── contacts.json                   # [{address, name, note}, ...]
└── schedules/<uuid>/schedule.json  # recurring send plans
```

### Creation semantics

| Dir | When created | By whom |
|-----|-------------|---------|
| `mailbox/` | `EmailManager.__init__()` (line 1228) | `mkdir(parents=True, exist_ok=True)` |
| `inbox/<uuid>/` | Incoming delivery | `FilesystemMailService.send()` |
| `outbox/<uuid>/` | Outgoing send | `_persist_to_outbox()` |
| `sent/<uuid>/` | Successful send | `EmailManager` (line 878-879) |
| `archive/<uuid>/` | Archive action | `_archive()` (line 1148) |
| `schedules/<uuid>/` | Schedule creation | `_create_schedule()` (line 474-480) |

### Atomic write

External delivery: `message.json.tmp` → `os.replace()` → `message.json`. Self-send skips atomic write (direct `write_text()`).

### Per-message fields

`_mailbox_id` (UUID), `from`, `to`, `subject`, `message`, `received_at`/`sent_at` (ISO-8601 UTC), `identity` (sender manifest).

### Molt survival

Entire `mailbox/` survives molt — it is on-disk filesystem, not LLM context.

---

## Source

All references to `lingtai-kernel/src/lingtai/`.

| What | File | Line(s) |
|------|------|---------|
| Layout docstring | `core/email/__init__.py` | 1-8 |
| `_mailbox_path` | `core/email/__init__.py` | 265-266 |
| `_schedules_dir` | `core/email/__init__.py` | 269-270 |
| `_contacts_path` | `core/email/__init__.py` | 1214 |
| Root `mkdir` | `core/email/__init__.py` | 1228 |
| Sent dir creation | `core/email/__init__.py` | 878-879 |
| Self-send direct write | `intrinsics/mail.py` | 248-264 |
| `_list_inbox` / `_read_ids` | `intrinsics/mail.py` | 135 / 162 |

---

## Related

| Sibling leaf | Relationship |
|--------------|-------------|
| `mail-protocol/send/peer-send` | External delivery → `inbox/<uuid>/` with atomic write |
| `mail-protocol/send/self-send` | Self-send → `inbox/<uuid>/` without atomic write |
| `core/wake-mechanisms` | Polling listener scans `inbox/` at 0.5 s |
| `core/molt-protocol` | Mailbox survives molt |
