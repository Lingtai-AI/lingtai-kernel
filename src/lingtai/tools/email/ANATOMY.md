---
related_files:
  - src/lingtai/__init__.py
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/email/BEHAVIORS.md
  - src/lingtai/tools/email/CONTRACT.md
  - src/lingtai/tools/email/__init__.py
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/registry.py
  - src/lingtai/kernel/base_agent/__init__.py
  - src/lingtai/tools/email/manager.py
  - src/lingtai/tools/email/primitives.py
  - src/lingtai/tools/email/settings.py
  - src/lingtai/adapters/posix/mail.py
  - src/lingtai/tools/email/schema.py
  - src/lingtai/tools/email/_family_schema.py
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - tests/test_email_official_tool_plugin.py
  - tests/test_email_settings.py
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/email/glossary-en.md
  - src/lingtai/tools/email/glossary-zh.md
  - src/lingtai/tools/email/glossary-wen.md
  - src/lingtai/tools/email/manual/SKILL.md
  - src/lingtai/tools/email/manual/reference/actions-and-storage/SKILL.md
  - src/lingtai/tools/email/manual/reference/addressing-and-replies/SKILL.md
  - src/lingtai/tools/email/manual/reference/notifications-and-delivery/SKILL.md
  - src/lingtai/tools/email/manual/reference/settings-reference/SKILL.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# intrinsics/email

Filesystem-based email system — mailbox I/O, composition, search, contacts, and delivery. The agent's primary inter-process communication channel. Recurring/scheduled sends were removed in favor of cron; the tool is request/response only.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

- `__init__.py` — Package surface and the LTP v2 family boundary. Re-exports the full public API of the former monolithic `email.py` for backward compatibility: all primitives, schema functions, and `EmailManager`. Registers the `email` generic-dismiss guard at import because `.notification/email.json` mirrors durable unread state. Owns the family composition and the module-level intrinsic protocol:
  - `_schema_only_family()` / `_FAMILY` — the module-level schema-only `ToolFamily`. Email is an *intrinsic* (module-level `get_schema()`/`handle(agent, args)`, no per-Agent manager object to hang a family off at import), so this one never dispatches; constructing it at import is still load-bearing as the registry's duplicate/reserved-child collision check and supplies the Email settings provider for schema composition.
  - `get_schema()` — the composed model-facing schema. Shadows the legacy flat `schema.get_schema` (re-exported as `get_flat_schema`) and replaces the generic composer's neutral `action` description with Email's own `ACTION_ENUM_DESCRIPTION`.
  - `_build_family(agent)` — the per-call dispatching family. Every operational child re-enters `EmailManager.handle` with its unchanged historical flat shape; the generic seam injects `settings` from the Email provider, and `manual` is `build_manual_child(agent, "email")`, registered directly and unwrapped.
  - `_strip_nulls()` — turns provider-sent explicit `null`s back into absent keys so the manager's `args.get(...)`/`in args` defaulting is preserved.
  - `_adapt_manual_result()` — Host/presentation-only flattening of the canonical ManualTool result to Email's pinned `{status, manual, manual_path}` public shape, strictly *after* dispatch.
  - `handle(agent, args)` — strips `_tc_id`, renders the reserved `unread` rejection before dispatch, delegates to the family, then adapts `manual` and restores Email's own unknown/absent-action results.
  - `boot(agent)` — idempotent official boot hook. For a real BaseAgent it
    creates/replaces the real `EmailManager` first, then registers `DECLARATION`
    through `register_agent_tool_plugins(..., extra_ports_for=...)`. The narrow
    `DaemonEmailAgentShim` lacks an official tool surface and intentionally gets
    manager/hook boot only, preserving its task-scoped daemon-email MCP route.
  - `EmailRuntimeRequest` / `EmailRuntimePort` — Email-owned manager-facing
    request, typed operation, and applied pseudo-subscription snapshot read.
    `_build_bound_family(host)` consumes exactly `host.email_runtime`;
    `_strip_nulls()` runs before request construction.
  - `DECLARATION` / `_bind(host)` — static official-plugin declaration and
    host-bound composition. It requires exactly `workdir`/`email_runtime`;
    operational children and settings provider consume only `EmailRuntimePort`,
    while the reserved manual child uses only `host.workdir`. The official mount supplies the one
    model-facing schema while the module remains available to kernel hook
    resolution.
  - `AgentEmailRuntimeAdapter` lives in the production host adapter module, not
    this family. It retains only manager/settings readers, rejects foreign Email actions,
    looks up the current `agent._email_manager` at call time, and calls its
    `handle({"action": request.action, **dict(request.input)})` exactly once.
    It never captures `_intrinsics` or routes through the official handler.
    The settings read returns the POSIX adapter's actual construction snapshot.

