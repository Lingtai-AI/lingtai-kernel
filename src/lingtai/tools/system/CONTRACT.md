---
name: system-contract
tool: system
contract_version: 3
related_files:
  - src/lingtai/tools/system/__init__.py
  - src/lingtai/tools/system/schema.py
  - src/lingtai/tools/system/name.py
  - src/lingtai/tools/system/summarize.py
  - src/lingtai/tools/system/ANATOMY.md
  - src/lingtai/tools/system/BEHAVIORS.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - tests/test_tool_family_system_migration.py
maintenance: |
  Keep related_files as repo-relative paths to real files, including the
  paired ANATOMY.md, the LTP/ToolFamily contracts this family is governed by,
  the system-manual this capability is taught by, and the migration evidence
  suite. If behavior and this contract disagree, the code is the source of
  truth — fix the contract in the same change and bump contract_version on
  breaking contract edits.
  contract_version 3 is the breaking public-ownership change: the public
  summarize action left for context (which split it into record-only summarize and full reconstruction rebuild)
  and the two name actions arrived here from the dissolved psyche family.
  summarize.py stays here as a private engine only — keep the context Contract
  link current, since that family owns the public actions driving it.
---

# System capability contract

`system` is the runtime, lifecycle, and identity tool: refresh/preset swaps,
self-sleep, karma-gated control of *other* agents, preset listing, and the
agent's true name/nickname. It does **not** own any notification verb — those
live on the standalone `notification` tool — and it no longer owns any public
context-hygiene action: molt, tool-result summarization, and the
full prompt reconstruction, summary application, and provider replay all belong to `context`
(`src/lingtai/tools/context/CONTRACT.md`). The implementation lives in
`src/lingtai/tools/system/`; the code is the source of truth.

One internal dependency crosses that public boundary and is deliberate:
`src/lingtai/tools/system/summarize.py` remains here as the **private engine**
for tool-result summarization. `context(action='summarize')` and
`context(action='rebuild')` import and call it, and the kernel's forced-rebuild
path imports `SUMMARIZE_MARKER`/`mark_pending_summaries_done` from it. That is
an internal runtime interface only — it is never reachable as a `system`
action, and `system(action='summarize')` fails loudly as an unknown action.

`system` is an **LTP v2 family** (`src/lingtai/tools/CONTRACT.md`): its final
model-facing root is exactly `action`, `input`, `reasoning`, and `summarize`
with `additionalProperties: false`, and each action's arguments live only in
that action's own strict `input` object — so `address` belongs to the six
address verbs, `preset`/`revert_preset` only to `refresh`, and `content` only
to the two name actions. It is the third migrated *intrinsic* (after `soul` and
`notification`) and therefore composes its dispatching family per call rather
than owning a per-Agent manager, and drops the kernel-injected `_tc_id` at its
own Host boundary. The migration changed the argument shape only: the public
tool name, every retained action value, every privilege gate, receipt, and error
are unchanged, and the children consume no additional model tool slots.
`system` is on the kernel's `_LTP_V2_MIGRATED_FAMILIES` allowlist
(`src/lingtai/kernel/tool_result_summary.py`), so the root `summarize` boolean
it advertises is actually honored.

## Routing Card

**Use this when:**
- You are editing runtime lifecycle: `refresh`/preset swap, `sleep`, or the
  karma-gated verbs (`lull`/`suspend`/`cpr`/`interrupt`/`clear`/`nirvana`).
- You are editing the agent's name: `name_set` (once, immutable) or
  `name_nickname` (mutable). Neither renames the agent's address or working
  directory — that is the operator migration workflow in `system-manual`.
- You are reviewing preset listing/connectivity or the karma/nirvana authz gate.

**Do not use this for:**
- Notification reads/dismissals: use the `notification` tool
  (`src/lingtai/tools/notification/CONTRACT.md`). `system` exposes no `notification`/
  `dismiss` alias; those actions are rejected as unknown.
- Context lifecycle and hygiene: molt, tool-result summarization, and the
  full prompt reconstruction, summary application, and provider replay all belong to `context`
  (`src/lingtai/tools/context/CONTRACT.md`) — `context(action='molt')`,
  `context(action='summarize')`, `context(action='rebuild')`. `system` owns
  none of them and exposes no `summarize` action or alias; `summarize.py`
  remains here only as the private engine those actions call.
