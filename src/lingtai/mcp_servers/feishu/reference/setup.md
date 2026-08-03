# Feishu Bot setup, canary, and rollback

This guide is the operator-facing source for installing and rolling out the
bundled LingTai Feishu channel. For model-facing message/action semantics, read
[`../SKILL.md`](../SKILL.md). For symptom-based investigation, read
[`diagnostics.md`](diagnostics.md).

## 1. Create and publish the Feishu app

Create a custom app in the Feishu Developer Console, enable the Bot capability,
and make the app available only to the intended test users during the canary.
LingTai uses app credentials and a long WebSocket connection; it does not use a
QR-login flow or require a public webhook endpoint.

Grant the tenant/app permissions required by the enabled slice:

| Permission | Needed for |
|---|---|
| `im:message` | Read and manage messages through the IM OpenAPI. |
| `im:message:send_as_bot` | Send, reply, edit, and delete as the Bot. |
| `im:message.p2p_msg:readonly` | Receive direct-message events. |
| `im:message.group_at_msg:readonly` | Receive group messages that explicitly `@Bot`. |
| `im:resource` | Download inbound message resources; this broad permission also satisfies outbound media upload. |
| `im:resource:upload` | Upload outbound media without granting broad resource access. Prefer this narrower permission when only upload is missing. |
| `im:message.reactions:read` | Receive and inspect reaction events. |
| `im:message.reactions:write_only` | Add and remove seen, typing, done, and public reactions. |

Feishu may present a broader aggregate permission instead of one of the narrow
permissions above. Use the Developer Console's permission requested by the
corresponding API when the tenant UI differs. In particular, an outbound upload
permission error can name either `im:resource:upload` or the broad
`im:resource`; grant the narrower upload permission when the console offers it.
Do not infer an unlisted download-scope name. The current LingTai adapter does
not need contact-directory access to admit a sender.

Select **long connection** as the event delivery mode and subscribe to:

| Event | Requirement | LingTai behavior |
|---|---|---|
| `im.message.receive_v1` | Required | Admitted messages can wake the Agent. |
| `im.message.reaction.created_v1` | Optional | Recorded in the reserved `events` conversation. |
| `im.message.reaction.deleted_v1` | Optional | Recorded in `events`; does not wake the Agent. |
| `im.message.message_read_v1` | Optional | Recorded in `events`; does not wake the Agent. |
| `im.chat.member.bot.added_v1` | Optional | Records the Bot joining a chat. |
| `im.chat.member.bot.deleted_v1` | Optional | Records the Bot leaving a chat. |

Interactive cards need one additional console setting: configure card action
callbacks to use the app's long connection and publish that configuration.
The wire callback is `card.action.trigger`; an ordinary message subscription
does not prove that card clicks are being delivered.

Publish or reinstall the app after changing permissions, subscriptions, app
availability, or card callback settings. Console changes that remain in draft
do not affect the running Bot.

## 2. Configure LingTai

Set `LINGTAI_FEISHU_CONFIG` on the Feishu MCP entry to a JSON file. A relative
path is resolved against `LINGTAI_AGENT_DIR`; a conventional per-Agent location
is `.secrets/feishu.json`.

```json
{
  "accounts": [
    {
      "alias": "main",
      "app_id": "cli_xxxxxxxx",
      "app_secret": "replace-in-the-secret-file",
      "allowed_users": ["ou_test_user"]
    }
  ]
}
```

The config fields are:

| Field | Required | Meaning |
|---|---:|---|
| `accounts` | yes | Non-empty array of app accounts. |
| `accounts[].alias` | yes | Unique local account name. It becomes the first segment of compound message IDs and selects account-local state. |
| `accounts[].app_id` | yes | Feishu app ID (`cli_...`). It is an identifier, not the app credential secret. |
| `accounts[].app_secret` | yes | Feishu app secret. Store it only in the secret config file. |
| `accounts[].allowed_users` | no | Sender `open_id` values admitted for messages, passive events, and card actions. |

`allowed_users` has compatibility semantics: omitting it, setting it to `null`,
or supplying an empty list disables the sender gate. A canary must therefore use
a **non-empty** list. Saving a contact alias does not grant admission and does
not replace this list.

For multiple accounts:

- each account starts its own REST client and WebSocket listener;
- aliases must be unique and stable across upgrades;
- the first account is used when an outbound action omits `account`;
- compound IDs remain
  `{account_alias}:{chat_id}:{feishu_message_id}` and must be passed back
  unchanged; and
- account message/contact/state directories remain separate, while Feishu
  Task Card preferences are Agent-wide.