- `_family_schema.py` — Canonical operational/manual action data for the composed schema: `ACTION_ORDER`, one strict closed `input_schema` per action in `INPUT_SCHEMAS`, and `ACTION_ENUM_DESCRIPTION`. The generic declaration seam inserts `settings` before `manual`, changing the disclosed input union to `anyOf` without hand-authoring a settings child. This module holds no composition logic and imports `MANUAL_INPUT_SCHEMA` from `tool_family.manual` rather than restating it.

- `settings.py` — Email-owned read-only settings provider and single source for the fixed body, duplicate-loop, check-result, and unread-projection limits consumed by `manager.py`/`primitives.py`. `email_settings_rows()` adds the fully redacted `manifest.pseudo_agent_subscriptions` row from the effective mail-adapter construction snapshot. It raises when that snapshot is unavailable and defines no mutation type or handler.

- `manual/SKILL.md` — the installed `email-manual` bundle the reserved `manual` child returns; `Agent._install_intrinsic_manuals()` copies it to `.library/intrinsic/capabilities/email/`.

- `primitives.py` — Mailbox I/O and display helpers. Module-level functions operating on the agent's `mailbox/` directory tree.
  - ID and path helpers: `_new_mailbox_id` (re-exported from the kernel mail service — `primitives.py:20` imports `from lingtai.kernel.services.mail import _new_mailbox_id`), `_mailbox_dir` / `_inbox_dir` / `_outbox_dir` / `_sent_dir` (`primitives.py:25-38`).
  - Inbox I/O: `_load_message` (`primitives.py:45-53`), `_list_inbox` (`primitives.py:56-75`).
  - Read tracking: `_read_ids` (`primitives.py:86-95`), `_save_read_ids` (`primitives.py:98-104`), `_mark_read` (`primitives.py:107-111`).
  - Display: `_summary_to_list` (`primitives.py:118-124`), `_message_summary` (`primitives.py:127-162`).
  - Delivery: `_is_self_send` (`primitives.py:169-171`), `_persist_to_inbox` (`primitives.py:174-186`), `_persist_to_outbox` (`primitives.py:189-202`), `_move_to_sent` (`primitives.py:205-221`), `_mailman` (`primitives.py:224-276`) — daemon thread that waits, dispatches, and archives.
  - Filtering helpers: `_coerce_address_list` (`primitives.py:283-296`), `_preview` (`primitives.py:299-304`), `_email_time` (`primitives.py:307-309`).

- `schema.py` — `get_description` (the registered intrinsic description, unchanged) and the legacy flat `get_schema`. Since the ToolFamily migration that flat schema is **no longer the model-facing schema**: it describes the *internal* `EmailManager.handle` argument shape and is re-exported from `__init__.py` as `get_flat_schema`.

- `manager.py` — `EmailManager` class (`manager.py:51-864`). The core filesystem-based email manager. Since the ToolFamily migration its flat `handle(args)` argument shape is an **internal** interface reached through the family's per-action children, not the model-facing surface; the behavioral engine below is unchanged by that migration; model-facing recovery guidance strings use the ToolFamily envelope. Key sections:
  - Lifecycle: `__init__` (`manager.py:54-58`). Recurring/scheduled sends were removed in favor of cron, so there is no scheduler thread or schedule action.
  - Filesystem helpers: `_load_email` (`manager.py:68-93`), `_list_emails` (`manager.py:95-118`), `_email_summary` (`manager.py:133-166`), `_inject_identity` (`manager.py:168-185`).
  - Action dispatch: `handle` (`manager.py:191-222`).
  - Send: `_send` (`manager.py:228-357`). Dispatches via `_mailman` daemon threads.
  - CRUD: `_check` (`manager.py:363-411`), `_read` (`manager.py:413-477`), `_dismiss` (`manager.py:479-538`), `_reply` (`manager.py:603-628`), `_reply_all` (`manager.py:630-675`), `_search` (`manager.py:677-702`), `_archive` (`manager.py:704-741`), `_delete` (`manager.py:743-782`). `_dismiss` is the lightweight cousin of `_read` — same effect on read state and notification but returns no email bodies; intended for the "I already saw it in `_meta.agent_meta.notifications.persistent.email`" path. All four read-state mutators (`_read`, `_dismiss`, `_archive`, `_delete`) call `EmailManager._rerender_unread_digest()` after the mutation so `.notification/email.json` mirrors the new state.
  - Reply routing: `_resolve_reply_target` (`manager.py:558-601`) returns the absolute target address for `_reply` / `_reply_all`. Preference order is (1) inbound `_return_route.address` (written by every send), (2) an absolute-path `from`; otherwise it returns an error. There is no bare-`from` peer fallback and no ambiguity guard: a bare name is never a reply target, so the issue #145 same-name collision cannot reach `send`.
  - Notification refresh: `_rerender_unread_digest` (method on `EmailManager`) — lazy-imports the kernel-side helper from `base_agent/messaging.py` and runs it. Centralised here so all read-state mutators share one call site.
  - Contacts: `_contacts_path` / `_load_contacts` / `_save_contacts` / `_contacts` / `_add_contact` / `_remove_contact` / `_edit_contact` (`manager.py:789-864`).

