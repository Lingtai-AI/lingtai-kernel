---
related_files:
- ENVIRONMENT_VARIABLES.md
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/feishu/SKILL.md
- src/lingtai/mcp_servers/feishu/server.py
- src/lingtai/mcp_servers/feishu/service.py
- src/lingtai/mcp_servers/feishu/settings.py
- tests/test_feishu_settings.py
maintenance: |
  Keep this operator route aligned with Feishu app permissions/events, the
  protected account configuration, launcher environment, and rollout owner.
  Model-facing behavior belongs in SKILL.md/message-semantics.md; symptoms
  belong in diagnostics.md.
---
# Feishu setup, canary, and rollback

Use this sidecar for first installation, account/config changes, or a controlled
rollout. It is the owner for app permissions and launcher configuration; start
with the model-facing [`../SKILL.md`](../SKILL.md) for ordinary calls and use
[`diagnostics.md`](diagnostics.md) for a symptom.

Configuration, credential changes, rollout, and rollback require explicit
orchestrator/owner authorization. Avatars must not reconfigure this MCP.
Before integration changes, read `mcp-manual`, inspect `mcp(info)`, then follow
its curated Feishu activation route; do not start a competing listener.

## App and event setup

Create a custom Feishu/Lark app, enable its Bot capability, restrict availability
during canary, and choose **long connection**. This adapter uses app credentials
and WebSocket; it has no QR login or public webhook.

Grant only the scopes required by the enabled slice:

| Scope | Use |
|---|---|
| `im:message` | message read/manage APIs |
| `im:message:send_as_bot` | send, reply, edit, delete |
| `im:message.p2p_msg:readonly` | DM events |
| `im:message.group_at_msg:readonly` | explicitly mentioned group events |
| `im:resource` | inbound download (also permits upload) |
| `im:resource:upload` | narrower outbound upload permission |
| `im:message.reactions:read` | reaction events |
| `im:message.reactions:write_only` | seen, typing, done, public reactions |

Subscribe to `im.message.receive_v1` for admitted messages. Optional subscriptions
populate the reserved `events` history without waking the Agent:

- `im.message.reaction.created_v1` and `im.message.reaction.deleted_v1`;
- `im.message.message_read_v1`;
- `im.chat.member.bot.added_v1` and `im.chat.member.bot.deleted_v1`.

Interactive
cards additionally require `card.action.trigger` callback delivery over the
long connection. Publish/reinstall after changing permissions, subscriptions,
app availability, or callbacks; draft console changes do not affect the Bot.

## Protected config

Set `LINGTAI_FEISHU_CONFIG` on the MCP entry. Relative paths resolve against
`LINGTAI_AGENT_DIR`; `.secrets/feishu.json` is a conventional private location.
Do not put credentials in `init.json`, command lines, repository files, or logs.

```json
{
  "accounts": [
    {
      "alias": "main",
      "app_id": "cli_xxxxxxxx",
      "app_secret": "keep-in-secret-file",
      "allowed_users": ["ou_test_user"]
    }
  ]
}
```

| Field | Required | Runtime meaning |
|---|---:|---|
| `accounts` | yes | truthy list of account objects |
| `accounts[].alias` | yes | stable local name and compound-ID segment |
| `accounts[].app_id` | yes | Feishu app identifier (normally `cli_...`) |
| `accounts[].app_secret` | yes | app credential; never expose it |
| `accounts[].allowed_users` | no | sender `open_id` set; omission/null/empty is unrestricted compatibility behavior |

The loader directly indexes the required account fields; it does not validate
string formats, alias uniqueness, or allowlist element shape. Duplicate aliases
remain in service order while the later entry replaces the lookup-map value.
Use stable unique aliases and a non-empty allowlist as operator guidance. The
first account is the default when an outbound action omits `account`. Each
account has its own REST/WS listener and state; Task Card preferences are
Agent-wide. Compound IDs remain `{alias}:{chat_id}:{feishu_message_id}` and
must be passed back verbatim where required.

A canary **must** use a non-empty `allowed_users` list and app availability/Bot
membership restricted to designated test groups. An allowlist is sender-only,
not a chat-ID allowlist; group/topic messages still need an explicit `@Bot`.
Saving a contact does not alter admission. Restrict the config file to the Agent
owner. If a secret reaches evidence/logs, disclose it and seek owner-authorized
rotation promptly; preserve the evidence and never rotate or clean logs silently.

## Acceptance sequence

After deploying or changing config, refresh/restart the Agent and verify in the
same runtime:

1. `lingtai://status`: readable config, initialized manager, started service,
   expected account count; inspect no secrets.
2. Logs: `Feishu listener running` and WebSocket connected, with no event bodies.
3. Allowed DM receives one durable reply; unmentioned group input is ignored and
   an explicit Bot mention is answered.
4. Topic reply stays in its topic; `read` confirms normalized rich/media types
   and attachment status.
5. One business-card click creates one authorized `card_action`; replay and
   unauthorized clicks do not wake. A local command-card click stays local.
6. Seen/typing/done, one public reaction add/remove, and a native progress card
   work; final answer is a separate message.
7. Automatic/programmable Task Cards remain conservative after refresh rather
   than duplicating an exact resident.

Record only synthetic scenario, normalized type/status, public error code,
retryability, redacted timestamp, and pass/fail. Never record IDs, text, raw
envelopes, attachment paths, provider keys, tokens, or credentials.

## Runtime defaults and rollback

Before replacing an installed candidate, record its version and keep a backup
of the adapter/config outside the repository; never copy secrets into evidence.

The pinned SDK is `lark-channel-sdk>=1.2,<2`; security mode is fixed `compat`,
not a JSON setting. Each outbound chunk uses `max_attempts=1`; `retryable` and
`retry_after_seconds` are caller guidance, not hidden retries. Media accepts only
an absolute readable path or explicit Bot-owned key, never URL/relative sources.

Rollback is package-first and state-preserving:

1. Stop/refresh so the candidate releases its WebSocket.
2. Restore the recorded package overlay; restore config only when deployment
   changed it, and never restore an older exposed secret.
3. Keep `<agent>/feishu/` intact: it contains inbox/sent history, attachments,
   contacts, callback claims, Task Card preferences, and resident bindings.
4. Start/refresh and repeat status, WebSocket, DM, group-mention, and topic checks.
5. If console permissions/subscriptions changed, restore and publish the prior
   console configuration.

Keep rollback evidence to a redacted code, retry classification, timestamp,
scenario, and candidate version. Use [`diagnostics.md`](diagnostics.md) before
retrying a provider side effect.
