---
related_files:
- src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md
maintenance: |
  Owns third-party and legacy MCP wiring; update when registry, mcp/servers.json, transport fields, or secret handling changes.
---

# Third-party and legacy MCP routes

Non-curated servers have two routes: the **registry route** (recommended,
gated by `mcp_registry.jsonl`) and legacy `<working_dir>/mcp/servers.json`
(ungated, useful for a short experiment). Neither route grants permission to
install, configure, or disclose credentials.

## Registry route (recommended)

1. Read the server README first. Use the top-level [README gate](../SKILL.md#readme-gate):
   `find_readme.py` for an installed Python package, otherwise Web `browse` on the
   public server homepage. Obtain the exact install command, env vars, and schema.
2. With explicit human authorization, append one valid JSON record atomically to
   `mcp_registry.jsonl`, preserving existing lines. Registry records use
   **`transport`**, not the activation field `type` shown below.
3. Add its `init.json` `mcp.<name>` activation entry.
4. Run one authorized System refresh (the complete envelope is in
   [curated setup](curated-addons.md#the-four-step-setup)), then MCP `info` and
   verify the relaunched child/tool separately. A registry record is not active proof.

A minimal **registry line** (replace the placeholders from the README):

```json
{"name":"example","summary":"Example MCP server","transport":"stdio","command":"npx","args":["-y","<server-package>"],"source":"third-party","homepage":"https://example.invalid"}
```

Required fields: `name` matches `^[a-z][a-z0-9_-]{0,30}$`; `summary` is nonempty
and at most 200 characters; `source` is nonempty; `transport` is `stdio` or
`http`. Stdio requires string `command`, with optional string-list `args`;
HTTP requires string `url`. Optional `homepage` is a nonempty string.
Do not use `source: "lingtai-curated"` for a third-party launcher.

The separate `init.json` `mcp.example` value uses the **activation** shape in
the next section (`type`, command/args/env or URL/headers). Registry contents do
not supply that third-party launch configuration. The registry gates membership
and supplies homepage/diagnostics; manual/info remain read-only.

## Legacy `mcp/servers.json`

This file is loaded directly at startup without registry validation or the
catalog → registry → active promotion. Use it only when intentionally wiring a
single short-lived server; use the registry route for anything to keep. Copy the
server's documented shape, never invent fields:

```json
{
  "vision": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "<server-package>"],
    "env": {"<KEY>": "<secret-from-approved-source>"}
  },
  "remote": {
    "type": "http",
    "url": "https://example.invalid/mcp",
    "headers": {"Authorization": "Bearer <approved-key>"}
  }
}
```

## Common fields

| Field | stdio | http |
|---|---|---|
| `type` | `"stdio"` (default) | `"http"` (required) |
| `command`, `args` | executable and arguments | not applicable |
| `env` | subprocess environment | not applicable |
| `url`, `headers` | not applicable | endpoint and HTTP headers, often auth |

## Credentials

Prefer the agent's approved env-file mechanism for stdio values when the server
supports it; some addons require literal credentials in a separate config file
pointed to by an env var. HTTP credentials belong in the documented `headers`
shape, usually `Authorization: Bearer <key>`. Read the server README for its
actual contract. Never commit `mcp/servers.json`, addon configs, or plaintext
credentials.
