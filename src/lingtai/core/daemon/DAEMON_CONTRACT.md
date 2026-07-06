---
name: daemon-contract
description: >
  Contract for daemon task delegation, selected skill context, one-run MCP
  registration propagation, completion signaling, and run artifacts across
  LingTai and CLI backends.
status: active
contract_version: 1
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
  daemon task input shape, selected skills catalog/path semantics, MCP
  registration redaction or native mounting, daemon_common completion
  enforcement, backend routing, or run artifact shape, re-read this contract in
  the same change and either update it or explicitly state in the PR why the
  daemon contract still holds.
---
# Daemon Contract

> **Maintenance trigger:** any change to a path listed in `review_triggers` must
> re-check this contract in the same change. The PR should either update this
> document or say why the daemon contract still holds.

## What this is

This document is the cross-backend contract for daemon task delegation and the
model/tool-visible runtime context that a daemon run receives. It is
anatomy-style: it names the input shape, source-cites the code that owns the
surface, separates prompt-visible catalog context from mounted tools, and gives
reviewers an acceptance gate for daemon changes.

Scope:

- `daemon(action="emanate")` task objects and backend/preset routing.
- Parent-selected `skills` catalog/path context.
- Parent-provided one-run `mcp` registrations, prompt redaction, and native MCP
  config boundaries.
- The built-in `daemon_common` MCP completion contract.
- Prompt, `daemon.json`, native config, event, heartbeat, result, and error
  artifacts.

Non-scope: adding new backend MCP support, changing MCP server protocols,
changing daemon scheduling/timeout policy except where it affects this contract,
or redefining individual third-party CLI behavior beyond LingTai's wrapper.

## Components

1. **Task input object.** Each task carries `task`, `tools`, optional
   `system_prompt`, `skills`, `mcp`, `backend_options`, and optional preset
   routing inherited from the outer `daemon` call. The public schema exposes
   these fields and backend choices (`src/lingtai/core/daemon/__init__.py:628-669`).
   `system_prompt` is a one-run behavior contract, not a lifecycle override
   (`src/lingtai/core/daemon/__init__.py:1418-1437`).
2. **Selected skills catalog/path context.** `skills` is an array of skill
   directory paths or direct `SKILL.md` paths. The runtime resolves each path,
   parses only frontmatter, and renders a compact selected skills catalog with
   `name`, `location`, and `description`; the SKILL.md body is not pasted
   (`src/lingtai/core/daemon/__init__.py:1000-1058`,
   `src/lingtai/core/daemon/__init__.py:1380-1397`). The backend obligation is
   that the final prompt/context tells the daemon which skills exist and where
   to read them.
3. **One-run MCP registrations.** `mcp` is an array of full MCP registration
   objects. Stdio registrations require `command`, optional `args`, and optional
   string `env`; HTTP registrations require `url` and optional string
   `headers`. The normalized prompt catalog redacts secret `env` and `headers`
   values while leaving keys, names, transports, and non-secret shape visible
   (`src/lingtai/core/daemon/__init__.py:1061-1148`).
4. **Native MCP mounting.** The LingTai backend starts task MCP clients and
   exposes their tools only for that run (`src/lingtai/core/daemon/__init__.py:1315-1367`).
   CLI backends must mount MCP natively where the backend has a verified
   run-scoped MCP config path; prompt catalog alone is not enough when native
   support exists. Current native stdio helpers cover Claude print-mode config,
   Codex `-c mcp_servers.*`, OpenCode `OPENCODE_CONFIG_CONTENT`, and Qwen
   settings (`src/lingtai/core/daemon/__init__.py:173-218`,
   `src/lingtai/core/daemon/__init__.py:1204-1222`,
   `src/lingtai/core/daemon/__init__.py:3096-3117`). Native helpers skip HTTP
   registrations today, so HTTP remains prompt catalog context until a
   backend-specific HTTP config path is implemented.
5. **daemon_common completion MCP.** MCP-capable backends receive the built-in
   `daemon_common` registration before parent registrations
   (`src/lingtai/core/daemon/__init__.py:1151-1202`). Its `finish` tool writes
   `daemon_completion.json` with status `done`, `failed`, or `incomplete`
   (`src/lingtai/mcp_servers/daemon_common/server.py:20-139`). A run with
   `daemon_common` loaded may mark success only after validated
   `finish(status="done")`; missing, invalid, failed, or incomplete completion
   is a contract failure (`src/lingtai/core/daemon/__init__.py:1224-1313`).
6. **Run artifacts.** `DaemonRunDir` creates the per-run folder, writes
   `daemon.json`, `.prompt`, `.heartbeat`, `history/chat_history.jsonl`,
   `logs/events.jsonl`, `logs/token_ledger.jsonl`, `result.txt`, and
   `artifacts.json` through the daemon filesystem boundary
   (`src/lingtai/core/daemon/run_dir.py:41-115`). `daemon.json.call_parameters`
   records the visible task surface with redacted MCP registrations, while
   native config files/env/argv/settings use full secret-bearing values only
   inside backend-owned run-scoped launch plumbing
   (`src/lingtai/core/daemon/__init__.py:3040-3134`).

## Connections

The flow is:

```text
parent daemon call
  -> task validation and backend/preset routing
  -> selected skills catalog/path + redacted MCP prompt catalog
  -> final prompt/context (.prompt)
  -> LingTai task MCP clients OR CLI native MCP config when supported
  -> daemon_common finish signal when loaded
  -> daemon.json / events / heartbeat / result / artifacts
  -> terminal notification and daemon(check/list) inspection
```

