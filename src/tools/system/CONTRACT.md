---
name: system-contract
tool: system
contract_version: 1
related_files:
  - src/tools/system/__init__.py
  - src/tools/system/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# System capability contract

`system` is the runtime, lifecycle, and context-hygiene tool: refresh/preset
swaps, self-sleep, karma-gated control of *other* agents, and agent-authored
context summarization. It does **not** own any notification verb — those live on
the standalone `notification` tool. The implementation lives in
`src/tools/system/`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing runtime lifecycle: `refresh`/preset swap, `sleep`, or the
  karma-gated verbs (`lull`/`suspend`/`cpr`/`interrupt`/`clear`/`nirvana`).
- You are reviewing `summarize` (agent-authored context compaction + rebuild).
- You are reviewing preset listing/connectivity or the karma/nirvana authz gate.

**Do not use this for:**
- Notification reads/dismissals: use the `notification` tool
  (`src/tools/notification/CONTRACT.md`). `system` exposes no `notification`/
  `dismiss` alias; those actions are rejected as unknown.
- Context molt (shedding history): that is `psyche(object='context',
  action='molt')` (`src/tools/psyche/CONTRACT.md`). `system` only *requests* a
  provider-context rebuild via `summarize(rebuild=true)`.
- Code navigation only: read `src/tools/system/ANATOMY.md`.

**Fast paths:** action list -> §Tool surface; karma signal files -> §State &
storage; summarize semantics -> §Anchored claims.

## Scope

- Canonical tool name: `system`.
- `summarize` stays on `system` (context hygiene, not a notification verb).
- Non-goals: notification `check`/`dismiss_*`, context molt, mailbox actions.

## Tool surface

Schema (`src/tools/system/schema.py`) requires `action` from a fixed enum.
Dispatch is the `handle()` table in `src/tools/system/__init__.py`.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `refresh` | — | `reason`, `preset`, `revert_preset` | `{status: "ok", message}` | `{status: "error", message}` on preset/revert conflict, unauthorized preset, oversize context, or activation failure |
| `sleep` | — | `reason`, `force` | `{status: "ok", message}` (self-sleep; refuses with an ok+message when notifications pending and not `force`) | — |
| `lull` | `address` | `reason` | `{status: "asleep", address}` | `{error: True, message}` (no karma, no/invalid address, self-target, target not running) |
| `suspend` | `address` | `reason` | `{status: "suspended", address}` | `{error: True, message}` (as above) |
| `cpr` | `address` | `reason` | `{status: "resuscitated", address}` | `{error: True, message}` (target already running, CPR unsupported/failed) |
| `interrupt` | `address` | `reason` | `{status: "interrupted", address}` | `{error: True, message}` (as above) |
| `clear` | `address` | `reason` | `{status: "cleared", address, source}` | `{error: True, message}` (as above) |
| `nirvana` | `address` | `reason` | `{status: "nirvana", address}` | `{error: True, message}` (requires karma AND nirvana; shutdown-timeout error) |
| `presets` | — | — | `{status: "ok", active, available: [...]}` | `{status: "error", message}` on unreadable init.json |
| `summarize` | `items` (list of `{tool_call_id, summary}`) *or* `rebuild=true` | `rebuild` (bool) | `{status: "ok"/"partial", mode: "summarize"/"rebuild", summarized, failed, items, context, ...}` | `{status: "error", reason: "missing_items"/"runtime_threshold_change_not_supported"/"no_chat_session"}`; per-item errors (`not_found`, `already_summarized`, `missing_tool_call_id`, `missing_summary`) |

Unknown/absent `action` returns `{status: "error", message: "Unknown system
action: ..."}`. Notification verbs (`check`, `dismiss_channel`,
`dismiss_event`, `dismiss_ref`) and the legacy `notification`/`dismiss` aliases
are **not** in the enum and dispatch to the unknown-action error.

The karma gate (`_check_karma_gate`) requires `admin.karma=True` for the
five control verbs and `admin.karma AND admin.nirvana` for `nirvana`; it also
rejects a missing address, a self-target, and a non-agent target.

## State & storage

Karma verbs write **signal files** into the *target* agent's working directory;
the target's heartbeat loop picks them up. Paths are relative to the resolved
target `working_dir`:

```text
<target>/.sleep      — written by lull (agent goes ASLEEP; process keeps running)
<target>/.suspend    — written by suspend and by nirvana (process shuts down)
<target>/.interrupt  — written by interrupt (cancels the current turn)
<target>/.clear      — written by clear; its contents become the recovery `source`
```