## Connections

- **Inbound:** the official handler is called by the tool dispatcher; the
  retained intrinsic shim supplies kernel inbound hooks and looks up that handler
  at call time. `handle()` strips `_tc_id` at this family's legacy/hook boundary.
  `BaseAgent._boot_official_intrinsics()` calls `boot()` during construction and
  refresh after it has wired the official intrinsic shim.
- **Inbound (kernel convenience API):** `base_agent/messaging.py:_mail` prefers the mounted official handler and falls back to the retained intrinsic shim; it carries the LTP v2 envelope (`{"action": "send", "input": {...}}`).
- **Outbound (family composition):** `__init__.py` imports `ChildTool`/`ToolFamily`
  from `../tool_family/` and `build_manual_child`/`MANUAL_INPUT_SCHEMA` from
  `../tool_family/manual.py`. Action handlers depend on the local
  `EmailRuntimePort`; the host's `AgentEmailRuntimeAdapter` supplies that narrow
  port through `extra_ports_for`, not generic host dispatch. See
  `src/lingtai/tools/tool_family/ANATOMY.md`.
- **Inbound (cross-module):** `_new_mailbox_id` is owned by the kernel mail service — defined at `src/lingtai/kernel/services/mail.py:29-44`. After the mail Ports & Adapters split it is imported explicitly by the POSIX mail adapter (`src/lingtai/adapters/posix/mail.py:29`) and consumed by its `send()` (`src/lingtai/adapters/posix/mail.py:118`), not by `kernel/services/mail.py` (which no longer defines a transport). The email package imports and re-exports it via `primitives.py:20` for back-compat with `lingtai.tools.email._new_mailbox_id` importers.
- **Inbound (cross-module):** `EmailManager` is imported by `src/lingtai/__init__.py:19` for the wrapper re-export.
- **Outbound:** Depends on `..i18n` (translations), `..message` (message construction), `..time_veil` (timestamp scrubbing), `..token_counter` (budget checks in `_check`).
- **Outbound (unread-email producer):** Mail arrival writes `.notification/email.json` via `publish_notification` (or deletes it via `clear_notification` when count hits 0). `base_agent/messaging.py:_on_normal_mail` calls `_rerender_unread_digest(agent)` (resolved via `_intrinsic_hook("email", "_rerender_unread_digest")` at `src/lingtai/kernel/base_agent/messaging.py:61`) which uses `primitives.py:_render_unread_digest` for count/newest compatibility and `_unread_notification_context` for full-body entries, then `system.publish_notification(workdir, "email", header=…, icon="📧", data={count, newest_received_at, email_ids, emails})`. The kernel's `_sync_notifications` poll picks up the fingerprint change on the next heartbeat tick and updates the wire's `notification(action="check")` block. See root `ANATOMY.md` "Notifications" for the full architecture.
- **Outbound (bounce notification):** `primitives.py:_mailman` calls `agent._enqueue_system_notification(source="email.bounce", ref_id=msg_id, body=...)` (`primitives.py:272`). The system events producer in `base_agent/messaging.py` merges the bounce into the events list inside `.notification/system.json` (capped at 20 newest) under a per-agent `threading.Lock`. Bounces share `system.json` with daemon notices, MCP-bridged events, and any future kernel events — they are NOT aggregated into the unread email notification at `email.json`.
- **Data flow:** Durable state lives in the filesystem under `mailbox/` and `.notification/`. The live `EmailManager` retains only `_agent`, `_last_sent` (duplicate-send guard), and `_dup_free_passes`; it has no scheduler thread.

## Key invariants

