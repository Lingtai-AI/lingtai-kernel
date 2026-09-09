---
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/feishu/SKILL.md
- src/lingtai/mcp_servers/feishu/_family.py
- src/lingtai/mcp_servers/feishu/plugin.py
- src/lingtai/mcp_servers/feishu/settings.py
- tests/test_feishu_settings.py
maintenance: |
  Keep this compact inventory aligned with Feishu's composed action family,
  read-only settings provider, owner manual, and focused tests. Put procedures
  in the setup, diagnostics, and message-semantics references instead.
---
# Feishu v1 capability matrix

This is a scope map, not a second procedure manual. Feishu and Telegram share
some intents but not provider concepts.

| Capability | Feishu behavior | Important boundary |
|---|---|---|
| Accounts | Multiple app accounts; first is outbound default | `account` and compound IDs select the owner |
| Admission | DMs; groups/topics need explicit `@Bot`; `allowed_users` gates actors | `@all` alone and saved contacts do not admit |
| Inbound | Text/post/task/share/card plus image/file/audio/video/sticker resources | normalized records retain traceable failed downloads; raw envelope is behind `read` |
| Outbound | Text, Markdown/post, schema-2.0 card, media, share, sticker | media uses absolute local path or Bot-owned key; no URL/relative upload |
| Reply/edit/delete | Thread-aware reply; logical chunk-group lifecycle | gone reply fails; edit/delete are for Bot-sent IDs and never silently fall back |
| Cards | Business card callbacks and localized local-control cards | business clicks wake/persist; control clicks update locally |
| Progress | Native Typing reaction and updateable schema-2.0 progress card | final answer is a separate durable message |
| Reactions/events | Public add/remove plus automatic presence; passive events in `events` | one attempt; passive events do not wake or accept sends |
| Notifications/LICC | Bounded attention hook plus persistent conversation/routing context | raw envelopes and provider keys stay behind `read` |
| Task Cards | Automatic and programmable resident projections per route | not public messages; unknown historical cards are never scanned/deleted |
| Manager/action-validation failures | Stable `error_code`, `retryable`, `retry_after_seconds`; SHOW has its own no-row envelope | `max_attempts=1`; caller reconciles partial/indeterminate state |

## Public action/content inventory

The single `feishu` family has 14 actions, in order:
`send`, `check`, `read`, `reply`, `react`, `search`, `delete`, `edit`,
`contacts`, `add_contact`, `remove_contact`, `accounts`, `settings`, `manual`.
`settings` and `manual` require strict-empty input; settings never writes.

`send` and `reply` accept tagged `text`, `markdown`, `post`, `card`, `image`,
`file`, `audio`, `video`, `share_chat`, `share_user`, and `sticker` (or legacy
plain `text`). `edit` accepts text/markdown/post/card only.

## Deliberate v1 limits

- No per-token streaming, hidden retry, plaintext downgrade, or gone-reply
  fallback. Progress is meaningful-phase feedback, not the final answer.
- No arbitrary URL/relative media upload, media-message edit, or JSON chat-ID
  allowlist. Group canaries use app availability/membership plus `allowed_users`.
- No command-menu registration, automatic wake for passive events, or raw
  envelope/provider-key copy into the ordinary notification lane.
- No scan, adoption, or deletion of unknown historical Task Cards.
- `security.mode` and the OpenAPI domain are not runtime config fields in this
  slice; setup targets the SDK's default domain and `compat` security mode.

For exact model-facing procedures read [`message-semantics.md`](message-semantics.md).
For deployment read [`setup.md`](setup.md); for symptoms read [`diagnostics.md`](diagnostics.md).
