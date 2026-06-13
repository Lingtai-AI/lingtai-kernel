# Curated addons — imap / telegram / feishu / wechat / whatsapp / cloud_mail

LingTai's first-party email and chat integrations. They now ship inside the `lingtai` distribution under `lingtai.mcp_servers.{imap,telegram,feishu,wechat,whatsapp,cloud_mail}` so a single kernel release carries the curated MCP surface atomically. Historical `lingtai_*` import packages remain as thin compatibility wrappers. Historical standalone package names remain useful as provenance/homepage names, but the normal runtime path no longer depends on separate addon wheels.

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

## Cloud Mail setup

`cloud_mail` is a REST client for a self-hosted [Cloud Mail](https://github.com/maillab/cloud-mail) deployment (Cloudflare Workers). It is **not** IMAP/SMTP — it talks to Cloud Mail's HTTP API. Inbound mail is discovered by polling Cloud Mail's `POST /public/emailList` and delivered to your inbox via LICC.

- **Env var:** `LINGTAI_CLOUD_MAIL_CONFIG` — path to the config JSON (resolved relative to the agent dir when not absolute).
- **Omnibus tool:** `cloud_mail` with actions `check` (recent inbound mail), `search` (filter by sender/recipient/subject/content), `read` (full content by compound id `<account>:<emailId>`), `send` (requires user credentials), `accounts` (redacted status), and `add_user` (admin convenience).
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

Inbound events (new emails, chat messages) flow into your `.mcp_inbox/<name>/` via the LICC v1 inbox callback contract — the kernel auto-injects them into your next turn as `[system]` messages. You don't poll; the kernel does. Outbound calls go through the omnibus tool: `imap(action="send", ...)`, `telegram(action="send_message", ...)`, etc. — see each addon's README for the action list.

## WeChat setup checklist

WeChat has unique pitfalls that catch agents off-guard. Walk this checklist on every new WeChat setup to avoid wasting the human's time:

1. **Ensure LingTai's runtime venv is current** — the `lingtai-wechat-bootstrap` script is installed by the `lingtai` wheel and lives inside the venv, not necessarily on the system PATH.

2. **Run bootstrap with the full venv path** from the project root:
   ```bash
   ~/.lingtai-tui/runtime/venv/bin/lingtai-wechat-bootstrap .secrets/wechat
   ```

3. **No manual credential copy needed** — the MCP resolves `LINGTAI_WECHAT_CONFIG` relative to the project root (the parent of `.lingtai/`), so `.secrets/wechat/config.json` works from both bootstrap and the MCP. Credentials are written next to `config.json`.

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
