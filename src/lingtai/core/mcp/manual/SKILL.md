---
name: mcp-manual
description: >
  Operational guide for the `mcp` capability — register, activate, update,
  deregister, and troubleshoot MCP (Model Context Protocol) servers in your
  agent. Single source of truth for both generic MCP setup AND the four
  kernel-curated LingTai addon MCPs (`imap`, `telegram`, `feishu`, `wechat`).

  Reach for this manual when:
    - The human asks to install, set up, configure, or remove an MCP server.
      Decision tree: is it kernel-curated (imap/telegram/feishu/wechat) → the
      `addons:` + `init.json mcp.<name>` workflow with the per-addon README is
      here. Is it third-party → the registry route OR the legacy
      `mcp/servers.json` route, both documented here.
    - The human asks to set up the `imap` / `telegram` / `feishu` / `wechat`
      addon, or any LingTai email/chat integration. **Step 1 is always**:
      fetch the addon's README with the bundled script —
      `~/.lingtai-tui/runtime/venv/bin/python3 .library/intrinsic/capabilities/mcp/scripts/find_readme.py <pkg-name>`
      (where `<pkg-name>` is `lingtai-imap` / `lingtai-telegram` /
      `lingtai-feishu` / `lingtai-wechat`). Field names like `email_password`,
      `bot_token`, `app_id`/`app_secret`, gewechat hosts are documented in
      each addon's README and nowhere else. Do NOT guess at config; ALWAYS
      read the README first.
    - You want to know what MCPs you currently have. `mcp(action="show")`
      returns the registry plus health; this manual explains the output.
    - An MCP isn't behaving — registry validation, `problems` list,
      refresh-after-edit verification, common boot errors are here.
    - You're exploring an unfamiliar MCP. The doc-discovery flow (local
      `scripts/find_readme.py` first, homepage URL fallback) is here.

  Covers (progressively, via reference/): the three states (catalog →
  registry → active), the curated-vs-third-party install paths, the legacy
  `mcp/servers.json` direct mount route (still functional, ungated), HTTP
  and stdio server configurations, where the registry file
  (`mcp_registry.jsonl`) lives and how to mutate it (`write` / `edit` /
  `bash` — the `mcp` capability is read-only), the `<homepage>` field as
  fallback documentation, and the relationship between `init.json`'s
  `addons:` list, `mcp:` activation entries, and the registry. Replaces the
  deprecated `lingtai-mcp` skill.

  Does NOT cover the protocol spec itself: schema validation rules, env
  injection mechanics (the `LINGTAI_AGENT_DIR` / `LINGTAI_MCP_NAME`
  variables), the LICC v1 inbox callback contract, and the validator's
  internal logic all live in `lingtai-kernel-anatomy reference/mcp-protocol.md`.
  Read this for *what to do*; read anatomy for *how it works*.
version: 3.2.0
---

# MCP Capability — How To Use It

The `mcp` capability is your interface to Model Context Protocol (MCP) servers — both generic third-party servers and the four kernel-curated LingTai addons (`imap`, `telegram`, `feishu`, `wechat`). Like the `library` capability, it is **pure presentation**: registered MCPs are listed in your system prompt under `<registered_mcp>`, and the registry itself is a JSONL file you edit directly with `write` / `edit` / `bash`.

This is the router. Detail lives in `reference/`. Load only what you need.

## Three states of an MCP

For any MCP server, relative to this agent:

1. **In the kernel catalog** — LingTai blesses it. Reference template ships with the kernel. The four curated addons live here: `imap`, `telegram`, `feishu`, `wechat`.
2. **Officially registered** — appears as a line in `mcp_registry.jsonl` (sibling to `init.json`). The system prompt's `<registered_mcp>` lists it.
3. **Active** — the MCP server subprocess is running, its tools are mounted in your tool surface.

Promotion path: catalog → registry → active. You move things along by editing files and calling `system(action="refresh")`.

## Pick a sub-skill