The prompt-visible and tool-visible lanes have different jobs:

| Lane | Job | Must not become |
|---|---|---|
| `.prompt` / final prompt/context | Model-visible task, selected skills catalog/path, redacted MCP registrations, and daemon_common instructions. | A place to paste SKILL.md bodies or secret values. |
| `daemon.json.call_parameters` | Durable audit of user-visible inputs: task, tools, skills, redacted MCP, system prompt, backend options. | A secret-bearing native backend config. |
| Native MCP config/env/argv/settings | Backend-specific mounting for MCP tools when the CLI has a verified run-scoped loader. | A claim of support for prompt-catalog-only unsupported backends. |
| `daemon_common` completion file | Internal success gate written by the MCP `finish` tool. | A conversational final answer substitute. |
| `events.jsonl` / heartbeat / result artifacts | Progress, forensic trace, and full result storage. | A second source of truth for task inputs. |

## Contract rules

### 1. Task fields stay distinct

`task` says what to do. `system_prompt` says how this daemon should behave for
one run. `tools` is the LingTai backend technical surface; CLI backends ignore
the LingTai tool list because the external CLI owns its own tool mode. `skills`
is progressive-disclosure context. `mcp` is one-run tool registration context.
`backend_options` is CLI-backend-only argv passthrough, validated before any run
directory is created (`src/lingtai/core/daemon/__init__.py:2977-3009`).

### 2. Skills are catalog entries, not pasted manuals

Daemon `skills` entries are selected skills catalog/path records: name,
location, and description. The daemon must read the pointed `SKILL.md` itself
when relevant. This is distinct from MCP because skills are prompt guidance and
workflow discovery, while MCP tools need runtime mounting.

### 3. MCP registrations are redacted in prompts and mounted only when verified

Parent task MCP registrations are normalized and prompt-rendered with env/header
redaction. The LingTai backend starts them as actual task-scoped MCP tools.
CLI backends with verified native stdio support receive native MCP config for
`daemon_common` plus parent stdio registrations. Backends without verified
native mounting remain prompt-catalog-only unsupported backends and must not
silently claim tool availability. HTTP MCP support is prompt-only today; follow-up support must be
backend-specific, source-evidenced, and tested before the contract claims native
HTTP mounting.

### 4. daemon_common is the terminal-success gate where loaded

CLI backends with native MCP support receive `daemon_common` so they can call
`finish(status=...)`; the LingTai backend receives the same completion MCP as a
task-scoped MCP. A normal final answer is not enough when `daemon_common` is
present. Success requires `finish(status="done")` where enforced.

### 5. Backend support status must be honest

Current implementation status:

- LingTai backend: parent MCP registrations are actual task MCP tools.
- Claude print backends (`claude-p` / `claude-code`): native stdio MCP config
  through a per-run `--mcp-config` file.
- Codex: native stdio MCP behavior through `-c mcp_servers.<name>.*` overrides.
- OpenCode: native stdio MCP behavior through `OPENCODE_CONFIG_CONTENT`
  (MiMo Code is OpenCode-family but is not included in this wiring yet; see
  below).
- Qwen Code: native stdio MCP behavior through a per-run Qwen settings file.
- MiMo Code: feasible native path via the OpenCode-family runner, but daemon
  wiring is not implemented in this PR.
- Kimi Code: only an `acp` entrypoint is documented locally; no verified
  arbitrary-MCP-loading flag is wired, so it ships no-MCP for now.
- Cursor: general MCP docs exist, but daemon-safe per-run CLI mounting needs an
  unlocked CLI proof before implementation.
- Oh-My-Pi: not verified as daemon-native MCP support.
- HTTP registrations: prompt-only today; follow-up support must be
  backend-specific and tested.

## Acceptance Gate

Any new daemon backend or contract-impacting daemon change must prove all
applicable items:

1. Selected skills catalog/path context is visible in the final prompt/context
   without pasting SKILL.md bodies.
2. MCP prompt catalog exists for parent registrations and prompt redaction keeps
   secret env/header values out of prompts, `daemon.json`, and reports.
3. Native MCP config includes parent MCPs when the backend supports run-scoped
   native mounting; unsupported transports are omitted from native config
   rather than malformed.
4. Unsupported backends fail honestly or remain documented prompt-catalog-only
   unsupported backends.
5. `daemon_common` completion is available and enforced where expected.
6. `.prompt`, `daemon.json`, native config files/env/argv/settings,
   `result.txt`, `events.jsonl`, and heartbeat/artifact files remain inspectable
   and bounded to the daemon run directory.

## Review Triggers

Re-check this contract when touching:

- `src/lingtai/core/daemon/__init__.py` task validation, prompt assembly, MCP
  registration handling, backend routing, native config writers, or completion
  enforcement.
- `src/lingtai/core/daemon/run_dir.py` artifact paths, `daemon.json`
  `call_parameters`, redaction-sensitive fields, terminal markers, or manifests.
- `src/lingtai/core/daemon/manual/` daemon argument semantics, backend status,
  or MCP/completion guidance.
- `src/lingtai/mcp_servers/daemon_common/` finish schema, payload file, or
  server behavior.
- `tests/test_daemon*.py` coverage that proves backend options, CLI native MCP,
  daemon_common completion, OpenCode-family routing, Qwen settings, Claude print
  MCP config, run-dir artifacts, or prompt redaction.
