---
related_files:
- src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md
- src/lingtai/tools/mcp/skills/mcp-manual/reference/curated-addons.md
maintenance: |
  Owns MCP update, deregistration, diagnostics, and recovery routing; update when boot failures or lifecycle semantics change.
---

# MCP troubleshooting

Before an update, deregistration, or diagnosis, read the applicable setup README,
obtain explicit human authorization for edits, and call `mcp(action="info", input={}, reasoning="inspect registry and problems")`.
The snapshot includes registry contents, invalid-line/config problems, path,
counts, and status. `info` reads afresh without changing registry/config.
Problems may include raw invalid lines or quoted input: disclose only line
numbers and sanitized reasons, never the whole list, raw logs or credentials.

## Update and deregister

- **Update:** edit the matching `mcp_registry.jsonl` record in place, preserving
  its schema; apply one authorized System refresh using the
  [setup envelope](curated-addons.md#the-four-step-setup); call `info` again.
  For non-curated launcher changes, edit the activation owner too; a registry
  command alone is not the active launch spec.
- **Deregister:** remove the matching registry line. This does **not** stop a
  running child. For a curated addon remove its `addons` name as well, or boot
  can register it again. Remove the matching activation from its actual owner
  (`init.json` `mcp` or legacy `mcp/servers.json`), then request one authorized
  refresh and verify the child is gone. The retry hook alone does not stop it.
  Do not delete credentials or recovery state as a substitute.

Invalid registry lines may be skipped with a refresh warning, so always verify
with a fresh `info`. Never treat registry membership as active proof.

## Common failures

- **Cryptic `KeyError`:** a required config field is missing. Re-read
  `curated-addons.md` for curated servers or the exact third-party README; copy
  exact names (`email_password`, not `password`; `bot_token`, not `token`).
- **Start failure / command not found:** for non-curated entries, check the
  configured executable/venv or `npx`/`uvx` on `PATH`. Curated entries never read
  `init.json` `command`/`args`/`type`; probe the running kernel catalog/runtime
  instead—editing those legacy fields cannot fix it. HTTP has no local command.
- **Tools absent:** after authorized config edit, make one controlled refresh (or
  one relaunch), then call `info` and verify the live mount. Do not start a
  duplicate parent.
- **HTTP 401/403:** check the README's exact `headers` key and auth format; do
  not paste a key into a report.
- **Boots but calls say manager not initialized:** inspect the agent's stderr or
  `logs/agent.log` for the underlying config/path error, fix through the owning
  route, and refresh.
- **Curated module/closed-resource symptom:** first run the effective provenance
  probe in [runtime-and-identity](runtime-and-identity.md). Do not diagnose a
  token failure until the live interpreter and module are confirmed.

## When uncertain

1. Read the curated route or third-party README (local `find_readme.py`, then
   public `<homepage>` with Web `browse`).
2. Call `mcp(action="info", input={}, reasoning="verify MCP registry health")`.
3. Inspect the actual error in `logs/agent.log` without exposing secrets.
4. If the remedy changes registry/config, stop until explicit authorization is
   present; then perform one edit, one refresh, and one verification.
