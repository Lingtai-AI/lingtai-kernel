---
related_files:
  - src/lingtai/__init__.py
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/tools/email/manager.py
  - src/lingtai/tools/email/primitives.py
  - src/lingtai/tools/email/schema.py
  - src/lingtai/tools/email/glossary-en.md
  - src/lingtai/tools/email/glossary-zh.md
  - src/lingtai/tools/email/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# intrinsics/email

Filesystem-based email system — mailbox I/O, composition, search, contacts, recurring schedules, and delivery. The agent's primary inter-process communication channel.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `__init__.py` — Package surface. Re-exports the full public API of the former monolithic `email.py` for backward compatibility: all primitives, schema functions, and `EmailManager`. Registers the `email` generic-dismiss guard at import (`__init__.py:27-32`) because `.notification/email.json` mirrors durable unread state. Contains the module-level `handle()` dispatcher (`__init__.py:80-93`) and idempotent `boot()` hook (`__init__.py:98-113`); `boot()` stops any prior manager's scheduler before wiring the fresh manager. External callers import `handle`, `boot`, `get_schema`, `get_description`, `EmailManager`, `_new_mailbox_id`, `mode_field` from this package.

- `primitives.py` — Mailbox I/O and display helpers. Module-level functions operating on the agent's `mailbox/` directory tree.
  - ID and path helpers: `_new_mailbox_id` (re-exported from the kernel mail service — `primitives.py:20` imports `from lingtai.kernel.services.mail import _new_mailbox_id`), `mode_field` (`primitives.py:29-35`), `_mailbox_dir` / `_inbox_dir` / `_outbox_dir` / `_sent_dir` (`primitives.py:38-51`).
  - Inbox I/O: `_load_message` (`primitives.py:56-61`), `_list_inbox` (`primitives.py:64-82`).
  - Read tracking: `_read_ids` (`primitives.py:89-98`), `_save_read_ids` (`primitives.py:101-106`), `_mark_read` (`primitives.py:109-113`).
  - Display: `_summary_to_list` (`primitives.py:118-123`), `_message_summary` (`primitives.py:126-145`).
  - Delivery: `_is_self_send` (`primitives.py:150-159`), `_persist_to_inbox` (`primitives.py:162-173`), `_persist_to_outbox` (`primitives.py:176-188`), `_move_to_sent` (`primitives.py:191-207`), `_mailman` (`primitives.py:210-267`) — daemon thread that waits, dispatches, and archives.
  - Filtering helpers: `_coerce_address_list` (`primitives.py:274-286`), `_preview` (`primitives.py:289-293`), `_email_time` (`primitives.py:296-298`).

- `schema.py` — Tool registration. `get_description` (`schema.py:10-11`) and `get_schema` (`schema.py:14-130`) build the JSON Schema for the email tool. Imports `mode_field` from `primitives`.

- `manager.py` — `EmailManager` class (`manager.py:46-876`). The core filesystem-based email manager. Key sections:
  - Lifecycle: `__init__` (`manager.py:48-55`), `start_scheduler` (`manager.py:57-66`), `stop_scheduler` (`manager.py:68-72`).
  - Filesystem helpers: `_load_email` (`manager.py:81-107`), `_list_emails` (`manager.py:109-132`), `_email_summary` (`manager.py:134-156`), `_inject_identity` (`manager.py:158-175`).
  - Action dispatch: `handle` (`manager.py:180-209`).
  - Schedules: `_handle_schedule` (`manager.py:214-224`), `_schedule_create` / `_cancel` / `_reactivate` / `_list` (`manager.py:226-363`), schedule helpers (`manager.py:368-442`), `_scheduler_loop` / `_scheduler_tick` (`manager.py:444-543`).
  - Send: `_send` (`manager.py:548-650`). Dispatches via `_mailman` daemon threads.
  - CRUD: `_check` (`manager.py:657-699`), `_read`, `_dismiss`, `_reply`, `_reply_all`, `_search`, `_archive`, `_delete`. `_dismiss` is the lightweight cousin of `_read` — same effect on read state and notification but returns no email bodies; intended for the "I already saw it in `_meta.notification_persistent.email`" path. All four read-state mutators (`_read`, `_dismiss`, `_archive`, `_delete`) call `EmailManager._rerender_unread_digest()` after the mutation so `.notification/email.json` mirrors the new state.
  - Reply routing: `_resolve_reply_target` picks ``(address, mode)`` for `_reply` / `_reply_all`. Preference order is (1) inbound `_return_route` (embedded by abs sends), (2) absolute-path `from`, (3) bare `from` in peer mode. An ambiguity guard refuses to send when a peer-mode bare `from` would resolve to the responder's own workdir while the original message's `identity.agent_id` differs from the responder's own agent id — the live failure mode from issue #145 where two `.lingtai/` networks both host an agent with the same short name (e.g. both have "mimo-1").
  - Notification refresh: `_rerender_unread_digest` (method on `EmailManager`) — lazy-imports the kernel-side helper from `base_agent/messaging.py` and runs it. Centralised here so all read-state mutators share one call site.
  - Contacts: `_contacts_path` / `_load_contacts` / `_save_contacts` / `_contacts` / `_add_contact` / `_remove_contact` / `_edit_contact` (`manager.py:801-876`).

