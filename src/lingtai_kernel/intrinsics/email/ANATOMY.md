# intrinsics/email

Filesystem-based email system — mailbox I/O, composition, search, contacts, recurring schedules, and delivery. The agent's primary inter-process communication channel.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `__init__.py` — Package surface. Re-exports the full public API of the former monolithic `email.py` for backward compatibility: all primitives, schema functions, and `EmailManager`. Contains the module-level `handle()` dispatcher (`__init__.py:79-87`) and `boot()` hook (`__init__.py:90-103`). External callers import `handle`, `boot`, `get_schema`, `get_description`, `EmailManager`, `_new_mailbox_id`, `mode_field` from this package.

- `primitives.py` — Mailbox I/O and display helpers. Module-level functions operating on the agent's `mailbox/` directory tree.
  - ID and path helpers: `_new_mailbox_id` (`primitives.py:22-26`), `mode_field` (`primitives.py:29-34`), `_mailbox_dir` / `_inbox_dir` / `_outbox_dir` / `_sent_dir` (`primitives.py:37-50`).
  - Inbox I/O: `_load_message` (`primitives.py:56-61`), `_list_inbox` (`primitives.py:64-82`).
  - Read tracking: `_read_ids` (`primitives.py:89-98`), `_save_read_ids` (`primitives.py:101-106`), `_mark_read` (`primitives.py:109-113`).
  - Display: `_summary_to_list` (`primitives.py:118-123`), `_message_summary` (`primitives.py:126-145`).
  - Delivery: `_is_self_send` (`primitives.py:150-159`), `_persist_to_inbox` (`primitives.py:162-173`), `_persist_to_outbox` (`primitives.py:176-188`), `_move_to_sent` (`primitives.py:191-207`), `_mailman` (`primitives.py:210-267`) — daemon thread that waits, dispatches, and archives.
  - Filtering helpers: `_coerce_address_list` (`primitives.py:274-286`), `_preview` (`primitives.py:289-293`), `_email_time` (`primitives.py:296-298`).

- `schema.py` — Tool registration. `get_description` (`schema.py:10-11`) and `get_schema` (`schema.py:14-147`) build the JSON Schema for the email tool. Imports `mode_field` from `primitives`.

- `manager.py` — `EmailManager` class (`manager.py:46-1082`). The core filesystem-based email manager. Key sections:
  - Lifecycle: `__init__` (`manager.py:48-55`), `start_scheduler` (`manager.py:57-66`), `stop_scheduler` (`manager.py:68-72`).
  - Filesystem helpers: `_load_email` (`manager.py:81-107`), `_list_emails` (`manager.py:109-132`), `_email_summary` (`manager.py:134-156`), `_inject_identity` (`manager.py:158-175`).
  - Action dispatch: `handle` (`manager.py:180-209`).
  - Schedules: `_handle_schedule` (`manager.py:214-224`), `_schedule_create` / `_cancel` / `_reactivate` / `_list` (`manager.py:226-363`), schedule helpers (`manager.py:368-442`), `_scheduler_loop` / `_scheduler_tick` (`manager.py:444-543`).
  - Send: `_send` (`manager.py:548-650`). Dispatches via `_mailman` daemon threads.
  - CRUD: `_check` (`manager.py:657-699`), `_read` (`manager.py:701-779`), `_reply` (`manager.py:785-807`), `_reply_all` (`manager.py:809-851`), `_search` (`manager.py:853-878`), `_archive` (`manager.py:880-910`), `_delete` (`manager.py:912-942`).
  - Contacts: `_contacts_path` / `_load_contacts` / `_save_contacts` / `_contacts` / `_add_contact` / `_remove_contact` / `_edit_contact` (`manager.py:947-1082`).

## Connections