- `_send` embeds an explicit `_return_route` dict (`{"address": <sender abs workdir>, "sender_agent_id": <sender id>}`, no `mode` key) into every dispatched payload AND the local `sent/{id}/message.json` record. Replies use that absolute address, the safe return route across `.lingtai/` networks where short addresses can collide (issue #145). Messages without the field — older messages — fall back to an absolute `from`; otherwise `_resolve_reply_target` refuses. The TUI's human-mail writer in the `lingtai` TUI repo stamps the same two keys, so human mail is answerable.
- `_mailman` runs as a daemon thread per recipient. It waits until `deliver_at`, then dispatches. The outbox entry is written synchronously before the thread starts.
- `_mailman` with `skip_sent=True` (used by `_send`) deletes the outbox entry instead of moving it to `sent/`, because `_send` writes the `sent/` entry itself.
- The model-facing root is one closed LTP v2 envelope (`action`, `input`, `reasoning`, `summarize`) over 15 internal children: thirteen unchanged operational actions, generic `settings` immediately before `manual`, and `manual`. Children consume no model tool slots, so `email` still advertises exactly one tool. The declaration opt-in and family provider drive advertised schema and dispatch. Cross-action `input` keys are rejected before any mailbox I/O.
- `settings` is read-only and performs no mailbox I/O. Four public rows are installed-code limits; `manifest.pseudo_agent_subscriptions` is configurable through the existing init manifest but fully redacts current/default path lists. Every success row projects exactly `key`, `current`, `default`, `configurable`, and an exact `email-manual` section pointer in `comment`. Unavailable applied truth fails the whole action without partial rows. Mailbox/session paths, addresses, identities, contacts, content, attachments, and read state never enter this inventory.
- The official manager-facing composition is domain-native: `EmailRuntimeRequest`
  is the only request shape accepted by `EmailRuntimePort`; `_strip_nulls()` runs
  before it is constructed. The host adapter rejects foreign actions before one
  flattened manager call, reads a replacement manager at call time, and never
  captures `_intrinsics` or recurses through an official handler. The official
  surface is not a dynamic capability and must not create an Email row in
  `_capabilities` or the persisted manifest.
- `unread` is kernel-synthesized digest state, **not** a public child: it is absent from `ACTION_ORDER` and the `action` enum, and `handle()` renders its exact reserved-action rejection before the family dispatches.
- `.notification/email.json` is a **live mirror** of the current unread set. Any action that mutates the read state — `_read`, `_dismiss`, `_archive`, `_delete` — calls `_rerender_unread_digest(agent)` (lazy import from `base_agent/messaging.py`) so the wire's notification updates on the next heartbeat sync. The earlier "snapshot at last arrival" semantics led to the unread email notification carrying mails the agent had already replied to indefinitely.
- `_dismiss` is the lightweight "mark read without returning content" path — used when the agent already saw the body in `_meta.agent_meta.notifications.persistent.email` and just wants to clear the notification entry. Same effect on `read.json` and `.notification/email.json` as `_read`, but no email bodies in the response. Accepts a list (`email_id=[id1, id2, ...]`).
- The unread-mail notification envelope carries an ``instructions`` field (set by `_rerender_unread_digest`) telling the agent to call `email(action="read", input={...}, reasoning=...)` or `email(action="dismiss", input={...}, reasoning=...)` after handling a mail; until the agent does, the unread mirror retains it, but automatic delivery does not repeat unchanged mail. This is the producer-side directive — generic frontend code does not have to know about email's dismissal contract.
- Each persistent email entry exposes the mailbox ID directly (under "ID:" in en, "ID：" in zh, "编号：" in wen). The agent passes that ID verbatim under `input.email_id` when calling `read` or `dismiss`. Without this, the agent has to call `email(action="check")` first just to discover the IDs, defeating the point of the inline notification.
- **Contact** writes are atomic — temp-file + `os.replace` (`manager.py:800-815`) — to prevent corruption on crash. Note this is **not** uniform across all email persistence: message/inbox/outbox bodies are written with direct `Path.write_text(json.dumps(...))` (`primitives.py:182`, `:198`, `:220`), so a crash mid-write can leave a partial `message.json`. Unifying these on the shared atomic helper is tracked under the kernel persistence-helper work (issue #510).

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
  "instructions": "Unread email bodies are injected in full into _meta.agent_meta.notifications.persistent.email. Prefer email.dismiss after handling; use email.read/reply for source-of-truth actions ...",
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

The agent sees this through the kernel-injected `notification(action="check")` wire pair (see root `ANATOMY.md` "Notifications"). The raw `.notification/email.json` mirror carries count, email IDs, and structured full-body email entries, but the model-visible `_meta.agent_meta.notifications.attention.email` lane is sanitized to a high-attention hook carrying only `data.email_ids`; unread context moves to `_meta.agent_meta.notifications.persistent.email`. There is no per-mail "notification pair" anymore — the file is the producer mirror and persistent meta is the context lane.

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
