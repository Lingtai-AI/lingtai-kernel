# Feishu Bot diagnostics

Use this runbook after the setup checklist in [`setup.md`](setup.md). Start from
redacted state and narrow the failing boundary before inspecting any complete
message or callback envelope.

## Safe diagnostic order

1. Read `lingtai://status`.
2. Check lifecycle logs for MCP start, account start, listener readiness, and
   WebSocket connect/reconnect lines.
3. Call `feishu` with `action="accounts"` to confirm aliases and non-secret
   public identity.
4. Use `check`, then `read` on only the affected conversation.
5. Inspect one downloaded attachment or one raw envelope only when the
   normalized record is insufficient; never paste it into an issue or PR.

`lingtai://status` contains:

| Field | Interpretation |
|---|---|
| `status` | `ok` means the manager was constructed; it does not prove provider connectivity or event delivery. |
| `manager_initialized` | The config/build boundary completed. |
| `service_started` | The service lifecycle start ran. This is not a WebSocket health probe. |
| `config_path_set` | `LINGTAI_FEISHU_CONFIG` is present. |
| `config_path` | Resolved local path. Treat it as private machine metadata in external evidence. |
| `config_readable` | The file exists and could be parsed for safe status. |
| `accounts_count` | Number of parsed config accounts. |
| `accounts[].alias` / `app_id` | Non-secret configured identity. |
| `accounts[].has_app_id` / `has_app_secret` | Presence booleans; the secret value is never returned. |
| `accounts[].allowed_users_count` | `null` means no sender gate; `0` also behaves as unrestricted compatibility config. A canary should show a positive count. |
| `notes` | Safe config/status reader findings. |

## Startup and configuration

### `LINGTAI_FEISHU_CONFIG env var not set`

Add the variable to the Feishu MCP entry and refresh. Relative paths resolve
against `LINGTAI_AGENT_DIR`, not necessarily the shell's current directory.

### `Feishu config not found` or `config_readable: false`

Resolve the path shown by safe status locally. Check that the Agent owner can
read the file and traverse its parent directories. Do not solve this by moving
the secret into the repository or loosening it to world-readable.

### `config must contain 'accounts' (list)` or account construction fails

Require a non-empty array. Every item needs a unique `alias`, `app_id`, and
`app_secret`; `allowed_users`, when present, contains sender `open_id` strings.
An empty allowlist is not block-all.

### Manager starts but the account identity is not verified

Confirm the credentials in the Developer Console, the app is installed and
published in the tenant, and the runtime can reach Feishu OpenAPI. A cached Bot
identity may preserve group mention matching during a temporary identity lookup
failure, but it is not evidence that the current credentials are valid.

## WebSocket and refresh/reconnect

### No WebSocket connected line

Check outbound network access, app credentials, long-connection event mode, and
whether another stale MCP instance still owns the same Agent lifecycle. Refresh
once after correcting the cause. Repeated blind refreshes can hide the first
useful error and create overlapping connection logs.

### Connected, then exits or repeatedly reconnects

Correlate only timestamps and exception classes. Check provider status, network
stability, credential rotation, and whether the installed SDK satisfies
`lark-channel-sdk>=1.2,<2`. The Feishu account owns a dedicated WebSocket thread
and event loop; a reconnect must not require changing application data.

### Refresh works, but old Task Cards appear uncertain

This is expected conservative behavior. The last-message high-water mark is
process-local. After restart the adapter updates the exact persisted resident
in place until it observes a newer route message; it must not guess and send a
duplicate. Do not delete state to force a rotation.

## Message admission

### DM produces no Agent wake

Check, in order:

1. `im.message.receive_v1` is subscribed over long connection and published;
2. the app is available to the sender;
3. the sender's `open_id` is in a non-empty `allowed_users` list, if used; and
4. `check`/`read` does not already show the message as admitted.

DMs do not require `@Bot`.

### Group or topic message is ignored

The sender must pass the account allowlist and explicitly mention this Bot.
`@all` alone is intentionally ignored. Confirm the test used a real Bot mention,
not plain text containing the Bot name. If DM works but every group mention is
silent, check the receive-message permission/subscription and the Bot's
membership in that group.

