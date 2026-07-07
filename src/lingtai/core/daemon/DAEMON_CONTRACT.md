---
name: daemon-architecture-capability-contract
description: >
  Architecture capability contract for daemon backends: selected skills
  progressive-disclosure context, one-run MCP registration propagation,
  daemon_common completion signaling, support-status honesty, and redacted run
  artifacts across LingTai and CLI daemon architectures.
status: active
contract_version: 2
last_changed_at: "2026-07-06"
related_files:
  - src/lingtai/core/daemon/ANATOMY.md
  - src/lingtai/core/daemon/__init__.py
  - src/lingtai/core/daemon/run_dir.py
  - src/lingtai/core/daemon/manual/SKILL.md
  - src/lingtai/core/daemon/manual/reference/cli-backends/SKILL.md
  - src/lingtai/mcp_servers/daemon_common/server.py
  - tests/test_daemon_contract_doc.py
  - tests/test_daemon.py
  - tests/test_daemon_backend_options.py
  - tests/test_daemon_claude_p_background_guard.py
  - tests/test_daemon_opencode_backend.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_daemon_run_dir.py
review_triggers:
  - src/lingtai/core/daemon/__init__.py
  - src/lingtai/core/daemon/run_dir.py
  - src/lingtai/core/daemon/ANATOMY.md
  - src/lingtai/core/daemon/manual/
  - src/lingtai/mcp_servers/daemon_common/
  - tests/test_daemon.py
  - tests/test_daemon_backend_options.py
  - tests/test_daemon_claude_p_background_guard.py
  - tests/test_daemon_opencode_backend.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_daemon_run_dir.py
maintenance: |
  Keep this file in the same maintenance graph as the daemon ANATOMY.md and
  manual files listed under related_files. If any review_triggers path changes
  daemon backend routing, selected skills catalog/path semantics, MCP
  registration redaction or native mounting, daemon_common completion
  enforcement, backend support status, or run artifact shape, re-read this
  architecture capability contract in the same change and either update it or
  explicitly state in the PR why the daemon capability contract still holds.
---
# Daemon Architecture Capability Contract

> **Maintenance trigger:** any change to a path listed in `review_triggers` must
> re-check this contract in the same change. The PR should either update this
> document or say why the daemon architecture capability contract still holds.

## What this is

This document is the architecture capability invariant for every daemon backend
and backend family LingTai exposes. It is not primarily a per-task input
contract. The durable requirement is that any daemon architecture preserves the
same selected-skill discovery semantics, one-run MCP registration semantics,
completion signaling, backend support honesty, and reviewable artifact boundary.

The public daemon task object is still the carrier for `skills`, `mcp`,
`system_prompt`, `tools`, `backend_options`, and backend routing
(`src/lingtai/core/daemon/__init__.py:628-669`). This contract governs what
each backend must do with those capabilities after routing.

Scope:

- Selected `skills` progressive-disclosure catalog and path semantics.
- Parent-provided one-run MCP registrations: prompt catalog, redaction, and
  native mounting where the backend has a source-proven run-scoped loader.
- Built-in LingTai daemon MCP (`daemon_common`) availability for MCP-capable
  daemon backends and its `finish(status, summary?, reason?, artifacts?)`
  terminal signal.
- Backend support matrix and acceptance expectations when adding or changing a
  backend.
- Prompt/native-config/artifact redaction boundary sufficient for review without
  leaking secrets.

Non-scope: claiming new backend MCP support before implementation, changing
third-party MCP protocols, or broad daemon scheduling/timeout behavior except
where those changes affect the capability invariants here.

## Capability Invariants

### 1. Selected skills are progressive-disclosure catalog entries

Every daemon backend must preserve selected `skills` as discoverable workflow
context, not as copied skill bodies. The runtime resolves each supplied skill
directory or direct `SKILL.md` path, parses frontmatter, and renders only
`name`, `location`, and `description` into the daemon context
(`src/lingtai/core/daemon/__init__.py:998-1058`). The model reads the referenced
`SKILL.md` only when relevant. A backend must not paste full SKILL bodies into
the prompt or hide the path needed for progressive disclosure.

### 2. Parent-provided MCP registrations have two lanes

The prompt lane is universal: parent-provided MCP registrations are normalized
as one-run registration objects, rendered as a prompt-visible catalog, and
redacted for `env` and `headers` values while preserving names, transports,
keys, and non-secret shape (`src/lingtai/core/daemon/__init__.py:1061-1148`).

The native lane is backend-specific: a backend may mount those MCP registrations
as actual tools only when its daemon runner has a verified run-scoped native MCP
config or client path. The LingTai backend starts task-scoped MCP clients directly
(`src/lingtai/core/daemon/__init__.py:1315-1367`). CLI backends must not claim
native MCP availability from the prompt catalog alone.

### 3. daemon_common is the completion capability for MCP-capable backends

MCP-capable daemon backends receive the built-in `daemon_common` MCP before any
parent registrations (`src/lingtai/core/daemon/__init__.py:1155-1180`). The
oneshot context tells the model to call `finish` exactly once with `done`,
`failed`, or `incomplete` (`src/lingtai/core/daemon/__init__.py:1183-1202`).
The MCP server writes `daemon_completion.json` with
`status`, optional `summary`, optional `reason` (required by validation when
`status` is `failed` or `incomplete`), and optional `artifacts`
(`src/lingtai/mcp_servers/daemon_common/server.py:20-139`).