- **Inbound:** `handle()` is called by the tool dispatcher (via `base_agent._dispatch_tool`). `boot()` is called during agent construction in `base_agent/__init__.py`.
- **Inbound (cross-module):** `_new_mailbox_id` is imported by `base_agent/messaging.py:28` and `services/mail.py:165` for ID generation.
- **Inbound (cross-module):** `EmailManager` is imported by `lingtai/__init__.py:19` for the wrapper re-export.
- **Outbound:** Depends on `..i18n` (translations), `..message` (message construction), `..time_veil` (timestamp scrubbing), `..token_counter` (budget checks in `_check`).
- **Outbound (unread-digest producer):** Mail arrival writes `.notification/email.json` via `publish_notification` (or deletes it via `clear_notification` when count hits 0). `base_agent/messaging.py:_on_normal_mail` calls `_rerender_unread_digest(agent)` (`base_agent/messaging.py:52`) which uses `primitives.py:_render_unread_digest` to build the digest prose, then `system.publish_notification(workdir, "email", header=…, icon="📧", data={count, newest_received_at, digest})`. The kernel's `_sync_notifications` poll picks up the fingerprint change on the next heartbeat tick and updates the wire's `system(action="notification")` block. See root `ANATOMY.md` "Notifications" for the full architecture.
- **Outbound (bounce notification):** `primitives.py:_mailman` calls `agent._enqueue_system_notification(source="email.bounce", ref_id=msg_id, body=...)` (`primitives.py:280`). The system events producer in `base_agent/messaging.py` merges the bounce into the events list inside `.notification/system.json` (capped at 20 newest) under a per-agent `threading.Lock`. Bounces share `system.json` with daemon notices, MCP-bridged events, and any future kernel events — they are NOT aggregated into the unread digest at `email.json`.
- **Data flow:** All state lives in the filesystem under `mailbox/` and `.notification/`. The `EmailManager` is stateless except for `_last_sent` (duplicate-send guard) and `_scheduler_thread` (background timer).

## Key invariants

- `_mailman` runs as a daemon thread per recipient. It waits until `deliver_at`, then dispatches. The outbox entry is written synchronously before the thread starts.
- `_mailman` with `skip_sent=True` (used by `_send`) deletes the outbox entry instead of moving it to `sent/`, because `_send` writes the `sent/` entry itself.
- Schedule status lifecycle: `active` → `inactive` (cancel) or `completed` (all sent). On startup, `_reconcile_schedules_on_startup` flips `active` → `inactive` so schedules don't fire until explicitly reactivated.
- `_read()` does NOT auto-dismiss notifications. Email arrivals write a single `.notification/email.json` snapshot of the current unread set; reads/archives/deletes do NOT trigger a rerender (the wire is "what was unread at the latest arrival," not "live mirror of unread"). Stale-after-read is acceptable; the agent can call `email(action="check")` for a fresh view, or wait for the next arrival to refresh `email.json`.
- Contact writes use atomic temp-file + `os.replace` to prevent corruption on crash.

## Notification format

When mail arrives, `base_agent/messaging.py:_on_normal_mail` calls
`_rerender_unread_digest(agent)` which builds a digest of all currently
unread mail using `primitives.py:_render_unread_digest`, then submits
the result via `system.publish_notification` to `.notification/email.json`:

```json
{
  "header":       "3 unread emails",
  "icon":         "📧",
  "priority":     "normal",
  "published_at": "2026-05-05T03:42:11Z",
  "data": {
    "count":               3,
    "newest_received_at":  "2026-05-05T03:42:09Z",
    "digest":              "[email] 3 unread message(s) — most recent ..."
  }
}
```

The agent reads this through the kernel-injected `system(action="notification")`
wire pair (see root `ANATOMY.md` "Notifications"); the JSON dict appears
under the `email` key in the `notifications` map. There is no per-mail
"notification pair" anymore — the file IS the notification.

Digest prose format (en) is what lands in `data.digest`:
```
[email] {count} unread message(s) — most recent {recency}.

  1. From {name} ({address}) — {subject}
     Sent at: {sent_at}
     {preview}

  2. ...
(showing first {N_shown} of {N_total})    ← only if N_total > N_shown
```

- **Cap:** max 10 entries (newest-first), 200 chars preview each.
- **`recency`:** veiled timestamp of newest unread (uses `time_veil.veil()`).
- **Lifecycle:** `.notification/email.json` is rewritten on every arrival; deleted via `clear_notification` when count hits 0 (the kernel sync then strips the wire's notification block on the next tick). Reads/archives/deletes do NOT trigger a rerender.