### Labels and content arrive as separate turns

Feishu sends a text label such as `语音：` and the following audio as two
independent messages. LingTai preserves both IDs and does not merge them. The
Agent can infer adjacency from the conversation window, but a diagnostic must
verify the actual media message's `content.kind` and `attachments`, not expect
the label record to own the attachment.

### Message is stored but does not wake the Agent

Reaction, read, and Bot join/leave records intentionally go to the reserved
`events` conversation with no wake. Ordinary admitted messages and authorized
business `card_action` records wake. Internal control-card callbacks are handled
locally and neither enter the business inbox nor wake the Agent.

## Rich text and inbound media

### Bot says only that it received an image/file

Use `read` and inspect the normalized record:

- `content.kind` must identify the original family (`image`, `file`, `audio`,
  `video`, `sticker`, `post`, `todo`, and so on);
- `attachments[].status="downloaded"` supplies the safe local path, filename,
  and size; and
- `attachments[].status="failed"` retains the resource type/key and a bounded
  error so the failure remains traceable.

The Agent should inspect the attachment with the appropriate local capability
instead of responding from the placeholder text. Provider keys and the complete
raw envelope stay behind `read` and should not be copied into prompts, logs, or
PR evidence without a specific need.

### Download fails

Confirm `im:resource`, reinstall/publish after the permission change, and check
that the resource belongs to the message being read. Inbound resources are
downloaded through Feishu's message-resource API; LingTai does not fetch an
arbitrary URL. A failed record should still retain the original descriptor.

### Audio downloads but has no transcript

Check the attachment status first. Then confirm `faster-whisper` is installed in
the exact runtime venv launching the MCP and inspect the bounded transcription
error. The downloaded audio remains available even when transcription fails.
Do not repeatedly upload sensitive audio to external debugging services.

### Rich post or task/todo is flattened unexpectedly

Confirm `message_type` and `content.kind` in `read`. The top-level `text` is a
compatibility preview; the normalized `content` and raw `feishu` envelope are
the source for structural diagnosis. A successful canary should distinguish a
rich `post` and a task/todo event rather than report them as generic files.

## Outbound content and replies

### Rich send/reply fails before provider I/O

Supply exactly one of legacy `text` or structured `content`. The tagged content
union is closed; unknown fields, mixed variants, relative media paths, URL media
sources, and unsupported captions are local `INVALID_ARGUMENT` failures.

### Topic reply appears outside the topic

Use the compound ID from the persisted incoming record. With
`reply_in_thread` omitted, LingTai follows the target record's `thread_id`.
Verify that the inbound record contains the expected thread metadata; an
explicit boolean overrides the default. A gone target returns failure and never
silently becomes a fresh message.

### One long message partially succeeds

Inspect `chunks`, `message_ids`, and `chunk_count`. Each materialized chunk is
attempted once, in order. Do not resend successful chunks blindly. Report the
failed chunk's public error classification without including its text.

### Media key reuse is rejected

An inbound user's provider key can be downloadable but not owned by the Bot for
outbound reuse. Upload from the downloaded absolute local path, or use a key
previously created by this Bot. Do not convert the key into a URL.

### Local media upload reports provider code `99991672`

The app lacks an effective resource-upload permission. In the Developer
Console, grant `im:resource:upload` when it is available; the broader
`im:resource` also satisfies the API but is not required solely for upload.
Create and publish a new app version, then complete any tenant-admin approval or
reinstallation the console requires. A draft permission change is not enough.

After the published permission is effective, make one new explicit upload
attempt. Before that point, repeating the same send only reproduces a
non-retryable `UPLOAD_FAILED` result and must not create a message side effect.

## Cards and callbacks

### Card sends, but clicking a button does nothing

Confirm the complete schema-2.0 card was accepted, then verify the Developer
Console's card callback delivery is set to long connection and published.
Message event subscriptions alone are insufficient. Also check that the click
includes an actor and that actor passes `allowed_users`.

### Business click shows client success but no inbox record

An authorized business click should create one `message_type="card_action"`
record in the source conversation and wake the Agent. Its stable event ID is the
dedupe key. A repeated delivery of the same event is ignored; a later distinct
click is retained even when it has the same button value.