When `daemon_common` is loaded, a conversational final answer is not enough.
Success requires a validated `finish(status="done")`; missing completion,
invalid JSON, invalid status, run-id mismatch, `failed`, or `incomplete` must
prevent terminal `done` (`src/lingtai/core/daemon/__init__.py:1224-1313`).

### 4. Artifacts separate review evidence from secret-bearing config

Run artifacts must make the daemon contract reviewable without leaking secrets.
`DaemonRunDir` owns the run folder and persistent artifact set
(`src/lingtai/core/daemon/run_dir.py`): its constructor creates the run
folder/history/log directories and state object (`src/lingtai/core/daemon/run_dir.py:41-120`),
while methods in the same module persist `daemon.json`, `.prompt`, `.heartbeat`,
`history/chat_history.jsonl`, `logs/events.jsonl`, `logs/token_ledger.jsonl`,
`result.txt`, and `artifacts.json`.
`daemon.json.call_parameters` and `.prompt` may contain task surface,
selected-skill catalog/path context, and redacted MCP registrations. Secret
MCP values belong only in native run-scoped launch plumbing where a backend
needs them to mount tools (`src/lingtai/core/daemon/__init__.py:3040-3134`).

### 5. Unsupported support status is an explicit capability state

An unsupported backend or transport must stay honest: prompt-catalog-only is not
native tool availability, and unsupported native MCP paths must be omitted or
reported explicitly rather than malformed into a fake-success launch. HTTP MCP
registrations are accepted for the prompt catalog today, but native HTTP
mounting is not claimed for CLI backends until a backend-specific source-proven
path is implemented and tested.

## Backend Support Matrix

Current source-backed status:

| Backend / architecture | Selected skills catalog/path | Parent MCP native mounting | `daemon_common` native completion |
|---|---|---|---|
| `lingtai` | Yes, in the daemon prompt/context. | Yes, task-scoped stdio and HTTP MCP clients. | Yes, task-scoped MCP; `finish(done)` is enforced. |
| `claude-p` / `claude-code` | Yes. | Yes for stdio via per-run `--mcp-config`; HTTP omitted. | Yes, same per-run config. |
| `codex` | Yes. | Yes for stdio via `-c mcp_servers.*`; HTTP omitted. | Yes, same config override path. |
| `opencode` | Yes. | Yes for stdio via `OPENCODE_CONFIG_CONTENT`; HTTP omitted. | Yes, same per-process config content. |
| `qwen-code` / `qwen` | Yes. | Yes for stdio via per-run Qwen settings; HTTP omitted. | Yes, same settings file. |
| `mimocode` / `mimo` | Yes. | Not wired in this slice; prompt catalog only. | Not wired; do not claim MCP-capable completion. |
| `oh-my-pi` / `omp` | Yes. | Not verified; prompt catalog only. | Not wired; do not claim MCP-capable completion. |
| `kimicode` / `kimi` | Yes. | Not verified; prompt catalog only. | Not wired; do not claim MCP-capable completion. |
| `cursor` | Yes. | Not verified; prompt catalog only. | Not wired; do not claim MCP-capable completion. |

The native stdio helper set is source-owned by `_codex_mcp_argv`,
`_opencode_mcp_env`, `_write_qwen_mcp_settings`, `_write_claude_mcp_config`,
and `_cli_backend_loads_common_mcp`
(`src/lingtai/core/daemon/__init__.py:173-218`,
`src/lingtai/core/daemon/__init__.py:1204-1222`,
`src/lingtai/core/daemon/__init__.py:3096-3117`). If a backend is not in that
loaded set, this contract treats it as prompt-catalog-only until code and tests
prove otherwise.

## Acceptance Gate

Any new daemon backend, backend-family reuse, or contract-impacting daemon
change must prove all applicable items:

1. Selected skills catalog/path context is visible in the final prompt/context
   without pasting SKILL.md bodies.
2. Parent MCP registrations appear in prompt context and durable call
   parameters with `env` and `headers` values redacted.
3. Native MCP config includes parent registrations only for transports and
   backends with a verified run-scoped loader; unsupported transports are
   omitted or reported honestly.
4. `daemon_common` is available for MCP-capable daemon backends, and terminal
   success is gated by valid `finish(status="done")`.
5. Unsupported backends remain documented as prompt-catalog-only or fail
   explicitly; they must not imply tool availability from prompt text alone.
6. `.prompt`, `daemon.json`, native config files/env/argv/settings,
   `result.txt`, `events.jsonl`, heartbeat, and artifact manifests remain
   inspectable within the daemon run boundary while secret-bearing native config
   is not copied into review artifacts.

## Review Triggers

Re-check this contract when touching:

- `src/lingtai/core/daemon/__init__.py` backend routing, selected-skill catalog
  assembly, MCP registration handling, native config writers, or completion
  enforcement.
- `src/lingtai/core/daemon/run_dir.py` artifact paths, `daemon.json`
  `call_parameters`, redaction-sensitive fields, terminal markers, or manifests.
- `src/lingtai/core/daemon/manual/` daemon argument semantics, backend status,
  MCP capability guidance, or completion guidance.
- `src/lingtai/mcp_servers/daemon_common/` finish schema, payload file, or
  server behavior.
- `tests/test_daemon*.py` coverage that proves backend options, CLI native MCP,
  daemon_common completion, OpenCode-family routing, Qwen settings, Claude print
  MCP config, run-dir artifacts, prompt redaction, or selected-skill catalog
  preservation.