`nirvana` writes `.suspend`, waits up to ~10s for shutdown, then
`shutil.rmtree`s the whole target directory. `refresh`/preset swaps persist
`manifest.preset.default` into the agent's own `<workdir>/init.json`.
`summarize` mutates live chat history in place (replacing tool-result block
content with a `lingtai_agent_summarized_result` marker), persists via
`_save_chat_history`, and the original payload stays traceable in
`<workdir>/logs/events.jsonl` by `tool_call_id`.

## Cross-platform invariants

- Address resolution uses `resolve_address` and all target file access is via
  `pathlib.Path.write_text` / `shutil.rmtree`; no shell-outs. DOCUMENT.
- Karma signal files are empty markers (except `.clear`, whose content is the
  `source` string), read/written UTF-8. DOCUMENT (do not change).
- `summarize` does no filesystem or subprocess work of its own beyond the
  history save; it operates on in-memory `ChatInterface` blocks. DOCUMENT: no
  platform-specific behavior; all file access via pathlib.
- No subprocess/PTY in this tool. DOCUMENT.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `system` is a wired intrinsic | `src/tools/system/__init__.py` | `tests/test_system.py::test_system_in_all_intrinsics`, `tests/test_system.py::test_system_wired_in_agent` |
| `sleep` transitions the agent to ASLEEP (self, no karma) | `src/tools/system/karma.py:_sleep` | `tests/test_system.py::test_system_self_sleep` |
| Unknown/legacy actions return the unknown-action error | `src/tools/system/__init__.py:handle` | `tests/test_system.py::test_system_unknown_action`, `tests/test_system.py::test_system_show_action_rejected` |
| `refresh` with an unauthorized preset is refused | `src/tools/system/preset.py:_refresh` | `tests/test_system.py::test_refresh_with_unauthorized_preset_returns_error` |
| `refresh` cannot combine `preset` and `revert_preset` | `src/tools/system/preset.py:_refresh` | `tests/test_system.py::test_refresh_revert_preset_with_preset_arg_errors` |
| `presets` lists the allowed library and strips credentials | `src/tools/system/preset.py:_presets` | `tests/test_system.py::test_presets_action_lists_full_library`, `tests/test_system.py::test_presets_action_strips_credentials` |
| `cpr` propagates launch failure instead of reporting success | `src/tools/system/karma.py:_cpr` | `tests/test_system.py::test_cpr_propagates_launch_failure_instead_of_resuscitated` |
| `summarize` records a `pending` marker and can rebuild the provider context | `src/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_summarize_writes_pending_status_marker`, `tests/test_system_summarize.py::test_rebuild_true_with_items_records_marks_done_then_rebuilds` |
| `summarize` with no items and `rebuild=false` is an invalid no-op | `src/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_missing_items_without_rebuild_is_invalid_no_op` |
| Runtime threshold mutation via `summarize` is rejected | `src/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_summarize_runtime_threshold_change_rejected` |
| Notification/dismiss actions are dropped from the `system` schema | `src/tools/system/schema.py` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss`, `tests/test_notification_tool.py::test_system_rejects_dismiss_action` |
| Karma signal files clear a target channel path end-to-end | `src/tools/system/karma.py` | `tests/test_system_dismiss.py` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Karma gate blocks unauthorized control | `tests/test_system.py::test_refresh_with_unauthorized_preset_returns_error` + karma gate paths | Call `lull` without `admin.karma` | Any agent could sleep/destroy peers |
| `nirvana` requires karma AND nirvana | karma gate in `src/tools/system/karma.py:_check_karma_gate` | Call `nirvana` with only karma | Irreversible deletion by under-privileged agent |
| `summarize` preserves the original in events.jsonl | `tests/test_system_summarize.py::test_summarize_replaces_block_content` | Summarize a result, grep events.jsonl by tool_call_id | Loss of original tool output |
| `summarize` rebuild flips pending markers done | `tests/test_system_summarize.py::test_rebuild_true_with_items_records_marks_done_then_rebuilds` | Summarize then rebuild; inspect marker status | Pending compaction never applied |
| No notification verbs on `system` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss` | Call `system(action='check')` | Duplicate notification surfaces diverge |

Run before merging system changes:

```bash
python -m pytest tests/test_system.py tests/test_system_summarize.py tests/test_system_dismiss.py tests/test_notification_tool.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m tools.glossary_validator --check`.
