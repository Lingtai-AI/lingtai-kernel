---
related_files:
- src/lingtai/tools/mcp/manual/SKILL.md
- src/lingtai/tools/mcp/manual/reference/troubleshooting.md
maintenance: |
  Kernel-curated MCP addon setup contract routed to from mcp/manual/SKILL.md and cross-linked from troubleshooting.md; update it whenever a curated addon (imap/telegram/feishu/wechat/whatsapp/cloud_mail) changes its config fields or install story.
---

# Curated addons — imap / telegram / feishu / wechat / whatsapp / cloud_mail

LingTai's first-party email and chat integrations. They now ship inside the `lingtai` distribution under `lingtai.mcp_servers.{imap,telegram,feishu,wechat,whatsapp,cloud_mail}` so a single kernel release carries the curated MCP surface atomically. Historical `lingtai_*` names may still appear in old configs or compatibility source, but new configurations must use the bundled `lingtai.mcp_servers.*` modules; do not assume a historical wrapper is importable in the active runtime. Historical standalone package names remain useful as provenance/homepage names, but the normal runtime path no longer depends on separate addon wheels.

## The four-step setup

1. **Read the curated setup docs before editing config.** The table below gives the registry/module/env/config-file names. If exact provider-specific fields are needed, inspect the shipped module resources or the catalog `homepage` for that addon. Field names like `email_password` (imap), `bot_token` (telegram), `app_id`/`app_secret` (feishu), and gewechat host (wechat) are addon-specific; do not guess them from memory.

2. **Add the addon to `init.json`.** Append the registry name to the top-level `addons:` list, then add an `mcp.<name>` activation entry with the subprocess spec from this table or the addon docs:

   ```json
   {
     "addons": ["imap"],
     "mcp": {
       "imap": {
         "type": "stdio",
         "command": "/Users/<you>/.lingtai-tui/runtime/venv/bin/python",
         "args": ["-m", "lingtai.mcp_servers.imap"],
         "env": {
           "LINGTAI_IMAP_CONFIG": ".secrets/imap.json"
         }
       }
     }
   }
   ```

3. **Create the config file** at the path referenced by the env var (e.g. `.secrets/imap.json`). Use the schema from the addon docs — copy it verbatim, don't paraphrase.

4. **Run `system(action="refresh")`.** The `mcp` capability decompresses the catalog record into `mcp_registry.jsonl`, the loader spawns the subprocess, and the omnibus tool (`imap`, `telegram`, etc.) appears in your tool surface.

## Module names

| Registry name | Historical distribution | Module name        |
|---------------|-------------------------|--------------------|
| `imap`        | formerly `lingtai-imap`     | `lingtai.mcp_servers.imap`     |
| `telegram`    | formerly `lingtai-telegram` | `lingtai.mcp_servers.telegram` |
| `feishu`      | formerly `lingtai-feishu`   | `lingtai.mcp_servers.feishu`   |
| `wechat`      | formerly `lingtai-wechat`   | `lingtai.mcp_servers.wechat`   |
| `whatsapp`    | formerly `lingtai-whatsapp` | `lingtai.mcp_servers.whatsapp` |
| `cloud_mail`  | (no standalone distribution) | `lingtai.mcp_servers.cloud_mail` |

Use the module name in `mcp.<name>.args`, e.g. `["-m", "lingtai.mcp_servers.feishu"]`. Historical distribution names are retained only for provenance and compatibility notes.

## Telegram setup/readiness checklist

Use this checklist as the Telegram setup acceptance test. It is intentionally separate from the generic catalog/registry steps above: a healthy registry record is not proof that the live listener is usable.

1. **Use the current launch module.** The Telegram child must be launched with `-m lingtai.mcp_servers.telegram` and `LINGTAI_TELEGRAM_CONFIG=.secrets/telegram.json` (or the agent-relative equivalent). Replace a stale `-m lingtai_telegram` in `init.json` or `mcp_registry.jsonl`; it can fail with `ModuleNotFoundError`, leave the stdio child down, and surface to the parent as a closed MCP/closed-resource symptom. Do not diagnose that symptom as a Telegram token failure until the launch module is corrected.

2. **Check both registry and runtime layers.** `mcp(action="info")` proves only that the registry is readable and reports its records/problems. It does **not** prove that a child is mounted. After one controlled refresh or relaunch, confirm there is one live Telegram MCP child/server, the `telegram` tool is mounted, and the intended configured account is mounted; an `info` entry by itself is insufficient.

3. **Separate outbound from inbound proof.** Startup `getMe` and one deliberate direct send prove only outbound Bot API reachability. They do not prove that the listener is receiving updates or that inbound events reach the host agent. Do not call the Bot API `getUpdates` yourself while the Telegram listener may own long polling; a second poller can contend for updates and invalidate the test.

4. **Make one controlled lifecycle change.** After editing a sidecar or Telegram config, perform exactly one controlled `system(action="refresh")` or one controlled relaunch, then inspect the resulting child and mount. Do not start a duplicate parent. A passing readiness check requires the live Telegram MCP child/server **and** its account mounted after that single transition.

