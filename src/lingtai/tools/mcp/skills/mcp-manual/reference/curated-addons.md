---
related_files:
- src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md
- src/lingtai/tools/mcp/skills/mcp-manual/reference/troubleshooting.md
maintenance: |
  Owns the curated addon setup route and its provider-specific hazards; update when a curated addon, launcher owner, config field, or install story changes.
---

# Curated addons — imap / telegram / feishu / wechat / whatsapp / cloud_mail

These first-party servers ship in the `lingtai` distribution as
`lingtai.mcp_servers.{imap,telegram,feishu,wechat,whatsapp,cloud_mail}`. Historical
`lingtai_*` distribution names may remain in old configs and provenance, but do
not assume their wrappers are importable; new configuration uses the bundled
modules. Set the curated config path through the addon's documented env var
(for example `LINGTAI_IMAP_CONFIG` or `LINGTAI_TELEGRAM_CONFIG`).

## The four-step setup

1. **Read the curated setup docs before editing.** Use this route and the catalog `homepage` or
   shipped resources for exact provider fields. Never guess fields, install
   commands, or environment variables: for example `email_password` (imap),
   `bot_token` (telegram), `app_id`/`app_secret` (feishu), and WeChat's host are
   addon-specific. Obtain explicit human authorization before editing.
2. **Activate in `init.json`.** Add the name to top-level `addons` and add
   `mcp.<name>` with account configuration, normally only its config-file `env`:

   ```json
   {"addons":["imap"],"mcp":{"imap":{"env":{"LINGTAI_IMAP_CONFIG":".secrets/imap.json"}}}}
   ```

   The running kernel's catalog owns a curated launcher's `type`, interpreter,
   module `args`, and `PYTHONPATH`. Existing `type`/`command`/`args` or
   `env.PYTHONPATH` are read-compatible legacy fields: they are ignored for
   launch, produce one bounded warning, and are not rewritten. If the catalog
   has no safe stdio launcher, it skips the entry rather than falling back.
3. **Create the config file** at the referenced path using the addon/provider schema verbatim.
4. **Run one** `system(action="refresh", input={"reason":"activate approved MCP configuration",
   "preset":null,"revert_preset":null}, reasoning="apply authorized setup")`.
   This requests an Agent relaunch; verify the new live child, mounted tool and
   intended account separately. Do not confuse the pre-handoff retry hook with
   the complete refresh lifecycle.

For orientation: `imap` → `lingtai.mcp_servers.imap`, `telegram` →
`lingtai.mcp_servers.telegram`, `feishu` → `lingtai.mcp_servers.feishu`,
`wechat` → `lingtai.mcp_servers.wechat`, `whatsapp` →
`lingtai.mcp_servers.whatsapp`, and `cloud_mail` →
`lingtai.mcp_servers.cloud_mail`. The module name is diagnostic/catalog
information, not a launcher to copy into a canonical curated entry.

## Runtime ownership

A curated main-agent child uses the current Agent's interpreter, catalog module
args, and imported source root. A third-party `init.json` entry or legacy
`mcp/servers.json` child instead owns its effective `type`/`command`/`args`/`env`;
a daemon/plugin MCP uses that task/plugin's config; HTTP has no local
interpreter. Do not assume any non-curated or standalone child shares the Agent's
venv, site-packages, source, or environment. See
[runtime-and-identity](runtime-and-identity.md) for authorized registry
reconciliation and the fail-closed provenance probe; process inspection alone is
not proof.

## Telegram readiness (standalone hazards)

A healthy registry record is not a usable listener. After one controlled refresh
or relaunch:

1. Confirm one live Telegram child, the `telegram` tool, and the intended account
   are mounted. For curated Telegram, editing stored `command`/`args` cannot fix
   a stale module; probe `sys.executable`, `lingtai.__file__`, and
   `lingtai.mcp_servers.telegram.__file__` first.
2. `getMe` and a deliberate direct send prove **outbound only**. Do not call
   `getUpdates` yourself while the listener owns long polling: a second poller
   contends for updates.
3. Have an allowed user send a fresh message; prove inbound delivery (LICC inbox
   or the Telegram `read` action with `input={"chat_id":<known-chat-id>}`) and
   reply using the returned compound message ID. Use the full action/input/reasoning
   envelope, not flat `chat_id` or `message_id` arguments. Account listing or
   outbound send alone is not end-to-end proof.
4. Keep one lifecycle transition—never start a duplicate parent. Protect secrets:
   directory `0700`, config `0600`; use placeholders, never real tokens, IDs,
   private paths, or raw logs.
5. Task Card changes that are real output still consume a real Telegram
   edit/send and its flood-control/rate-limit budget. Diff-skipping protects only
   **unchanged** bytes; avoid deliberate churn.

## Cloud Mail

`cloud_mail` is a self-hosted Cloud Mail REST client, **not IMAP/SMTP**. It polls
`POST /public/emailList` and delivers inbound mail through LICC. Its config env
is `LINGTAI_CLOUD_MAIL_CONFIG` (relative paths resolve under the agent dir), and
its detailed action surface is `cloud_mail(action="manual", input={}, reasoning="read Cloud Mail guidance")`.

- Public-token auth from `admin_email`/`admin_password` via `/public/genToken`
  supports read/poll/search. `user_email`/`user_password` via `/login` plus
  `send_account_id` are optional and enable `send`; sending is a real external
  **side effect** and is unavailable without user credentials.
- The first poll silently seeds `<agent_dir>/cloud_mail/<alias>/watermark.json`
  (set `notify_existing: true` to opt into existing mail). `allowed_senders` is
  case-insensitive and filtered senders still advance the watermark. Attachments
  are unsupported.

Use the provider's exact schema; a redacted shape is:

```json
{"accounts":[{"alias":"cloudmail","base_url":"https://mail.example.com","admin_email":"admin@example.com","admin_password":"REDACTED","user_email":"admin@example.com","user_password":"REDACTED","send_account_id":1,"allowed_senders":["only-this@example.com"],"poll_interval":30,"notify_existing":false}]}
```

## WeChat bootstrap

WeChat has a separate bootstrap lifecycle:

1. Ensure the current `lingtai` runtime venv contains `lingtai-wechat-bootstrap`;
   run it by full venv path, not by assuming it is on system `PATH`.
2. The MCP's `LINGTAI_WECHAT_CONFIG` relative path (typically
   `.secrets/wechat/config.json`) resolves under `LINGTAI_AGENT_DIR` first;
   project-root paths remain a backward-compatible fallback. Prefer an
   agent-relative bootstrap target. Bootstrap writes `config.json` and
   `credentials.json` together—do not copy credentials manually.
3. WSL browser opening falls back to printing an HTML path when `cmd.exe /c
   start` and `wslview` are unavailable.
4. After bootstrap, use the authorized System refresh above, then
   `wechat(action="check", input={}, reasoning="verify WeChat readiness")`.
   On `metadata.event_type: "session_expired"`, re-run bootstrap.

Inbound events arrive through `.mcp_inbox/<name>/` and are injected by the
kernel; read each addon's own manual for outbound action side effects. This
reference stops at setup and readiness.