## Connections

- **Inbound:** `handle()` is called by the tool dispatcher (via `base_agent._dispatch_tool`). `boot()` is called during agent construction in `base_agent/__init__.py`.
- **Inbound (cross-module):** `_new_mailbox_id` is now owned by the kernel mail service — defined at `src/lingtai/kernel/services/mail.py:29` and used there by `send()` (`src/lingtai/kernel/services/mail.py:181`). The email package imports and re-exports it via `primitives.py:20` for back-compat with `lingtai.tools.email._new_mailbox_id` importers.
- **Inbound (cross-module):** `EmailManager` is imported by `src/lingtai/__init__.py:19` for the wrapper re-export.
- **Outbound:** Depends on `..i18n` (translations), `..message` (message construction), `..time_veil` (timestamp scrubbing), `..token_counter` (budget checks in `_check`).
- **Outbound (unread-email producer):** Mail arrival writes `.notification/email.json` via `publish_notification` (or deletes it via `clear_notification` when count hits 0). `base_agent/messaging.py:_on_normal_mail` calls `_rerender_unread_digest(agent)` (resolved via `_intrinsic_hook("email", "_rerender_unread_digest")` at `src/lingtai/kernel/base_agent/messaging.py:61`) which uses `primitives.py:_render_unread_digest` for count/newest compatibility and `_unread_notification_context` for full-body entries, then `system.publish_notification(workdir, "email", header=…, icon="📧", data={count, newest_received_at, email_ids, emails})`. The kernel's `_sync_notifications` poll picks up the fingerprint change on the next heartbeat tick and updates the wire's `notification(action="check")` block. See root `ANATOMY.md` "Notifications" for the full architecture.
- **Outbound (bounce notification):** `primitives.py:_mailman` calls `agent._enqueue_system_notification(source="email.bounce", ref_id=msg_id, body=...)` (`primitives.py:280`). The system events producer in `base_agent/messaging.py` merges the bounce into the events list inside `.notification/system.json` (capped at 20 newest) under a per-agent `threading.Lock`. Bounces share `system.json` with daemon notices, MCP-bridged events, and any future kernel events — they are NOT aggregated into the unread email notification at `email.json`.
- **Data flow:** All state lives in the filesystem under `mailbox/` and `.notification/`. The `EmailManager` is stateless except for `_last_sent` (duplicate-send guard) and `_scheduler_thread` (background timer).

## Key invariants

