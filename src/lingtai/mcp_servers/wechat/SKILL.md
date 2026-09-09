---
name: wechat-mcp-manual
description: |
  Progressive-disclosure usage manual for WeChat/iLink: first safe read, exact
  IDs, external send/reply, media partials, and standalone poller/setup hazards.
  Use the schema for routine calls; load this manual for media details,
  unfamiliar results, setup, or recovery.
version: 2.1.0
last_changed_at: "2026-09-09T03:15:00Z"
related_files:
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/mcp_servers/wechat/manager.py
- src/lingtai/mcp_servers/wechat/server.py
- src/lingtai/mcp_servers/wechat/_family.py
- src/lingtai/mcp_servers/wechat/plugin.py
- src/lingtai/mcp_servers/wechat/settings.py
- src/lingtai/mcp_servers/wechat/api.py
- src/lingtai/mcp_servers/wechat/media.py
- src/lingtai/mcp_servers/wechat/login.py
- src/lingtai/mcp_servers/wechat/lockfile.py
- src/lingtai/mcp_servers/wechat/reference/operations.md
- src/lingtai/mcp_servers/wechat/reference/media.md
- src/lingtai/mcp_servers/wechat/reference/setup.md
- tests/test_wechat_toolfamily_ltpv2.py
- tests/test_wechat_media_validation.py
- tests/test_wechat_login.py
- tests/test_wechat_settings.py
- tests/test_wechat_config_resolution.py
- ENVIRONMENT_VARIABLES.md
maintenance: |
  Tracks the WeChat MCP action catalog and its progressive-disclosure safety,
  media, and setup routes; update this router and the owning references when the
  provider behavior, setup boundary, settings projection, or public actions change.
---

# WeChat MCP

Use the schema for routine calls; this progressive disclosure guide adds media,
setup/recovery and unfamiliar result details, not a per-send reading ritual.

## First safe action

```json
{"action":"check","input":{},"reasoning":"find WeChat conversations"}
```

Then `read` an exact `user_id` from `check`, `read`, or `contacts`. For `reply`, use
an inbound `message_id` from `read`; do not invent recipients. `search` matches
inbox bodies by regex; it cannot prove that an outgoing reply is absent.
Calls require
`action`, closed action-owned `input`, and `reasoning`; optional `summarize` is
boolean. The advertised schema owns the action catalog and validation.

## External effects and recovery

`send`/`reply` are real external side effects: obtain authority and verify recipient
and content. Success is provider acceptance, not delivery (`delivery_confirmed`
is false); never replay an accepted request. `reply` cannot fall back to a new
recipient if its inbound target or sender is missing.

`media_path` is a file inside the allowed working directory. Text precedes media;
partial results or uncertain errors require reconciliation, not whole-request
replay. A missing local sent record does not prove nothing was accepted. Read
[operations](reference/operations.md) for result/read-state limits and
[media](reference/media.md) for paths, validation and upload stages.

`getUpdates` has one consumer per bot account. The per-account lock refuses a
second poller; do not delete its lockfile or start another. After refresh, worker
failure or recovery, use `read` to reconcile merged inbox/sent history before
replying. Local history is context, not a delivery receipt.

Login, configuration and credential replacement require the owner's setup
authorization. Avatar sessions must not reconfigure this MCP. Never share the
admin login QR, token or credentials. See [setup/recovery](reference/setup.md);
host addon activation remains owned by `mcp-manual`.

## Settings SHOW

`settings` takes `input={}` and returns the startup snapshot without writing or
rereading owner files. Five sensitive rows redact both `current` and `default`;
only `poll_interval` is displayed. An unavailable row fails the whole inventory.
SHOW grants no configuration authority. Each stable anchor routes directly:

## setting-config-path
[Resolved config source](reference/setup.md#setting-config-path).

## setting-base-url
[Endpoint precedence](reference/setup.md#setting-base-url).

## setting-poll-interval
[Polling interval/default](reference/setup.md#setting-poll-interval).

## setting-allowed-users
[Inbound allow-list](reference/setup.md#setting-allowed-users).

## setting-bot-token
[Login secret](reference/setup.md#setting-bot-token).

## setting-user-id
[Backend identity](reference/setup.md#setting-user-id).

Sidecars ship with the package but are not embedded in `manual`; follow the
relative links only when their detail is needed.