- Code navigation only: read `src/lingtai/tools/system/ANATOMY.md`.

**Fast paths:** action list -> §Tool surface; karma signal files -> §State &
storage; name semantics -> §Anchored claims.

## Scope

- Canonical tool name: `system`.
- `name_set`/`name_nickname` live on `system` (runtime identity state). They
  update live in-memory identity, persist `.agent.json`, and rewrite the
  protected prompt `identity` section. They are NOT raw init/config editing and
  NOT the physical address/workdir rename.
- Non-goals: notification `check`/`dismiss_*`, context molt/summarize/rebuild
  (all on `context`), physical address/workdir rename, mailbox actions.

## Tool surface

Per-action `input` schemas are the data in `src/lingtai/tools/system/schema.py`
(`ACTION_ORDER`, `INPUT_SCHEMAS`); the model-facing family schema is composed
from them by `get_schema()` in `src/lingtai/tools/system/__init__.py`, and
dispatch is the generic `ToolFamily.handle()` that same module delegates to.
Every call takes `action` + `input` + `reasoning`; the "inputs" columns below
name properties of the selected action's own `input` object, never root
siblings.

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
| `name_set` | `content` | — | `{status: "ok", name}` | `{error}` when empty or when a true name is already set (immutable) |
| `name_nickname` | `content` | — | `{status: "ok", nickname}` (`null` when cleared) | — (empty `content` clears the nickname) |

`manual` takes the canonical strict-empty `input` and returns the flat
`{status, manual, manual_path}` shape (plus `error` when the installed manual is
missing). The reserved child is registered unwrapped, so `ToolFamily.handle()`
returns its canonical `content`/`structuredContent` result verbatim and
`_adapt_manual_result` flattens it strictly *after* dispatch — no double wrap.

Unknown/absent `action` returns `{status: "error", message: "Unknown system
action: ..."}`, preserved verbatim from before the migration by normalizing the
generic dispatcher's `ACTION_REQUIRED` envelope failure. An unhashable `action`
(e.g. `[]` from invalid JSON) renders the same stable error rather than raising.
Notification verbs (`check`, `dismiss_channel`, `dismiss_event`, `dismiss_ref`)
and the legacy `notification`/`dismiss` aliases are **not** in the enum and
dispatch to the unknown-action error.

Envelope enforcement is two-layered. The composed schema correlates `action`
with `input` via a root `allOf`/`if`/`then` per action, and dispatch is the
always-authoritative, fail-closed second layer: it validates `action` before
child lookup, type-checks and strips root `summarize`, rejects unknown root
fields, and rejects `input` keys outside the selected action's own declared
schema **before** any handler runs. That last rule is the safety-relevant one
for this family — a cross-action smuggle such as `action='sleep',
input={'address': ...}` fails with no signal file written to any target.
Children receive only their own validated `input`: never `action`, `reasoning`,
`_reasoning`, `summarize`, or `_tc_id`.

Two fields deserve explicit mention because they are not a straight carry-over
of the pre-migration *schema*:

