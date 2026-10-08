---
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/feishu/SKILL.md
- src/lingtai/mcp_servers/feishu/server.py
- src/lingtai/mcp_servers/feishu/manager.py
- src/lingtai/mcp_servers/feishu/service.py
- src/lingtai/mcp_servers/feishu/settings.py
- tests/test_feishu_settings.py
maintenance: |
  Keep this symptom-led runbook aligned with Feishu startup, admission,
  message, Task Card, and settings diagnostics. Start from redacted state and
  route exact message semantics to message-semantics.md rather than duplicating
  them here.
---
# Feishu diagnostics

Use this directly for a symptom; [`setup.md`](setup.md) owns deployment details. Narrow
the boundary before inspecting any raw envelope; never paste raw content,
credentials, IDs, keys, or local paths into evidence.

## Safe order

1. Read `lingtai://status`.
2. Check lifecycle logs for MCP/account start, listener readiness, and WebSocket
   connect/reconnect (not event bodies).
3. Call `feishu` `accounts` for aliases and non-secret identity.
4. Use `check`, then `read` only on the affected chat.
5. Inspect one attachment or raw envelope only if normalized data cannot answer
   the question; reduce it to a synthetic field-shape fixture.

`status="ok"`/`manager_initialized=true` proves construction, not connectivity
or event delivery. `service_started` proves lifecycle start, not WebSocket
health. `config_path` is private machine metadata. `config_readable`, account
count, app-ID presence, secret presence, and `allowed_users_count` are safe
status facts; a positive allowlist count is required for a canary. Missing/null/
empty allowlists are unrestricted compatibility behavior.

## Symptom → next action

### Startup/configuration

- **Environment not set:** set `LINGTAI_FEISHU_CONFIG` on the MCP entry and
  refresh. Resolve relative paths against `LINGTAI_AGENT_DIR`, not shell cwd.
- **Config missing/unreadable:** use the path from safe status; check file and
  parent traversal for the Agent owner. Do not move secrets into the repository
  or loosen permissions.
- **`config must contain 'accounts' (list)` or construction fails:** require a
  truthy `accounts` list and required `alias`, `app_id`, `app_secret` keys. The
  loader does not enforce string formats, unique aliases, `cli_` prefixes, or
  allowlist element shapes; duplicate aliases replace lookup entries while
  preserving service order.
- **Manager starts but identity is unverified:** verify Developer Console
  credentials, published app/tenant availability, and OpenAPI reachability. A
  cached Bot identity can preserve mention matching during a temporary lookup
  failure; it is not credential proof.

### WebSocket/refresh

- **No connected listener:** check credentials, long-connection event mode,
  outbound network, and stale competing MCP instances; correct once and refresh.
- **Repeated reconnect/exit:** correlate timestamps and exception classes, then
  check provider status, network stability, credential rotation, and pinned
  `lark-channel-sdk>=1.2,<2`. Do not blind-refresh until the first error is lost.
- **Old Task Card seems uncertain after refresh:** expected. Ordering high-water
  is process-local; the exact persisted resident is edited in place until a
  newer route message is observed. Never delete state or guess a duplicate.

### Admission and wake

- **DM silent:** check `im.message.receive_v1`, app availability, sender
  `open_id` in non-empty `allowed_users` (when used), then `check`/`read` for an
  already admitted record. DMs do not require mention.
- **Group/topic silent:** require allowlist admission and an explicit real Bot
  mention. `@all` or text containing the Bot name is not enough. Then check Bot
  membership and message-event permission/subscription.
- **Stored but no wake:** reaction/read/Bot membership records belong to the
  reserved `events` chat and never wake. Authorized business `card_action`
  records wake; local control callbacks do neither.
- **Label and media are separate turns:** Feishu can send a text label and an
  audio/media message independently. Preserve both IDs; inspect the media
  record's `content.kind` and `attachments` rather than merging by assumption.

### Rich inbound/media

- **Only a placeholder is visible:** `read` the message. `content.kind` should
  identify image/file/audio/video/sticker/post/task/todo; downloaded attachments
  have `status="downloaded"`, safe path, filename, and size; failures retain
  descriptor and bounded error. Use the appropriate local capability, not the
  placeholder. Raw envelopes and provider keys stay behind `read`.
