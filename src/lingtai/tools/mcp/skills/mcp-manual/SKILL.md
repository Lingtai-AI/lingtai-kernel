---
name: mcp-manual
description: >
  Read-only MCP status router: distinguish catalog, registered, and active servers;
  inspect effective settings safely; and route authorized setup or recovery to the
  exact provider and runtime references. Routine info/settings/manual calls need
  no ritual manual reading. This is not an MCP protocol guide.
version: 3.6.0
last_changed_at: 2026-09-09T03:20:00Z
related_files:
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/tools/mcp/__init__.py
- src/lingtai/tools/mcp/settings.py
- src/lingtai/tools/mcp/ANATOMY.md
- src/lingtai/tools/mcp/CONTRACT.md
- src/lingtai/tools/mcp/skills/mcp-manual/reference/curated-addons.md
- src/lingtai/tools/mcp/skills/mcp-manual/reference/third-party-and-legacy.md
- src/lingtai/tools/mcp/skills/mcp-manual/reference/troubleshooting.md
- src/lingtai/tools/mcp/skills/mcp-manual/reference/runtime-and-identity.md
- src/lingtai/tools/mcp/skills/mcp-manual/scripts/find_readme.py
- tests/test_mcp_settings.py
maintenance: |
  Keep this short router aligned with the routed references, installed manual layout, and mcp description; update when MCP behavior, setup ownership, or safety boundaries change.
---

# MCP capability

`mcp` is a **read-only signpost**, not a server-management API. Every action
uses strict `input={}`; routine inspection needs no prerequisite reading:

```text
mcp(action="info", input={}, reasoning="inspect MCP registry health")
mcp(action="settings", input={}, reasoning="show effective MCP settings")
mcp(action="manual", input={}, reasoning="read MCP guidance")
```

- **info** re-reads registry/identity caches, reconciles the protected prompt
  section, and returns contents, problems and registry path—not the manual.
- **settings** reads effective init values; it neither edits nor proves liveness.
- **manual** returns this installed body as flat `mcp_manual` and `manual_path`,
  without registry I/O. Missing installation yields a degraded result, not fallback.

None registers, activates, configures, or fixes a server. Leave root
`summarize=false` when exact names or paths matter. Registry `problems` can
contain raw invalid lines: never publish the whole result or raw diagnostics;
report only line numbers and sanitized reasons, not credentials or private paths.

## States and change gate

**Catalog** is the kernel-shipped reference; **registered** is a valid
`mcp_registry.jsonl` record beside `init.json`; **active** means a live server
with mounted tools. A record or `info` success does not prove active status.

Before setup, update, deregistration or recovery: read the matching route and
exact server README, call `info`, and obtain explicit human authorization before edits.
Registry/config changes use authorized file write/edit, then **one** controlled
System refresh and live verification. Public refresh requests an Agent relaunch;
it is not merely the pre-handoff failed-child retry hook. See runtime ownership
below before changing a launcher. `/addon` is retired; `/mcp` is the only current TUI command for this surface.
It is read-only status/config inspection, not a setup wizard.

## Routes

| Need | Read |
|---|---|
| Curated addon setup: exact provider docs and Telegram/Cloud Mail/WeChat hazards | [curated addons](reference/curated-addons.md#the-four-step-setup) |
| Third-party registry or legacy stdio/HTTP activation | [third-party and legacy](reference/third-party-and-legacy.md) |
| Update, deregister, missing tools or failure | [troubleshooting](reference/troubleshooting.md) |
| Identity cache, installed paths, venv/source provenance | [runtime and identity](reference/runtime-and-identity.md) |
| Protocol, env injection or LICC | `lingtai-kernel-anatomy` → `reference/mcp-protocol.md` |
| Footprint inspection / separately approved cleanup | `skills-manual` → `reference/cleanup-footprint-contract.md` |

## README gate

Installation, config fields, env vars and error meanings come from the exact
server README—never guess. For an installed Python package use:

```bash
<runtime-venv-python> .library/intrinsic/capabilities/mcp/scripts/find_readme.py <distribution>
```

Add `--module` for module-to-distribution lookup. The helper prefers editable
source README, then wheel `METADATA`. If absent, browse the registered public
`homepage` with `web(action="browse", input={"url":"<homepage>", "link_ref":null,
"cursor":null, "extract":null, "max_chars":null}, reasoning="read server README")`.
Runtime self-description is the last resort; the helper installs/fetches nothing.

## Configuration settings

SHOW returns exactly `{"settings": [...]}`; each row has `key`, `current`,
`default`, `configurable`, `comment`, in that order. The inventory is bounded to
65,536 UTF-8 bytes. Source/read/serialization failure returns one fixed no-row
failure, not partial values or exception text.

| Key | Meaning | Authorized change |
|---|---|---|
| `init.addons` | Fresh canonical effective list; default `[]`, not registry/live health. | Edit top-level `init.json` `addons`, refresh, SHOW again. Removal does not delete a registry row. |
| `init.mcp` | Fresh canonical activation mapping; both current and default always `<redacted>`. | Edit top-level `init.json` `mcp`, refresh, then verify the child/tool/account—not just `info`. Registry membership gates activation. |

SHOW excludes registry/identity files, legacy `mcp/servers.json`, private addon
config/session data, Task Cards and live process state. It grants no edit authority.

## Cleanup / footprint

Inspect only selected MCP-owned registry, legacy activation, inbox and addon
assets using the shared route above; default inspection writes/deletes nothing.
Never blindly delete credentials, messages, audit records, active state or
recovery evidence. Present a dry-run and obtain explicit consent before archive
or deletion. Any separately approved audit/apply write records timestamp, mode,
candidate count/bytes, path summary and approval in `logs/cleanup.jsonl`.