Do not commit the config file. Restrict its filesystem permissions to the Agent
owner, and rotate `app_secret` immediately if it reaches a log, chat, issue, PR,
artifact, or generated setup page.

The MCP entry follows the normal curated-addon shape:

```json
{
  "mcp": {
    "feishu": {
      "type": "stdio",
      "command": "/path/to/the/lingtai/runtime/python",
      "args": ["-m", "lingtai.mcp_servers.feishu"],
      "env": {
        "LINGTAI_FEISHU_CONFIG": ".secrets/feishu.json"
      }
    }
  }
}
```

The kernel supplies `LINGTAI_AGENT_DIR` and `LINGTAI_MCP_NAME`. Do not add
credentials directly to `init.json` or the process command line.

## 3. Canary deployment

Use a reversible deployment of the candidate package or worktree in the same
runtime that launches the Agent. Before replacing it, record the current
package version and back up the current installed Feishu adapter plus Agent
config. Keep the backup outside the repository and never copy the secret into
PR evidence.

Constrain the canary in both places:

1. configure a non-empty `allowed_users` list containing only test actors; and
2. make the app available to, and add the Bot to, only the designated test
   group(s).

The adapter has a sender allowlist, not a separate chat-ID allowlist. Limiting
test groups is therefore an app-availability/group-membership operation.
Ordinary group and topic messages still require an explicit mention of this
Bot; `@all` alone is not admission.

Refresh or restart the Agent after deploying or changing configuration. Then
validate with real Feishu events in this order:

1. Read `lingtai://status`: config is readable, the manager is initialized,
   the service is started, and the expected account count is present.
2. Confirm the process log reaches `Feishu listener running` and the SDK
   reports a WebSocket connection without printing event bodies.
3. Send a DM from the allowed test actor and receive a durable reply.
4. In the test group, confirm an unmentioned message is ignored and an
   explicit `@Bot` message is answered.
5. Send a topic message and confirm the reply remains in that topic.
6. Send image, file, audio, video, sticker, rich post, and task/todo samples;
   use `read` to verify normalized type and attachment status without copying
   the raw envelope into evidence.
7. Send/update a schema-2.0 business card, click it once, and verify one
   authorized `card_action`; replay and unauthorized clicks must not wake the
   Agent.
8. Exercise a localized local command and one navigation button. Internal
   control clicks should update the card locally, not create business inbox
   records.
9. Verify native typing/seen/done reactions, one public `react` add/remove, and
   a progress card whose final answer is a separate message.
10. Confirm automatic and programmable Task Card slots, then refresh and
    verify the persisted card is conservatively updated rather than duplicated.

Record only the scenario, normalized type/status, and pass/fail result. Do not
record real actor/chat/message IDs, provider keys, attachment paths, message
text, raw envelopes, tokens, or credentials.

## 4. Runtime and safety defaults

- The adapter currently pins `lark-channel-sdk>=1.2,<2` and constructs
  `SecurityConfig(mode="compat")`. Moving to `audit` or `strict` is a separate
  audited rollout, not a configuration field in this JSON.
- Every outbound wire chunk uses `max_attempts=1`. LingTai never hides an
  automatic provider retry. A caller may make a new attempt only from the
  returned `retryable` and `retry_after_seconds` guidance.
- URL media sources and relative local paths are rejected. Outbound media uses
  an absolute readable local path or an explicit Bot-owned provider key.
- Complete inbound Feishu envelopes are deliberately retained behind `read`
  for diagnosis. They are sensitive operational data, not PR/log material.
- Runtime files under `<agent>/feishu/` include inbox/sent records, downloaded
  attachments, contacts, callback claims, Task Card preferences, and exact
  resident bindings. Back them up and handle them as private Agent state.

## 5. Rollback

Rollback is package-first and state-preserving:

1. Stop or refresh the Agent so the candidate MCP no longer owns the WebSocket.
2. Restore the previously recorded package/install overlay. Restore config only
   if the deployment changed it; never replace a current rotated secret with an
   older exposed value.
3. Leave `<agent>/feishu/` in place. Deleting it loses inbox history, exact card
   bindings, contacts, and callback claims and may cause duplicate visible
   state after restart.
4. Start or refresh the Agent and repeat the status, WebSocket, DM, and group
   mention checks against the restored version.
5. If the candidate changed Developer Console permissions or subscriptions,
   restore the last published console configuration and reinstall/publish it.

When the rollback reason is a provider failure, retain only a redacted error
code, retry classification, timestamp, candidate commit/version, and scenario.
Use [`diagnostics.md`](diagnostics.md) to distinguish config, transport,
admission, content, and callback failures before retrying the rollout.