5. **Prove the complete inbound/reply path.** Have an allowed-user producer send a fresh test message. Verify the producer's inbound read reaches the host (LICC inbox delivery or `telegram(action="read", chat_id=<chat-id>)`), then reply on that same channel with `telegram(action="reply", message_id=<inbound-message-id>, text=<sanitized-test-reply>)` or the equivalent channel send. An account listing, `getMe`, or an outbound send alone is not end-to-end proof.

6. **Lock down the config.** The secrets directory must be mode `0700` and the Telegram config must be mode `0600`:

   ```bash
   chmod 700 .secrets
   chmod 600 .secrets/telegram.json
   ```

   Use placeholders such as `<bot-token>`, `<allowed-user-id>`, `<chat-id>`, and `<inbound-message-id>` in examples and reports; never include real tokens, IDs, private paths, or raw logs.

## Cloud Mail setup

`cloud_mail` is a REST client for a self-hosted [Cloud Mail](https://github.com/maillab/cloud-mail) deployment (Cloudflare Workers). It is **not** IMAP/SMTP — it talks to Cloud Mail's HTTP API. Inbound mail is discovered by polling Cloud Mail's `POST /public/emailList` and delivered to your inbox via LICC.

- **Env var:** `LINGTAI_CLOUD_MAIL_CONFIG` — path to the config JSON (resolved relative to the agent dir when not absolute).
- **Omnibus tool:** `cloud_mail`. Its action surface is owned by the addon's own manual — `cloud_mail(action="manual")`.
- **Auth model:** the addon mints a *public token* from `admin_email`/`admin_password` via `/public/genToken` for read/poll/search, and logs in with `user_email`/`user_password` via `/login` for `send`. If user creds are absent, read/check/search/poll still work; only `send` is disabled with a clear error.
- **Watermark:** the first poll seeds the per-account high-water mark silently (no flood of old mail) unless `notify_existing: true`. State lives under `<agent_dir>/cloud_mail/<alias>/watermark.json`.

Config schema (plaintext; copy verbatim, never commit real passwords):

```json
{
  "accounts": [
    {
      "alias": "cloudmail",
      "base_url": "https://mail.example.com",
      "admin_email": "admin@example.com",
      "admin_password": "REDACTED",
      "user_email": "admin@example.com",
      "user_password": "REDACTED",
      "send_account_id": 1,
      "allowed_senders": ["only-this@example.com"],
      "poll_interval": 30,
      "notify_existing": false
    }
  ]
}
```

`user_email`/`user_password`/`send_account_id` are optional and only required for `send`. `allowed_senders` (case-insensitive) limits which inbound senders raise an inbox event; the watermark still advances for filtered senders so they never replay. Attachments are not supported in this first pass.

## After it's running

Inbound events (new emails, chat messages) flow into your `.mcp_inbox/<name>/` via the LICC v1 inbox callback contract — the kernel auto-injects them into your next turn as `[system]` messages. You don't poll; the kernel does. Outbound calls go through the omnibus tool: `imap(action="send", ...)`, `telegram(action="send", ...)`, etc. Each addon owns its own action surface and side-effect rules — pull it with `<addon>(action="manual")`; this file stops at setup.

## WeChat setup checklist

WeChat has unique pitfalls that catch agents off-guard. Walk this checklist on every new WeChat setup to avoid wasting the human's time:

1. **Ensure LingTai's runtime venv is current** — the `lingtai-wechat-bootstrap` script is installed by the `lingtai` wheel and lives inside the venv, not necessarily on the system PATH.

2. **Run bootstrap with the full venv path.** The `LINGTAI_WECHAT_CONFIG` relative path (typically `.secrets/wechat/config.json`) resolves against `LINGTAI_AGENT_DIR` first (the agent working dir, like imap/telegram/feishu), then falls back to the project root for backward compatibility. **Preferred:** write secrets into the agent dir, e.g. from the project root:
   ```bash
   ~/.lingtai-tui/runtime/venv/bin/lingtai-wechat-bootstrap .lingtai/<agent>/.secrets/wechat
   ```
   Older setups that wrote `.secrets/wechat` at the **project root** still work via the backward-compat fallback — no migration is required (`Lingtai-AI/lingtai#336`).

3. **No manual credential copy needed** — `config.json` and `credentials.json` are written together in whichever directory you point bootstrap at, and the MCP reads `credentials.json` next to `config.json`.

4. **WSL users**: bootstrap auto-detects WSL and uses `cmd.exe /c start` or `wslview` to open the browser. If neither works, it prints the HTML file path for manual opening.

5. **Refresh the MCP** after bootstrap writes credentials:
   ```
   system(action="refresh")
   ```

6. **Test the connection**:
   ```
   wechat(action="check")
   ```

7. **Session expiry** — WeChat sessions expire (~30 days). When expired, a LICC event with `metadata.event_type: "session_expired"` arrives. Re-run the bootstrap to re-authenticate.
