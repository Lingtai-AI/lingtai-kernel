---
name: context-contract
tool: context
contract_version: 7
related_files:
  - src/lingtai/tools/context/BEHAVIORS.md
  - src/lingtai/tools/context/__init__.py
  - src/lingtai/tools/context/_molt.py
  - src/lingtai/tools/context/_session_journal.py
  - src/lingtai/tools/context/settings.py
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/tools/context/ANATOMY.md
  - src/lingtai/agent.py
  - src/lingtai/kernel/base_agent/prompt.py
  - src/lingtai/tools/system/summarize.py
  - src/lingtai/tools/system/CONTRACT.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/lingtai/CONTRACT.md
  - src/lingtai/tools/context/manual/SKILL.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - tests/test_context_ownership_redesign.py
  - tests/test_tool_family_context_migration.py
  - tests/test_deep_refresh.py
  - tests/test_context_declared_tool_plugin.py
  - tests/test_context_settings.py
maintenance: |
  Keep related paths real and the paired Anatomy reciprocal. Update schemas,
  model prose, manuals, results, lifecycle wiring, private summary engine, and
  focused evidence together. Version 7 opts Context into the bounded settings
  action without changing its lifecycle engines or persistence; version 6
  packaged the established context-manual with its owner and recut the public
  surface through the declared host-plugin contract.
---

# Context capability contract

## Purpose and public ownership

