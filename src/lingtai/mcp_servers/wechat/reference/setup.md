---
name: wechat-setup-reference
description: |
  WeChat owner setup and recovery: config/credential resolution, QR or headless
  login, read-only settings, one-poller locking, session expiry, and safe restart.
  Load before changing setup or diagnosing startup.
version: 1.1.0
last_changed_at: "2026-09-09T03:15:00Z"
related_files:
- src/lingtai/mcp_servers/wechat/SKILL.md
- src/lingtai/mcp_servers/wechat/server.py
- src/lingtai/mcp_servers/wechat/manager.py
- src/lingtai/mcp_servers/wechat/login.py
- src/lingtai/mcp_servers/wechat/lockfile.py
- src/lingtai/mcp_servers/wechat/settings.py
- src/lingtai/mcp_servers/wechat/api.py
- tests/test_wechat_config_resolution.py
- tests/test_wechat_login.py
- tests/test_wechat_settings.py
- ENVIRONMENT_VARIABLES.md
maintenance: |
  Tracks WeChat configuration, login, settings projection, and poller recovery;
  update when owner procedures, path precedence, redaction, or startup behavior changes.
---

# WeChat setup and recovery

Registration and activation belong to the host. This reference covers provider
files, owner login, startup, SHOW, and recovery after the host selects WeChat.
Obtain explicit authority before login, changing configuration/credentials,
stopping another owner or restarting the MCP; avatars must not reconfigure it.

## Configuration and credentials

`LINGTAI_WECHAT_CONFIG` points to `config.json`; sibling `credentials.json` is read
from the same directory. Absolute paths are used as-is. Relative paths resolve
against `LINGTAI_AGENT_DIR` first, then its legacy project root (two parents up)
if no file exists at the first candidate. Without an agent directory, use the
process working directory instead. Prefer the agent directory for new setups.

`config.json` may contain `base_url`, legacy `cdn_base_url`, `poll_interval` (float,
default `1.0`), and optional `allowed_users`. A falsy allow-list preserves
unrestricted compatibility behavior. Credentials contain the login-produced
`bot_token`, account `user_id`, and optional effective `base_url`; never print,
paste, commit, or diagnose their contents. Login writes credentials atomically with
mode `0600`.

### setting-config-path
`config_path` is the sensitive resolved `config.json` path captured at startup; it
is not an authored config field.

### setting-base-url
`base_url` uses truthy `credentials.json.base_url`; otherwise it uses config
`base_url`, defaulting to the provider endpoint only when that config key is
absent. Explicit null/empty config values do not select that default. The manager
loads legacy `cdn_base_url` but does not pass it to its upload call; changing it
does not override uploads through this tool.

### setting-poll-interval
`poll_interval` is converted with Python `float`; default is `1.0`. Choose a
positive finite value: runtime construction accepts zero/negative values, while
a nonfinite snapshot makes SHOW fail. This guidance is not stricter validation.

### setting-allowed-users
`allowed_users` is the optional inbound sender allow-list. Missing, null, empty, or
other falsy values mean unrestricted compatibility behavior.

### setting-bot-token
`bot_token` is the required login-produced secret; it has no default and is always
redacted by SHOW.

### setting-user-id
`user_id` is the required login-produced account identity; it has no default and is
redacted by SHOW. It is distinct from recipient IDs used by messaging actions.

## QR login (owner procedure)

Use the existing bootstrap, not a messaging action:

```text
lingtai-wechat-bootstrap <config-directory>
```

For a headless host, use the kernel environment's Python:

```text
python -c "from lingtai.mcp_servers.wechat.login import cli_login; cli_login('<config-directory>')"
```

It uses the same flow. It creates config when needed, displays an admin login QR,
polls for confirmation, and atomically replaces sibling credentials, including
any existing login. Confirm the target directory and replacement authority first. The QR authorizes the
backend account; it is not a contact/group QR. Never share it. After login, restart
or refresh the MCP through the host procedure and use `settings` only for redacted
verification. Session expiry is a reason to request authorized login/restart,
not permission to hand-edit a token or repair credentials through SHOW.

## Settings SHOW

Call the strict-empty action:

```json
{"action":"settings","input":{},"reasoning":"inspect active WeChat settings"}
```

Success is `{"settings":[...]}` with rows containing only `key`, `current`,
`default`, `configurable`, and `comment`, in order: `config_path`, `base_url`,
`poll_interval`, `allowed_users`, `bot_token`, `user_id`. Sensitive rows redact
both values. SHOW is read-only: it never writes, resets, or rereads owner files;
unavailable/invalid truth returns one bounded `SETTINGS_UNAVAILABLE` result
(`status: failed`, `error_code`, `message`), with no rows or automatic retry. All
six rows are configurable through their owner procedures. A row describes the
owner procedure for the next manager construction, not a mutation input.

## One poller per account

The iLink updates stream is single-consumer. The MCP holds an exclusive per-account
POSIX lock for the poller's lifetime and refuses a second poller; on platforms
without `fcntl`, startup fails rather than pretending safety. Normal exit releases
the lock. Do not delete lockfiles blindly: presence on disk alone does not prove a
lock is held.

If `PollerLockBusy` persists, the failed server keeps its startup-time diagnostic
and does not reacquire or rebuild its manager on ordinary calls. Inspect the named
holder and confirm the owning project; do not stop an unknown PID. With exact
authority, gracefully stop
or reconfigure the duplicate owner, verify it is gone, then restart/refresh the
intended MCP through the host procedure. The holder PID is a startup snapshot,
not current liveness. Inspect locally with `ps -p <pid> -o pid,command` and
`lsof -p <pid> 2>/dev/null | grep cwd`; do not publish raw diagnostics.
Reconcile with `read` before
sending or replying.
