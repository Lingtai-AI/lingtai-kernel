# Scheduling — Recurring Email Dispatch

> **Capability:** email

---

## What

The email capability includes a background scheduler that sends recurring emails on a timer. Schedules are stored as filesystem state (`mailbox/schedules/<id>/schedule.json`), polled by a daemon thread, and guarantee **at-most-once** delivery by incrementing the `sent` counter **before** the send call.

---

## Contract

### Storage

Each schedule lives at: `mailbox/schedules/<schedule_id>/schedule.json`

```json
{
  "schedule_id": "a1b2c3d4e5f6",
  "send_payload": { "address": "...", "subject": "...", "message": "...", ... },
  "interval": 300,
  "count": 10,
  "sent": 3,
  "created_at": "2026-04-30T07:00:00Z",
  "last_sent_at": "2026-04-30T07:10:00Z",
  "status": "active"
}
```

### Status state machine

```
active  ──cancel──▶  inactive  ──reactivate──▶  active
  │                                               │
  └── (sent >= count) ──▶  completed             │
                          (terminal, no reactivation)
```

### Operations

| Action | Required params | Behavior |
|---|---|---|
| `create` | `address`, `message`, `interval`, `count` | Creates `schedule.json` with `status=active`, `sent=0` |
| `cancel` | `schedule_id` (optional — omit to cancel all) | Sets `status=inactive` |
| `reactivate` | `schedule_id` | Sets `status=active`, `last_sent_at=now` (prevents immediate fire). Refuses if `status=completed` or `sent>=count` |
| `list` | none | Returns all schedules with current status |

### At-most-once guarantee (core/email/__init__.py:719-722)

```
sent += 1
write schedule.json  (persist BEFORE send)
call self._send(...)
```

The counter is incremented and persisted **before** the send call. If the process crashes after persist but before send, the message is counted as sent but never delivered — at-most-once semantics.

### Scheduler loop (lines 665-781)

Daemon thread polling every 1s. Processes only `status=active` with `sent < count` and `last_sent_at + interval <= now`. After send: updates `last_sent_at`; if `sent >= count`, sets `status=completed`. Injects a system notification per send.

### Startup reconciliation (lines 634-663)

On startup, every non-completed schedule is flipped to `inactive` — forces deliberate reactivation.

### Dedup bypass & atomic writes

Scheduled sends pass `_schedule` metadata → dedup gate skipped (see `mail/dedup`). Schedule files use tmp+replace (see `mail/atomic-write`).

---

## Source

All references: `lingtai-kernel/src/`

| What | File | Line(s) |
|---|---|---|
| `_schedules_dir` property | `lingtai/core/email/__init__.py` | 269-270 |
| `_schedule_create` | `lingtai/core/email/__init__.py` | 443-484 |
| `_schedule_cancel` | `lingtai/core/email/__init__.py` | 486-525 |
| `_schedule_reactivate` | `lingtai/core/email/__init__.py` | 527-553 |
| `_schedule_list` | `lingtai/core/email/__init__.py` | 555-593 |
| `_write_schedule` (atomic) | `lingtai/core/email/__init__.py` | 599-613 |
| `_reconcile_schedules_on_startup` | `lingtai/core/email/__init__.py` | 634-663 |
| `_scheduler_loop` | `lingtai/core/email/__init__.py` | 665-672 |
| `_scheduler_tick` (main logic) | `lingtai/core/email/__init__.py` | 674-781 |
| At-most-once: increment-before-send | `lingtai/core/email/__init__.py` | 720-722 |
| Schedule action dispatch | `lingtai/core/email/__init__.py` | 430-441 |

---

## Related

| Leaf | Relationship |
|---|---|
| `mail/dedup` | Scheduled sends bypass the dedup gate via the `_schedule` flag |
| `mail/atomic-write` | Schedule records use the same tmp+replace pattern |
| `mail/peer-send` | Each scheduled send ultimately routes through `_send()` → Mailman → transport |
