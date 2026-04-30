# MCP Capability Discovery — Catalog → Registry → Activation

## What

The MCP capability is a three-layer model for discovering, registering, and activating MCP servers. The kernel ships a curated catalog; the agent opts in by registering entries in a per-agent JSONL registry; and the agent activates registered MCPs by adding entries to `init.json.mcp`. The capability itself is pure presentation — it reads the registry and renders it into the system prompt as `<registered_mcp>` XML so the agent knows what's available. All mutations happen via file operations.

## Contract

**Three-layer model.**

1. **Catalog** (kernel-shipped, read-only): `src/lingtai/mcp_catalog.json` — a dict mapping `name → {summary, transport, command, args, source, homepage}`. Known entries: imap, telegram, feishu, wechat. Template substitution: `{python}` → `sys.executable`.

2. **Registry** (per-agent, JSONL, gating): `<workdir>/mcp_registry.jsonl` — one validated record per line, append-only. Sources: (a) auto-decompressed from catalog when an addon name appears in `init.json.addons`, (b) hand-written by the agent for third-party MCPs.

3. **Activation** (per-agent, in init.json): `init.json.mcp = {name → subprocess spec}`. The loader cross-references against the registry; entries without a matching record are skipped with a warning.

**Registry record schema** (validated by `validate_record()`):
| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | str | yes | Matches `^[a-z][a-z0-9_-]{0,30}$` |
| `summary` | str | yes | Max 200 chars |
| `transport` | `"stdio"` \| `"http"` | yes | |
| `source` | str | yes | E.g., `"lingtai-curated"`, `"user"`, or a URL |
| `command` | str | conditional | Required for stdio |
| `args` | list[str] | conditional | Required for stdio (may be `[]`) |
| `url` | str | conditional | Required for http |
| `env` | dict | optional | Subprocess env vars |
| `headers` | dict | optional | http-only |
| `homepage` | str | optional | Canonical setup-doc URL |

**Boot-time decompression** (`decompress_addons()`): For each name in `init.json.addons[]` that isn't already in the registry, look up the catalog entry, substitute `{python}`, validate, and append to the registry. Append-only, idempotent, catalog-version stable (existing records are never touched).

**Subprocess loader** (`_load_mcp_from_workdir()`): Reads two sources in order:
1. `<workdir>/mcp/servers.json` — legacy, ungated, loaded as-is.
2. `init.json.mcp` — gated by registry membership. Each entry is checked against `mcp_registry.jsonl`; unregistered entries are skipped with a warning.

**Environment injection.** The kernel injects two env vars into every spawned MCP subprocess:
- `LINGTAI_AGENT_DIR` — absolute path to the host agent's working directory.
- `LINGTAI_MCP_NAME` — the MCP's registry name.

**Tool surface.** Single action: `mcp(action="show")`. Returns `{status, mcp_manual, registry_path, registered_count, registered, problems}`. The `<registered_mcp>` XML is rendered into the system prompt on every reconciliation.

**Health check.** `_reconcile()` reads the registry, renders XML into the prompt, checks for the mcp-manual SKILL.md presence, and returns a health snapshot including `problems` (invalid JSONL lines).

## Source (real file:line)

| Component | File | Lines |
|---|---|---|
| Constants (`REGISTRY_FILENAME`, `_NAME_RE`, `_VALID_TRANSPORTS`) | `core/mcp/__init__.py` | 37-43 |
| `_load_catalog()` — kernel catalog reader | `core/mcp/__init__.py` | 53-75 |
| `validate_record()` — registry record schema | `core/mcp/__init__.py` | 82-125 |
| `validate_registry_line()` — single JSONL line | `core/mcp/__init__.py` | 128-138 |
| `read_registry()` — reads + validates full registry | `core/mcp/__init__.py` | 149-186 |
| `_append_record()` — append to JSONL | `core/mcp/__init__.py` | 189-194 |
| `decompress_addons()` — catalog → registry | `core/mcp/__init__.py` | 201-257 |
| `_build_registry_xml()` — renders to system prompt | `core/mcp/__init__.py` | 274-299 |
| `_reconcile()` — registry + health snapshot | `core/mcp/__init__.py` | 306-342 |
| `setup()` — capability entry point | `core/mcp/__init__.py` | 382-406 |
| `_load_mcp_from_workdir()` — subprocess loader | `agent.py` | 274-389 |
| Registry gating in loader | `agent.py` | 371-388 |
| LICC env injection | `agent.py` | 308-310, 328-330 |

## Related

- **inbox-listener/** — the LICC poller that the spawned MCP subprocesses push events into
- **licc-roundtrip/** — end-to-end test of event flow from MCP to agent
- Anatomy reference: `intrinsic_skills/lingtai-kernel-anatomy/reference/mcp-protocol.md` §1-§3 (three-layer model, capability, subprocess loader)
