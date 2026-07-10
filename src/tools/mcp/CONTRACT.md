---
name: mcp-contract
tool: mcp
contract_version: 1
related_files:
  - src/tools/mcp/__init__.py
  - src/tools/mcp/ANATOMY.md
  - src/lingtai/services/mcp_registry.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# MCP capability contract

`mcp` is a SIGNPOST-ONLY, read-only tool: it renders the per-agent MCP server
registry into the protected `mcp` system-prompt section and reports registry
health. It does NOT register, activate, configure, or troubleshoot MCP servers —
all mutations happen by editing `mcp_registry.jsonl` with `write`/`edit`. The tool
slice lives in `src/tools/mcp/__init__.py`; the registry machinery it renders
lives in `src/lingtai/services/mcp_registry.py` (imported lazily). The code is the
source of truth.

## Routing Card

**Use this when:**
- You are editing the mcp tool slice's action dispatch or the reconciliation that
  builds the prompt XML.
- You need to confirm which fields `info` surfaces and that the tool never mutates
  MCP configuration.

**Do not use this for:**
- Registry validation, JSONL I/O, catalog load, identity projection, or addon
  decompression: those are the service at
  `src/lingtai/services/mcp_registry.py`.
- Code navigation only: read `src/tools/mcp/ANATOMY.md`.
- Actually registering an MCP: edit `mcp_registry.jsonl` with `write`/`edit`,
  then call `system(action="refresh")`.

**Fast paths:** tool schema -> §Tool surface; registry file location & writers ->
§State & storage; the `tools → lingtai` lazy back-edge -> §Scope.

## Scope

- Canonical tool name: `mcp`.
- Registered via `capabilities=["mcp"]` or via init.json.
- Symmetric to `skills` / `knowledge`: a per-agent presentation capability with a
  protected prompt section.
- Non-goals: this tool never writes the registry, never launches or configures a
  server, and never troubleshoots one. It is purely `info` (re-render + health)
  and `manual` (return the umbrella manual body).
- Ownership boundary: the module is the agent-callable tool slice only. The
  registry service is imported lazily inside `setup` and the handlers, per the
  `tools → lingtai` lazy-back-edge rule.

## Tool surface

Schema requires `action`; the handler is `handle_mcp` (dispatched via
`dispatch_action`). Exactly two read-only actions.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `info` | `action="info"` | — | reconciles registry, re-injects prompt XML, returns `{status: "ok", registry_path, registered_count, registered: [{name, summary, identity?}], problems}` | see below |
| `manual` | `action="manual"` | — | `{status: "ok", mcp_manual, manual_path}` | degraded shape below |

Each `registered` entry is `{name, summary}` and carries `identity` only when a
matching identity record with non-empty `accounts` exists. `manual` returns
`status: "degraded"` with an empty `mcp_manual` and an `error` string when
`.library/intrinsic/capabilities/mcp/SKILL.md` is missing.

**Error shapes** (plain dicts):
- Unknown action: `{"status": "error", "message": "unknown action: <action>, only 'info' or 'manual' is supported"}`.

## State & storage

The capability reads (never writes) the per-agent registry:

```text
<agent>/mcp_registry.jsonl      # one JSON record per line, sibling to init.json
```

Writers of this file are OUTSIDE this tool: the agent (`write`/`edit`) and the
boot-time addon decompression (`decompress_addons`, run by the Agent initializer,
which appends catalog entries named in init.json's `addons: [...]`, append-only
and idempotent). Identity records are read separately via `read_identities`. `mcp`
only reads, validates, and renders; `info` re-reads and re-injects on demand.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Registry path:** resolved by `_registry_path(working_dir)` in the service; the
  file sits beside `init.json` in the agent working dir.
- **Prompt injection:** the registry XML is written to the protected `mcp` section
  via `agent.update_system_prompt("mcp", xml, protected=True)`.
- **Lazy import:** `src/lingtai/services/mcp_registry.py` is imported lazily inside
  `_reconcile` / `setup`, keeping the `tools → lingtai` back-edge deferred.
- **Identity safety:** identity projection strips secret fields before they can
  reach the prompt; only allowlisted, non-secret account fields are surfaced.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| The capability renders the registry into the `mcp` prompt section | `src/tools/mcp/__init__.py` (`_reconcile`) | `tests/test_mcp_capability.py::test_mcp_capability_renders_registry_into_prompt` |
| `info` returns a health snapshot | `src/tools/mcp/__init__.py` (`_reconcile`) | `tests/test_mcp_capability.py::test_mcp_show_action_returns_health_snapshot` |
| Unknown actions return a `{status: error}` dict | `src/tools/mcp/__init__.py` (`handle_mcp`) | `tests/test_mcp_capability.py::test_mcp_show_unknown_action_returns_error` |
| init.json `addons: [...]` triggers append-only decompression | `src/lingtai/services/mcp_registry.py` | `tests/test_mcp_capability.py::test_addons_list_triggers_decompression`, `::test_decompress_is_idempotent` |
| Duplicate / invalid registry lines are dropped | `src/lingtai/services/mcp_registry.py` | `tests/test_mcp_capability.py::test_registry_drops_duplicates_by_name`, `::test_registry_drops_invalid_lines` |
| Identity is attached only when present and secrets are stripped | `src/tools/mcp/__init__.py` (`_registered_entry`) / service | `tests/test_mcp_identity_discovery.py::test_show_action_includes_identity_when_present`, `::test_secret_fields_are_stripped_from_accounts` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Registry renders into the prompt | `tests/test_mcp_capability.py::test_mcp_capability_renders_registry_into_prompt` | Add a registry line, inspect the `mcp` prompt section | Registered MCPs invisible to the model |
| Tool is read-only (no mutation) | `tests/test_mcp_capability.py::test_mcp_show_action_returns_health_snapshot` | Call `info`, confirm `mcp_registry.jsonl` unchanged | Signpost promise violated; surprise mutations |
| Unknown actions handled | `tests/test_mcp_capability.py::test_mcp_show_unknown_action_returns_error` | Call `mcp(action="foo")` | Silent mis-dispatch |
| Addon decompression is idempotent | `tests/test_mcp_capability.py::test_decompress_is_idempotent` | Boot twice with the same `addons`, diff the registry | Duplicate registry growth |
| Secrets never reach the prompt | `tests/test_mcp_identity_discovery.py::test_secret_fields_are_stripped_from_accounts` | Add an identity with a secret field, inspect `info` output | Credential leakage into the prompt |

Run before merging:

```bash
python -m pytest tests/test_mcp_capability.py tests/test_mcp_identity_discovery.py -q
```
