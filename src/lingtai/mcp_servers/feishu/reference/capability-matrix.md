# Feishu v1 capability matrix

This matrix describes the bundled LingTai adapters at this release. It is a
review and rollout aid, not a promise that Feishu and Telegram expose identical
provider concepts.

## User-facing and Agent-facing capabilities

| Capability | Feishu v1 | Telegram reference | Notes |
|---|---|---|---|
| Multi-account lifecycle | Yes | Yes | Feishu uses one app/WS listener per stable alias; the first account is the outbound default. |
| Direct messages | Yes | Yes | Feishu DM requires sender admission but no mention. |
| Group admission | Explicit `@Bot` | Bot-specific policy | Feishu `@all` alone is ignored; `allowed_users` gates actors, not chat IDs. |
| Native topics/threads | Yes | Provider topic metadata | Feishu reply follows the persisted `thread_id` by default and Task Card routes include the optional thread. |
| Text and rich inbound | Text, post, todo/task, shares, cards, and normalized future families | Text/caption/entities and raw Update branches | Feishu keeps a compatibility text preview plus normalized content and the complete raw envelope. |
| Inbound media preservation | Image, file, audio, video, sticker, post resources; failed downloads remain traceable | Photo, document, voice, audio with failed-download metadata | Feishu can transcribe downloaded audio locally. |
| Raw provider event recovery | Complete `feishu` envelope on records | Complete `telegram` Update envelope | Both are read-time diagnostic data and excluded from ordinary notification previews. |
| Plain outbound text | Yes; legacy `text` shortcut retained | Yes; explicit rendering mode | Feishu accepts exactly one of `text` or structured `content`. |
| Rich outbound text | Markdown-to-post, raw post AST | HTML, Markdown, MarkdownV2, entities | Provider formatting models remain intentionally different. |
| Outbound media | Image, file, audio, video from absolute path or Bot-owned key | Photo/document and captions from local path | Feishu also supports chat/user shares and stickers. |
| Reply target safety | Reply, thread-aware; gone target fails | Reply to exact compound ID | Feishu never silently promotes a failed reply to a fresh message. |
| Edit/delete | Text/post/card edit; exact delete | Text/markup edit; exact delete | Feishu media edits are not supported. |
| Interactive business callbacks | Schema-2.0 card send/update; authorized callback persistence and wake | Inline keyboard callback updates | Feishu uses stable event-ID dedupe and per-account/chat serialization. |
| Native progress | Typing reaction plus updateable schema-2.0 progress card | Typing/chat action plus editable progress message | Both require a separate durable final answer. |
| Public reaction action | Add by `emoji_type`, remove by `reaction_id` | Automatic reactions; no matching public family action | Feishu also keeps automatic seen/typing/done reactions. |
| Passive event retention | Reaction, read, Bot join/leave in reserved `events` | Broad non-chat Updates in reserved `updates` | Passive buckets do not wake the Agent and are not outbound targets. |
| Local control commands | Nine commands in localized `zh`/`en`/`wen` schema-2.0 cards | Same shared command core with Telegram presentation | Feishu group commands still require `@Bot`; unknown commands pass to the Agent. |
| Control navigation/callbacks | Kanban/system drill-down, local actor-check and dedupe | Inline-keyboard navigation | Internal callbacks do not become business inbox events. |
| Automatic Task Card | Shared safe event projection, resident per account/chat/thread | Same projection, resident per account/chat | Shared rendering remains Telegram byte-compatible. |
| Programmable Task Card | Shared intrinsic producer composed as independent `WATCH` slot | Same producer/slot model | One route failure does not block others. |
| Resident rotation | Persist exact ID; conservative restart; old-first rotation | Persist exact ID; conservative restart; old-first rotation | Neither adapter discovers or deletes unknown historical orphan cards. |
| Classified failures | Stable failed/error/error_code/retryable/retry-after shape | Telegram-specific error surface | Feishu performs one outbound attempt per wire chunk. |
| LICC persistent context | Bounded conversation, routing, mentions, attachment projection | Bounded conversation plus Telegram-specific reply references | Full raw envelopes/provider keys stay behind channel `read`. |

## Feishu action/content surface

The public `feishu` family has 13 actions:

`send`, `check`, `read`, `reply`, `react`, `search`, `delete`, `edit`,
`contacts`, `add_contact`, `remove_contact`, `accounts`, and `manual`.

`send` and `reply` support:

- `text`, `markdown`, `post`, and schema-2.0 `card`;
- `image`, `file`, `audio`, and `video`;
- `share_chat`, `share_user`, and `sticker`; and
- legacy plain `text` as a compatibility shortcut.

`edit` supports text, Markdown/post, and complete schema-2.0 card replacement.
`react` is intentionally separate from message content.

## Deliberate v1 non-goals and limits

- No kernel-level per-token Feishu streaming. Progress cards update only at
  meaningful phases; the final answer is a separate message.
- No hidden outbound retry or implicit plaintext downgrade. Provider errors are
  returned to the caller.
- No fallback from a revoked reply target to a fresh message.
- No arbitrary URL media download and no relative-path upload. This keeps
  outbound source ownership explicit.
- No media-message edit path.
- No Feishu group-chat-ID allowlist in JSON. Canary chat scope is controlled by
  app availability/Bot membership, with a non-empty sender `allowed_users` list.
- No Feishu command-menu registration equivalent to Telegram `setMyCommands`.
  Commands are discovered through `/help` and card navigation.
- No automatic wake for reactions, read receipts, or Bot membership events.
- No raw-envelope or provider-key copy into the normal persistent notification
  lane.
- No scan, adoption, or deletion of unknown historical Task Cards. Only exact
  persisted resident IDs are mutation targets.
- No runtime config switch for SDK `security.mode`; v1 remains on `compat` until
  a separate audit-backed hardening PR changes it.
- No configurable Lark/global OpenAPI domain in the current account JSON. This
  documented setup targets Feishu tenants using the SDK's default domain.

See [`setup.md`](setup.md) for deployment and permissions, and
[`diagnostics.md`](diagnostics.md) for provider/config failure isolation.