| Task | Read |
|---|---|
| Set up an `imap` / `telegram` / `feishu` / `wechat` addon | `reference/curated-addons.md` |
| Add a third-party MCP (`npx`/`uvx`/HTTP) | `reference/third-party-and-legacy.md` |
| Wire up a server quickly via `mcp/servers.json` (legacy/ungated) | `reference/third-party-and-legacy.md` |
| MCP not behaving / cryptic boot errors / `KeyError: 'foo'` | `reference/troubleshooting.md` |
| Update or deregister an MCP | `reference/troubleshooting.md` |
| Spec-level questions (schema, env injection, LICC) | `lingtai-kernel-anatomy reference/mcp-protocol.md` |

**Before any setup or troubleshooting**, fetch the relevant addon's README with:

```bash
~/.lingtai-tui/runtime/venv/bin/python3 \
  .library/intrinsic/capabilities/mcp/scripts/find_readme.py <pkg-name>
```

`<pkg-name>` is `lingtai-imap`, `lingtai-telegram`, `lingtai-feishu`, or `lingtai-wechat` (or any other Python-installed MCP). Full details: see §Reading an MCP's README below.

## Reading an MCP's README

Every MCP server's README is the canonical install + config + troubleshooting doc — config field names, env vars, error meanings, the lot. **Always read the README before guessing at config.** For curated addons in particular, field names like `email_password` (imap) or `bot_token` (telegram) are documented there and nowhere else.

### 1. Local README (preferred)

If the MCP is installed as a Python package (all four kernel-curated addons are), run the script with the **runtime venv's Python** — the same interpreter where `lingtai_imap` / `lingtai_telegram` / etc. are actually installed:

```bash
~/.lingtai-tui/runtime/venv/bin/python3 \
  .library/intrinsic/capabilities/mcp/scripts/find_readme.py <pkg-name>
```

(`python3` from your `$PATH` may resolve to a system or conda interpreter that doesn't see the venv's installed packages — always use the venv's Python explicitly.)

The script tries the editable repo on disk first, then falls back to the README embedded in the wheel's `METADATA` file (PEP 566). Works for editable installs and normal PyPI wheels alike. Pass `--module <modname>` if you only know the importable module name (e.g. `lingtai_imap`) instead of the distribution name.

Distribution names for the four kernel-curated addons:

| Registry name | `<pkg-name>`       | Module name        |
|---------------|--------------------|--------------------|
| `imap`        | `lingtai-imap`     | `lingtai_imap`     |
| `telegram`    | `lingtai-telegram` | `lingtai_telegram` |
| `feishu`      | `lingtai-feishu`   | `lingtai_feishu`   |
| `wechat`      | `lingtai-wechat`   | `lingtai_wechat`   |

### 2. Homepage URL (fallback)

If the script prints `ERROR: no README found locally` (or the MCP isn't a Python package — e.g. an `npx`-launched server), fetch the registry's `<homepage>` field with `web_read`. Each registered MCP exposes this when known.

### 3. Runtime self-description (last resort)

If neither path yields docs, fall back to the MCP's own runtime self-description: once activated, its tool descriptions appear in your tool surface, and many servers also publish a server-level `instructions` string at connection time.

## Tool surface

One action: `mcp(action="show")`. Returns this manual body, the current registry contents, and a runtime health snapshot (registry path, count, problems).

All registry mutations happen via `write` / `edit` / `bash`. The `mcp` capability never writes to the registry.

## See also

- **Canonical spec**: `lingtai-kernel-anatomy reference/mcp-protocol.md` — full three-layer model, env injection, validator schema, **LICC v1** inbox callback contract, reference implementations.
- **File formats**: `lingtai-kernel-anatomy reference/file-formats.md` §2.7 (init.json `addons` + `mcp` fields), §6 (`mcp/servers.json` legacy direct mounts), §6.5 (`mcp_registry.jsonl`), §6.6 (`.mcp_inbox/<name>/<id>.json` LICC events).