### Local-command button creates no `card_action`

That is correct. Namespaced control callbacks are actor-checked, serialized,
durably claimed by a bounded hash, and handled locally by updating the source
card. Diagnose them from the visible card result and redacted lifecycle errors,
not from the business inbox.

### Card edit or progress update fails

Only complete schema-2.0 cards can replace cards. A `placeholder=true` message
is a native progress card; later text/Markdown/post edits remain progress-card
updates. The final answer must be a separate durable send/reply. Media cannot be
edited through the same path.

## Reactions and passive events

### Seen/Typing/done or public `react` fails

Confirm reaction write permission and that the target message still exists.
Adding uses a symbolic `emoji_type`; removing uses the exact returned
`reaction_id`. A revoked target is non-retryable and must not be replaced with a
new message.

### Reactions work but no reaction events are recorded

Outbound reaction permission and inbound event subscription are separate.
Subscribe and publish both reaction-created and reaction-deleted events when the
reserved `events` history is required. These events never wake the Agent.

## Automatic and programmable Task Cards

### No resident card appears

Check `/taskcard` or the Agent-wide `feishu/taskcard.json` preference, then
confirm the route has been admitted and has an exact account/chat/thread anchor.
Automatic projection reads the Agent's `logs/events.jsonl`; programmable content
appears only when intrinsic `taskcard/status` is exact `active` and
`taskcard/taskcard.md` is non-empty.

### Duplicate or stale resident card is suspected

Inspect only the exact binding stored in `feishu/task_cards.json`. LingTai never
scans and deletes guessed historical cards. When a newer route message is
observed, replacement is old-first: the tracked old card must be deleted or
confirmed missing before one replacement is sent. A failed replacement can
legitimately leave zero cards and should surface as a partial failure.

### One chat fails while others continue

This is intended route isolation. Automatic and programmable slots share a
per-route transaction, but one route's provider failure does not block another
account/chat/thread route. Diagnose the failed route's classified outcome
without replaying successful routes.

## Failure result interpretation

Every public action failure has this stable shape:

```json
{
  "status": "failed",
  "error": "human-readable compatibility text",
  "message": "same text",
  "error_code": "RATE_LIMITED",
  "retryable": true,
  "retry_after_seconds": 2.0
}
```

Common classifications include:

| `error_code` | Operator action |
|---|---|
| `INVALID_ARGUMENT` | Correct the local action shape; do not retry unchanged. |
| `PERMISSION_DENIED` | Fix/publish permissions or credentials, then make a new explicit attempt. |
| `FORMAT_ERROR` | Correct the Feishu content/card schema. |
| `TARGET_REVOKED` | Stop; do not convert a reply/reaction/edit into a fresh side effect. |
| `RATE_LIMITED` | Retry only when `retryable=true`, after `retry_after_seconds` when present. |
| `UPLOAD_FAILED` / `DOWNLOAD_FAILED` | Preserve the record, diagnose ownership/permission/source, then retry explicitly if safe. |
| `NOT_CONNECTED` / `SEND_TIMEOUT` | Check lifecycle/connectivity and ambiguity before attempting again. |

`max_attempts=1` means a retryable result is guidance, not evidence that the
adapter already retried. For a timeout or other indeterminate transport result,
check persisted/provider state before sending duplicate external content.

## Evidence and privacy checklist

Safe PR/issue evidence includes candidate commit/version, a synthetic scenario,
normalized message/content type, attachment status, public error code,
retryability, redacted timestamps, and pass/fail.

Do not attach:

- `app_secret`, tokens, auth headers, or config contents;
- real actor, chat, message, tenant, provider-resource, or reaction IDs;
- raw message/card callback envelopes or original chat text;
- attachment contents or absolute local paths;
- `state.json`, inbox/sent JSON, contact files, callback claim files, or Task
  Card binding files; or
- unredacted process command lines and WebSocket URLs containing device or
  connection identifiers.

If the raw envelope is essential, reproduce with a synthetic tenant/message and
reduce it to the smallest field-shape fixture before sharing.