- **Download fails:** check the published `im:resource`/effective resource scope
  and message ownership. The adapter downloads via Feishu's resource API, never
  an arbitrary URL, and retains the failed descriptor.
- **Audio has no transcript:** check attachment status and `faster-whisper` in
  the exact launching runtime. Failed transcription leaves downloaded audio
  and its bounded error available; do not upload it to debugging services.
- **Rich post/task is flattened:** compare `message_type`, normalized
  `content.kind`, and raw `feishu` in `read`; top-level `text` is only a preview.

### Outbound/replies

- **Rejected before I/O:** provide exactly one top-level `text` or tagged
  `content`; use the closed union. Unknown fields, mixed variants, unsupported
  captions, relative paths, and URL media are local `INVALID_ARGUMENT` errors.
- **Topic reply leaves topic or target is gone:** use the exact compound ID from
  `read`; omitted `reply_in_thread` follows persisted `thread_id`, explicit
  boolean overrides. A gone target fails; it never becomes a fresh send.
- **Long message partial:** inspect `chunks`, ordered `message_ids`, and
  `chunk_count`. Every chunk is attempted once. Do not resend successful chunks;
  report the public error classification and reconcile first.
- **Provider key rejected:** an inbound key may be readable but not Bot-owned;
  upload from a downloaded absolute local path or use a key created by this Bot.
- **Upload code `99991672`:** publish effective `im:resource:upload` (or broad
  `im:resource`), complete tenant approval/reinstall, then make one new explicit
  upload attempt. Repeating before permission is effective only reproduces
  non-retryable `UPLOAD_FAILED` and creates no message.

### Cards/progress/reactions

- **Card click silent:** complete schema-2.0 card plus separately published
  `card.action.trigger` long-connection callback is required; ordinary message
  subscriptions are insufficient. Verify actor presence and allowlist.
- **Business click no inbox record:** an authorized distinct event ID should
  persist one `card_action` and wake. Replays of that event ID are ignored.
- **Control click no `card_action`:** correct. It updates the source control card
  locally after actor check, with bounded hash dedupe, and does not wake.
- **Card/progress update fails:** only complete schema-2.0 cards replace business
  cards. A placeholder accepts only text/Markdown/post edits, not custom card
  replacement or media. Final answer is a separate send or
  reply.
- **Reaction failure:** adding uses symbolic `emoji_type`; removal uses the
  exact returned `reaction_id`. A revoked target is non-retryable and is not
  replaced with another side effect. Event subscription is separate from write
  permission.

### Task Cards

- **No resident:** check Agent-wide `/taskcard` preference, admitted route, and
  exact account/chat/thread anchor. Automatic data comes from `logs/events.jsonl`;
  programmable data requires exact `taskcard/status=active` and non-empty body.
- **Duplicate/stale resident:** inspect only `feishu/task_cards.json`. The exact
  persisted ID is the only target; newer observed messages trigger old-first
  replacement, and unknown historical cards are never scanned/deleted.
- **One route fails:** intended isolation. Diagnose its classified result; do not
  replay successful account/chat/thread routes.

## Failure result

Feishu manager/action-validation failures have:

```json
{"status":"failed","error":"...","message":"...",
 "error_code":"RATE_LIMITED","retryable":true,"retry_after_seconds":2.0}
```

Common actions: correct `INVALID_ARGUMENT`/`FORMAT_ERROR` without retry;
correct private credentials only with owner authorization, or publish approved
permission changes in the Developer Console for `PERMISSION_DENIED`; stop on
`TARGET_REVOKED`; honor delay only when `retryable=true`; preserve
`UPLOAD_FAILED`/`DOWNLOAD_FAILED`; inspect state before retrying
`NOT_CONNECTED`/`SEND_TIMEOUT`. `max_attempts=1` means the adapter did not
silently retry. For mixed chunks or lifecycle operations,
`automatic_retry_allowed=false` overrides generic retry guidance until
reconciliation. SHOW failures use a smaller no-row envelope; see
[`message-semantics.md#failure-shape-and-settings`](message-semantics.md#failure-shape-and-settings).
