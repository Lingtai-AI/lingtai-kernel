# Curated addons — imap / telegram / feishu / wechat

LingTai's first-party email and chat integrations. Each ships as an installed Python package (`lingtai-imap`, `lingtai-telegram`, `lingtai-feishu`, `lingtai-wechat`) with its own README documenting config schema, env vars, and credentials.

## The four-step setup

1. **Read the addon's README first.** The script lives at `.library/intrinsic/capabilities/mcp/scripts/find_readme.py` and must be run with the runtime venv's Python (system `python3` may not see the addon's editable install):

   ```bash
   ~/.lingtai-tui/runtime/venv/bin/python3 \
     .library/intrinsic/capabilities/mcp/scripts/find_readme.py <pkg-name>
   ```

   `<pkg-name>` is `lingtai-imap`, `lingtai-telegram`, `lingtai-feishu`, or `lingtai-wechat`. Field names like `email_password` (imap), `bot_token` (telegram), `app_id`/`app_secret` (feishu), gewechat host (wechat) are documented there. Skipping this step is the #1 cause of "MCP boot failed" rabbit holes.

2. **Add the addon to `init.json`.** Append the registry name to the top-level `addons:` list, then add an `mcp.<name>` activation entry with the subprocess spec from the README:

   ```json
   {
     "addons": ["imap"],
     "mcp": {
       "imap": {
         "type": "stdio",
         "command": "/Users/<you>/.lingtai-tui/runtime/venv/bin/python",
         "args": ["-m", "lingtai_imap"],
         "env": {
           "LINGTAI_IMAP_CONFIG": ".secrets/imap.json"
         }
       }
     }
   }
   ```

3. **Create the config file** at the path referenced by the env var (e.g. `.secrets/imap.json`). Use the schema from the README — copy it verbatim, don't paraphrase.

4. **Run `system(action="refresh")`.** The `mcp` capability decompresses the catalog record into `mcp_registry.jsonl`, the loader spawns the subprocess, and the omnibus tool (`imap`, `telegram`, etc.) appears in your tool surface.

## Distribution names

| Registry name | Distribution name  | Module name        |
|---------------|--------------------|--------------------|
| `imap`        | `lingtai-imap`     | `lingtai_imap`     |
| `telegram`    | `lingtai-telegram` | `lingtai_telegram` |
| `feishu`      | `lingtai-feishu`   | `lingtai_feishu`   |
| `wechat`      | `lingtai-wechat`   | `lingtai_wechat`   |

Pass `<distribution name>` to `find_readme.py`. Pass `<module name>` to `find_readme.py --module`.

## After it's running

Inbound events (new emails, chat messages) flow into your `.mcp_inbox/<name>/` via the LICC v1 inbox callback contract — the kernel auto-injects them into your next turn as `[system]` messages. You don't poll; the kernel does. Outbound calls go through the omnibus tool: `imap(action="send", ...)`, `telegram(action="send_message", ...)`, etc. — see each addon's README for the action list.
