---
related_files:
- src/lingtai/tools/mcp/skills/mcp-manual/SKILL.md
- src/lingtai/tools/mcp/__init__.py
- src/lingtai/services/mcp_registry.py
- src/lingtai/tools/mcp/CONTRACT.md
- tests/test_mcp_identity_discovery.py
- tests/test_tool_family_mcp_migration_parity.py
maintenance: |
  Owns identity projection, installed-manual paths, launcher ownership, and venv/source provenance; update when those contracts change.
---

# MCP identity and runtime provenance

This is diagnostic guidance, not a registration or server-management API.

## Identity projection

`mcp(action="info", input={}, reasoning="inspect MCP identity")` reads the
addon-published cache `system/mcp_identities/<name>.json` (schema
`lingtai.mcp.identity.v1`); it makes no network request. It attaches an
`identity` block only to a matching registry record whose `accounts` list is
non-empty. The projection allowlists account alias, provider username/ID or
display name, bot marker, and non-secret routing counts. It drops tokens,
passwords, app/refresh/access secrets, headers, and unknown fields. Prompt
`<registered_mcp>` narrows it again and removes volatile `last_verified_at`.
Missing, unrelated, or empty identity files produce no identity. Use the addon's
`accounts` action for richer detail—never private config.

## Installed manual and public paths

The reserved `manual` action reads exactly the installed
`.library/intrinsic/capabilities/mcp/SKILL.md` below the agent workdir and does
no registry/identity I/O or mutation. Its flat public result is:

```json
{"status":"ok","mcp_manual":"<full body>","manual_path":"<host-local path>"}
```

When missing, it returns `status: "degraded"`, an empty body, the truthful path,
and an error; it never falls back to another manual. `manual_path` and
`registry_path` are diagnostic host-local values, never config inputs or proof
that a child is active. Do not publish private absolute paths.

## Who owns a child's launch

| Route | Effective owner |
|---|---|
| Curated addon in main-agent `init.json` (`source == "lingtai-curated"`) | Running kernel `mcp_catalog.json`: Agent `sys.executable`, catalog module args, and Agent source-root `PYTHONPATH`; only non-launch account `env` passes through. |
| Non-curated `init.json` entry | That entry's effective `type`/`command`/`args`/`env`. |
| Legacy `mcp/servers.json` | Its direct activation spec. |
| Daemon task/plugin MCP | That task/plugin's config, never the main registry. |
| HTTP | No local subprocess or interpreter. |

For curated entries, legacy `type`/`command`/`args`/`env.PYTHONPATH` are accepted
for compatibility, ignored for launch with one bounded warning, and never used as
a fallback if the catalog lacks a safe stdio launcher. Never assume a
third-party, legacy, daemon, or plugin child shares the Agent's interpreter,
site-packages, source root, or environment.

## Authorized venv swap and registry truth

A venv swap does not reconcile `mcp_registry.jsonl` automatically. Rewriting its
curated command is optional metadata reconciliation, not required for curated
main-agent spawning (the running kernel owns that launcher). Only after
inspecting MCP `info` and obtaining explicit registry-write
authorization, run the operation under the **new** absolute interpreter with an
absolute agent directory. Use this fail-closed, atomic, order-preserving,
non-appending procedure:

1. Confirm the registry exists and call the shared `read_registry(agent_dir)`.
   If it reports any invalid or duplicate line, report only its line number
   and a sanitized reason; never print the `raw` field or whole problems list.
   Abort before writing. Error strings may also quote unsafe input.
2. Preserve every line and its ending. For each first-seen record whose
   `source` is exactly `lingtai-curated`, set only `command` to the new process's
   `sys.executable`; do not append records or alter independent-interpreter
   overrides. If nothing changed, write nothing.
3. If changed, create a temporary file in the registry's directory, write the
   preserved lines with `ensure_ascii=False`, flush and `fsync`, copy the old
   mode, then `os.replace` it. On any error, remove only the temporary file and
   re-raise. Before replacement the original remains unchanged; after replacement,
   an error does not prove rollback. Preserve evidence and inspect the outcome
   before retrying, rather than promising the old file is still authoritative.

This rewrites durable registry truth only. It is never the spawn source for
non-curated, legacy, daemon, or plugin children, and has no effect on HTTP. A
curated main-agent child still derives launcher, args, and `PYTHONPATH` from the
running kernel catalog/runtime. A changed curated runtime must be verified after the Agent relaunch.
The internal `_retry_failed_mcps` hook skips healthy clients and uses captured
launch specs. The public System refresh invokes that hook **then requests a
full Agent relaunch**; it does not promise healthy children survive unchanged.
A refresh receipt is handoff intent, not proof the new process/child is ready.

## Fail-closed provenance check

After a venv or source change, probe the child's **effective** command and
environment and compare all three paths:

- `sys.executable`;
- `lingtai.__file__`; and
- `lingtai.mcp_servers.<name>.__file__`.

For curated main-agent MCPs, effective means the Agent's own interpreter,
catalog-derived module args, and current source root; never use launcher strings
stored in `init.json` or the registry. For non-curated/legacy use that entry's
command and env; for a daemon use its task/plugin config. If any path still
resolves the old venv/source, report **requires relaunch** and do not claim the
change is live. Process inspection such as `lsof` is only corroboration.