- `sleep.force` was always read by `karma._sleep` (the kernel#112 escape hatch)
  but was never advertised. A strict child `input` must declare every key its
  handler accepts, so it is now declared. This surfaces existing behavior; it
  grants no new capability.
- `notification_threshold_chars` is declared by no action here (nor by any
  `context` action) — the threshold is config-only
  (`manifest.summarize_notification_threshold` + refresh). The private engine's
  loud `runtime_threshold_change_not_supported` refusal is retained as the
  inner layer for direct in-process callers that bypass the envelope.

The karma gate (`_check_karma_gate`) requires `admin.karma=True` for the
five control verbs and `admin.karma AND admin.nirvana` for `nirvana`; it also
rejects a missing address, a self-target, and a non-agent target.

**Behavioral tests**: the agent-observable promises of this clause are guarded
by [`BEHAVIORS.md`](BEHAVIORS.md) — authorization gate
([B001](BEHAVIORS.md#behavior-b001)), signal-file effects
([B002](BEHAVIORS.md#behavior-b002), [B003](BEHAVIORS.md#behavior-b003)),
refusal paths ([B004](BEHAVIORS.md#behavior-b004),
[B005](BEHAVIORS.md#behavior-b005)), and nirvana privilege
([B006](BEHAVIORS.md#behavior-b006)). Change any of these behaviors, update the
matching behavior entry and this clause together.

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
The private summarize engine (`summarize.py`, driven by
`context(action='summarize'|'rebuild')`) mutates live chat history in place
(replacing tool-result block content with a `lingtai_agent_summarized_result`
marker), persists via `_save_chat_history`, and leaves the original payload
traceable in `<workdir>/logs/events.jsonl` by `tool_call_id`. That state is
described here because the engine module lives here; the public actions that
drive it are `context`'s.

## Cross-platform invariants

- Address resolution uses `resolve_address` and all target file access is via
  `pathlib.Path.write_text` / `shutil.rmtree`; no shell-outs. DOCUMENT.
- Karma signal files are empty markers (except `.clear`, whose content is the
  `source` string), read/written UTF-8. DOCUMENT (do not change).
- The private summarize engine does no filesystem or subprocess work of its
  own beyond the history save; it operates on in-memory `ChatInterface` blocks.
  DOCUMENT: no platform-specific behavior; all file access via pathlib.
- No subprocess/PTY in this tool. DOCUMENT.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `system` is a wired intrinsic | `src/lingtai/tools/system/__init__.py` | `tests/test_system.py::test_system_in_all_intrinsics`, `tests/test_system.py::test_system_wired_in_agent` |
| `sleep` transitions the agent to ASLEEP (self, no karma) | `src/lingtai/tools/system/karma.py:_sleep` | `tests/test_system.py::test_system_self_sleep` |
| Unknown/legacy actions return the unknown-action error | `src/lingtai/tools/system/__init__.py:handle` | `tests/test_system.py::test_system_rejects_unknown_and_retired_actions` |
| `refresh` with an unauthorized preset is refused | `src/lingtai/tools/system/preset.py:_refresh` | `tests/test_system.py::test_refresh_with_unauthorized_preset_returns_error` |
| `refresh` cannot combine `preset` and `revert_preset` | `src/lingtai/tools/system/preset.py:_refresh` | `tests/test_system.py::test_refresh_revert_preset_with_preset_arg_errors` |
| `presets` lists the allowed library and strips credentials | `src/lingtai/tools/system/preset.py:_presets` | `tests/test_system.py::test_presets_action_lists_full_library`, `tests/test_system.py::test_presets_action_strips_credentials` |
| `cpr` propagates launch failure instead of reporting success | `src/lingtai/tools/system/karma.py:_cpr` | `tests/test_system.py::test_cpr_propagates_launch_failure_instead_of_resuscitated` |
| The two name actions preserve live identity, `.agent.json`, and the protected prompt `identity` section, and mutate neither address nor workdir | `src/lingtai/tools/system/name.py` | `tests/test_tool_family_system_migration.py::test_name_actions_preserve_identity_semantics` |
| The public `summarize` action is gone from `system` with no alias | `src/lingtai/tools/system/schema.py:ACTION_ORDER` | `tests/test_tool_family_system_migration.py::test_public_summarize_action_is_gone_and_fails_loudly`, `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss` |
| `items`/`rebuild` belong to no `system` action | `src/lingtai/tools/system/schema.py:INPUT_SCHEMAS` | `tests/test_tool_family_system_migration.py::test_departed_summarize_fields_are_rejected_on_every_action` |
| PRIVATE ENGINE (driven by `context`, not a `system` action): records a `pending` marker and can rebuild the provider context | `src/lingtai/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_summarize_writes_pending_status_marker`, `tests/test_system_summarize.py::test_rebuild_true_with_items_records_marks_done_then_rebuilds` |
| PRIVATE ENGINE: no items with the rebuild discriminator off is an invalid no-op | `src/lingtai/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_missing_items_without_rebuild_is_invalid_no_op` |
| PRIVATE ENGINE: runtime threshold mutation is rejected | `src/lingtai/tools/system/summarize.py:_summarize` | `tests/test_system_summarize.py::test_summarize_runtime_threshold_change_rejected` |
| Notification/dismiss actions are dropped from the `system` schema | `src/lingtai/tools/system/schema.py` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss`, `tests/test_notification_tool.py::test_system_rejects_dismiss_action` |
| Karma signal files clear a target channel path end-to-end | `src/lingtai/tools/system/karma.py` | `tests/test_system_dismiss.py` |
| The model-facing root is the closed LTP v2 envelope with the twelve current actions | `src/lingtai/tools/system/__init__.py:get_schema` | `tests/test_tool_family_system_migration.py::test_root_envelope_is_exactly_the_four_ltp_v2_fields`, `::test_public_tool_name_and_action_inventory_are_unchanged` |
| Each action's arguments live only in its own strict `input` | `src/lingtai/tools/system/schema.py:INPUT_SCHEMAS` | `tests/test_tool_family_system_migration.py::test_action_input_fields_match_what_the_handler_reads` |
| A cross-action smuggle is rejected before any lifecycle I/O | `src/lingtai/tools/tool_family/__init__.py:ToolFamily.handle` | `tests/test_tool_family_system_migration.py::test_cross_action_input_is_rejected_before_any_lifecycle_io` |
| Envelope metadata never reaches a child handler | `src/lingtai/tools/system/__init__.py:_build_children` | `tests/test_tool_family_system_migration.py::test_envelope_metadata_never_reaches_a_child_handler` |
| `manual` is the reserved family-owned child, returned without double wrap | `src/lingtai/tools/system/__init__.py:_adapt_manual_result` | `tests/test_tool_family_system_migration.py::test_manual_child_is_registered_unwrapped`, `::test_manual_returns_the_pinned_flat_public_shape` |
| `system` is on the kernel `summarize` allowlist | `src/lingtai/kernel/tool_result_summary.py:_LTP_V2_MIGRATED_FAMILIES` | `tests/test_tool_family_system_migration.py::test_system_is_on_the_kernel_summarize_allowlist` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Karma gate blocks unauthorized control | `tests/test_system.py::test_refresh_with_unauthorized_preset_returns_error` + karma gate paths | Call `lull` without `admin.karma` | Any agent could sleep/destroy peers |
| `nirvana` requires karma AND nirvana | karma gate in `src/lingtai/tools/system/karma.py:_check_karma_gate` | Call `nirvana` with only karma | Irreversible deletion by under-privileged agent |
| The private engine preserves the original in events.jsonl | `tests/test_system_summarize.py::test_summarize_replaces_block_content` | `context(action='summarize')` a result, grep events.jsonl by tool_call_id | Loss of original tool output |
| The private engine's rebuild path flips pending markers done | `tests/test_system_summarize.py::test_rebuild_true_with_items_records_marks_done_then_rebuilds` | `context(action='summarize')` then `context(action='rebuild')`; inspect marker status | Pending compaction never applied |
| No public `summarize` action survives on `system` | `tests/test_tool_family_system_migration.py::test_public_summarize_action_is_gone_and_fails_loudly` | Call `system(action='summarize')` | A silent second context-hygiene surface |
| A true name stays immutable and neither name action renames the workdir | `tests/test_tool_family_system_migration.py::test_name_actions_preserve_identity_semantics` | `name_set` twice; inspect `.agent.json` + workdir | Identity overwrite or an unintended physical rename |
| No notification verbs on `system` | `tests/test_notification_tool.py::test_system_schema_drops_notification_and_dismiss` | Call `system(action='check')` | Duplicate notification surfaces diverge |
| Cross-action input cannot reach a lifecycle handler | `tests/test_tool_family_system_migration.py::test_cross_action_input_is_rejected_before_any_lifecycle_io` | Call `sleep` with an `address` in `input` | A weaker action smuggles a privileged target |
| `nirvana`'s blast radius is exactly one directory | `tests/test_tool_family_system_migration.py::test_nirvana_destroys_only_the_disposable_target` | Inspect siblings after a disposable-target nirvana | Irreversible over-deletion |

Run before merging system changes:

```bash
python -m pytest tests/test_system.py tests/test_system_summarize.py tests/test_system_dismiss.py tests/test_notification_tool.py tests/test_karma.py tests/test_tool_family_system_migration.py tests/test_tool_family_context_migration.py tests/test_intrinsic_manual_actions.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters resolve the top-level tool description
  through `wire_tool_description`: the global `WIRE_TOOL_DESCRIPTION` pointer
  while the resident `## tools` section is opted in via
  `LINGTAI_TOOL_PROSE_SECTION_ENABLED`, otherwise the full
  `FunctionSchema.description` prose (that section is off by default, so the
  wire is where the canonical prose lands). Nested parameter descriptions are
  unchanged either way.
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
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