- `_send(mode="abs")` embeds an explicit `_return_route` dict (`{"mode": "abs", "address": <sender abs workdir>, "sender_agent_id": <sender id>}`) into every dispatched payload AND the local `sent/{id}/message.json` record. This is the only safe return route across `.lingtai/` networks where short addresses can collide (issue #145). Recipients without the field — older messages — keep working through the existing absolute-`from` fallback in `_resolve_reply_target`.
- `_mailman` runs as a daemon thread per recipient. It waits until `deliver_at`, then dispatches. The outbox entry is written synchronously before the thread starts.
- `_mailman` with `skip_sent=True` (used by `_send`) deletes the outbox entry instead of moving it to `sent/`, because `_send` writes the `sent/` entry itself.
- Schedule status lifecycle: `active` → `inactive` (cancel) or `completed` (all sent). On startup, `_reconcile_schedules_on_startup` flips `active` → `inactive` so schedules don't fire until explicitly reactivated.
- `.notification/email.json` is a **live mirror** of the current unread set. Any action that mutates the read state — `_read`, `_dismiss`, `_archive`, `_delete` — calls `_rerender_unread_digest(agent)` (lazy import from `base_agent/messaging.py`) so the wire's notification updates on the next heartbeat sync. The earlier "snapshot at last arrival" semantics led to the unread email notification carrying mails the agent had already replied to indefinitely.
- `_dismiss` is the lightweight "mark read without returning content" path — used when the agent already saw the body in `_meta.notification_persistent.email` and just wants to clear the notification entry. Same effect on `read.json` and `.notification/email.json` as `_read`, but no email bodies in the response. Accepts a list (`email_id=[id1, id2, ...]`).
- The unread-mail notification envelope carries an ``instructions`` field (set by `_rerender_unread_digest`) telling the agent to call `email(action="read", ...)` or `email(action="dismiss", ...)` after handling a mail; until the agent does, the notification keeps reminding them. This is the producer-side directive — generic frontend code does not have to know about email's dismissal contract.
- Each persistent email entry exposes the mailbox ID directly (under "ID:" in en, "ID：" in zh, "编号：" in wen). The agent passes that ID verbatim to `email_id` when calling `read` or `dismiss`. Without this, the agent has to call `email(action="check")` first just to discover the IDs, defeating the point of the inline notification.
- **Contact** writes are atomic — temp-file + `os.replace` (`manager.py:830-834`) — to prevent corruption on crash. Note this is **not** uniform across all email persistence: message/inbox/outbox bodies are written with direct `Path.write_text(json.dumps(...))` (`primitives.py:202`, `:218`, `:239`), so a crash mid-write can leave a partial `message.json`. Unifying these on the shared atomic helper is tracked under the kernel persistence-helper work (issue #510).

## Notification format

When mail arrives, `base_agent/messaging.py:_on_normal_mail` calls
`_rerender_unread_digest(agent)` which renders the current
unread mail mirror using `_render_unread_digest` for count/newest and `_unread_notification_context` for full-body entries, then submits
the result via `system.publish_notification` to `.notification/email.json`.
The same path runs after `_read` / `_dismiss` / `_archive` / `_delete`
mutate the read state (each of those calls `EmailManager._rerender_unread_digest()`):

```json
{
  "header":       "3 unread emails",
  "icon":         "📧",
  "priority":     "normal",
  "published_at": "2026-05-05T03:42:11Z",
  "instructions": "Unread email bodies are injected in full into _meta.notification_persistent.email. Prefer email.dismiss after handling; use email.read/reply for source-of-truth actions ...",
  "data": {
    "count":               3,
    "newest_received_at":  "2026-05-05T03:42:09Z",
    "email_ids":           ["mailbox-id-1"],
    "emails":              [{"id": "mailbox-id-1", "from": "human", "subject": "...", "message": "full body text", "message_chars": 14, "message_truncated": false}]
  }
}
```

The ``instructions`` field is the producer-side directive that
replaces the static-prompt approach: it travels with the payload, so
each producer owns its own dismissal contract without the kernel
having to know about it.

The agent sees this through the kernel-injected `notification(action="check")` wire pair (see root `ANATOMY.md` "Notifications"). The raw `.notification/email.json` mirror carries count, email IDs, and structured full-body email entries, but the model-visible `_meta.notifications.email` lane is sanitized to a high-attention hook carrying only `data.email_ids`; unread context moves to `_meta.notification_persistent.email`. There is no per-mail "notification pair" anymore — the file is the producer mirror and persistent meta is the context lane.

Persistent email entries carry full message bodies:

```json
{
  "id": "mailbox-id-1",
  "from": "human",
  "subject": "...",
  "message": "full body text",
  "message_chars": 123,
  "message_truncated": false
}
```

- **Cap:** new sends whose body exceeds 50,000 characters are rejected at send time. Ordinary notification rendering should not truncate unread email bodies; legacy over-limit bodies may carry `message_truncated=true` defensively.
- **`recency`:** veiled timestamp of newest unread (uses `time_veil.veil()`).
- **Lifecycle:** `.notification/email.json` is rewritten on every arrival; deleted via `clear_notification` when count hits 0 (the kernel sync then strips the wire's notification block on the next tick). Reads/dismisses/archives/deletes also trigger rerender through `EmailManager._rerender_unread_digest()`, so the mirror and persistent lane reflect current unread state.