Guarded by: [K003](../../kernel/BEHAVIORS.md#behavior-k003)

`context` owns the agent's context lifecycle. Its exact public actions are:

- `molt` — shed conversation history while preserving durable stores;
- `summarize` — record compact replacements in local runtime history only;
- `rebuild` — the **one active full context reconstruction operation**;
- `settings` — show effective Context policy through the read-only five-field
  projection;
- `manual` — return `context-manual` without a lifecycle operation.

The implementation is an official declared host plugin: its static
`DECLARATION` owns the identity, actions, settings opt-in, schemas, and
`context-manual`; its
binder receives only `workdir` and `context_runtime`; its three narrow lifecycle
operations delegate the existing live molt/summarize/rebuild engines, and its
owner adapter supplies the read-only settings provider. The package
manual under `tools/context/manual/` is the sole canonical source and is installed
at the longstanding `context-manual` path. Any same-name collision fails loudly.
No OLD
`psyche` action is reachable anywhere, and none is aliased here. A public
root named `psyche` does exist again — it is the read-only family for the four
durable domains (`pad + lingtai + knowledge + skills = psyche`,
`src/lingtai/tools/psyche/CONTRACT.md`) — it carries five strict-empty manual
loaders plus redacted settings SHOW for its two Pad inputs.
`psyche.context_molt`, `psyche.pad_edit`, `psyche.lingtai_update`,
`psyche.name_set`, and every other old spelling fail as unknown actions: root
reuse is not action compatibility. The lifecycle actions live here; name changes
remain `system.name_set | system.name_nickname`. Pad body and LingTai identity
mutation belong to `file.write | file.edit`; neither domain exposes a public
mutating action.

## LTP v2 port

The root is the strict closed envelope `action`, `input`, `reasoning`, optional
root `summarize`; `action`, `input`, and `reasoning` are required. Root
`summarize` is unrelated Host result presentation and never child input.
Schema and dispatch derive from one child registry.

| Action | Input | Result / errors |
|---|---|---|
| `molt` | required `summary`, `session_journal_path`, nullable `keep_tool_calls`, `keep_last` | preserved molt receipt/errors and refusal-before-shed gates |
| `summarize` | required nonempty `items` | record-only marker/result; no prompt composition or provider rebuild |
| `rebuild` | optional nullable `items`; bare `{}` is valid | summary-engine rebuild result plus `prompt_reconstructed: true`; LTP-shaped reconstruction/no-session errors |
| `settings` | strict `{}` | seven ordered rows containing exactly `key`, `current`, `default`, `configurable`, `comment`; one fixed bounded whole-action failure on unavailable/malformed/unserializable provider output |
| `manual` | strict `{}` | flat manual result |

`settings` is inserted immediately before `manual` only through the declaration's
generic opt-in seam. Unknown actions and branch/root shape errors fail before handler I/O. `_tc_id` is
stripped from the closed root and reaches `molt` only. No retired Pad/LingTai or
system-summarize spelling is accepted.

## Settings inventory and authority

Guarded by: [T011](../tool_family/BEHAVIORS.md#behavior-t011)

The provider returns exactly these ordered key/comment pairs:

| Key | Exact manual pointer |
|---|---|
| `context_limit` | `context-manual#context-limit` |
| `summarize_notification_threshold` | `context-manual#summarize-notification-threshold` |
| `system_prompt_pressure_ratio` | `context-manual#system-prompt-pressure-ratio` |
| `pressure_high_ratio` | `context-manual#pressure-high-ratio` |
| `forced_rebuild_ratio` | `context-manual#forced-rebuild-ratio` |
| `pressure_warn_after_rounds` | `context-manual#pressure-warn-after-rounds` |
| `recovery_target_ratio` | `context-manual#recovery-target-ratio` |

The first three rows are configurable only through the authorized procedures at
those exact manual targets; the four kernel constants are not configurable. The
SHOW action itself never writes config, process environment, or runtime state.
All seven are non-sensitive public scalars; session paths, history, prompt text,
provider identity, and credentials are absent. A provider raises when either
live runtime value is unavailable, so no partial or placeholder row is returned.
The generic action owns the fixed failure and 65,536-byte complete-response
bound. Accepted values, precedence, canonical keys, apply timing, authorization,
and change/verification procedures live only in `context-manual`.

## Full reconstruction ordering

Every `context.rebuild`, including bare `{}` with **zero pending summaries**,
must execute in this order:

1. call `Agent._reconstruct_context()`;
2. through `Agent._reload_prompt_sections`, re-read/recompose **all** canonical
   configured, durable, and packaged prompt sources: base prompt, covenant,
   configured/self-authored character, substrate, rules, Pad body plus pinned
   references, the enabled Skills and Knowledge catalogs, principle, procedures,
   guidance mirror, brief, and comment. The Skills and Knowledge catalogs are
   rescanned from disk through their capabilities' own private composers; a
   disabled domain is skipped entirely (no section written, no scan), and the
   Knowledge one-time legacy migration is **not** reachable from this path — it
   remains owned by that capability's setup/refresh lifecycle;
3. perform exactly one final full prompt build/flush through the Agent override
   after every section is composed; private Pad/LingTai composers must not
   publish intermediate prompts, and the live interface plus `system/system.md`
   must contain the same newly composed prompt;
4. only then record newly supplied summaries and/or mark already-pending
   summaries applied;
5. only then request provider history replay/rebuild with the new prompt and
   rewritten history.

The private Pad/LingTai composers remain reused; composition logic is not
copied into the context handler. Reconstruction failure returns
`context_reconstruction_unavailable` or `context_reconstruction_failed` without
applying summaries or requesting provider replay.

## Passive lifecycle scenarios

Guarded by: [L004](BEHAVIORS.md#behavior-l004)

Refresh and molt are passive scenarios invoking the same internal
`Agent._reconstruct_context` contract:

- refresh passes its already-resolved init mapping, completes the final prompt
  flush, then rebuilds the session with preserved history;
- Agent registers exactly one post-molt hook (`_reconstruct_context`), invoked
  before the fresh session is created;
- Pad/LingTai `boot` functions do initial private composition only and register
  no competing section-specific hooks.

Their distinct lifecycle effects remain unchanged: refresh rebuilds runtime
configuration/capabilities while preserving conversation; molt validates the
journal and keep sets, snapshots/archives, sheds/replays selected history,
updates `molt_count`, writes its summary, and publishes the post-molt reminder.

## Molt safety invariants

Guarded by: [L003](BEHAVIORS.md#behavior-l003), [K004](../../kernel/BEHAVIORS.md#behavior-k004)

Agent-initiated molt requires a nonempty retrospective and a valid
`knowledge/session-journal/<entry>/KNOWLEDGE.md` with session-journal
frontmatter. Validation and keep-list checks occur before snapshot/archive/wipe
or count mutation. The true system-forced `context_forget` path remains distinct
and synthesizes its own model-visible call/result pair. Durable history and
summary paths remain under `history/` and `system/summaries/`; notification files
survive the shed.

## Evidence

Focused verification:

```bash
python -m pytest -q tests/test_context_ownership_redesign.py \
  tests/test_tool_family_context_migration.py tests/test_deep_refresh.py \
  tests/test_pad_lingtai_split.py tests/test_context_declared_tool_plugin.py \
  tests/test_context_settings.py
```

Evidence pins public action sets and strict retirement; file and append no-hot-
load; bare zero-pending reconstruction; compose-before-summary-before-provider
ordering (including provider replay observing the new prompt); all canonical
durable sources; one shared refresh/molt hook; manual strictness; provider-wire parity; and existing molt refusal/lifecycle semantics.
The settings evidence additionally pins exact five-field rows and manual targets,
live current/default/configurable values, strict empty input, whole-action
unavailable-current failure, absence of sensitive material, and ordinary rebuild
non-regression.
