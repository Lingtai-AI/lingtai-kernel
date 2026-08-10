---
name: notification-tool
contract_version: 3
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/tools/registry.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/base_agent/turn.py
  - src/lingtai/agent.py
  - tests/test_notification_tool.py
  - tests/test_system_dismiss.py
  - tests/test_tools_package_data.py
  - src/lingtai/tools/notification/glossary-en.md
  - src/lingtai/tools/notification/glossary-zh.md
  - src/lingtai/tools/notification/glossary-wen.md
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/reference/dismissal-safety/SKILL.md
maintenance: |
  <!-- CANONICAL-MAINTENANCE v2 BEGIN -->
  This component contract is governed by the root CONTRACT.md. Keep
  related_files complete and repo-relative: the paired ANATOMY.md, Port, every
  production Adapter, contract tests, and directly relevant component contracts
  belong here. Re-read this contract whenever a linked boundary changes. Update
  the Port, affected Adapters, contract tests, and this contract in the same
  change; update the paired Anatomy when structure or composition also changes;
  bump contract_version for a breaking Port-contract change. If code and contract
  disagree, treat the disagreement as a defect—do not silently rewrite the
  normative contract to match the implementation.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  <!-- CANONICAL-MAINTENANCE END -->
---
# Notification Tool Contract

## Purpose

The mandatory `notification` tool is the sole agent-callable notification
surface. It exposes eight operational actions: four hook-registry actions
(`add`/`drop`/`edit`/`list`) plus the four pre-existing actions for reading or
atomically clearing notification mirrors (`check` and the three atomic dismiss
actions), plus one strictly read-only `manual` action for progressive
disclosure. It owns no producer state. The hook-registry actions mutate the
Notification Store's family-8 hook-manifest registry
(`load_hook_manifests`/`update_hook_manifests`/`stat_hook_registry`,
`.notification/hooks.json`);
the read and dismiss actions introduce no Store operation.

Hook channels are **per-agent**: the effective allowlist is the static set ∪ the
`mcp.` prefix ∪ the agent's own registered hook channels, and a hook channel is
allowed only for the agent whose workdir registered it — never process-global.

## Behavior

Guarded by: [K005](../../kernel/BEHAVIORS.md#behavior-k005), [K006](../../kernel/BEHAVIORS.md#behavior-k006)

LingTai agents MUST use `manual` only to retrieve installed guidance, `check` to
request current notification state, and the narrowest producer-specific or
atomic dismiss action after handling a notification. They MUST NOT treat generic
dismissal as mutation of producer canonical state, bypass protected channels, or
route large-result compaction through this tool.

Coding agents MUST preserve all eight operational actions, Store semantics,
notification Core guards, producer state, and the absence of `system`
notification/dismiss aliases. They MUST keep `manual` read-only, fixed to the
installed per-agent path, and independent of check/dismiss delivery state.
Procedures and safety explanations live in the linked notification manual and
nested references rather than in this contract.

## Port

The inbound agent-tool Port is named `notification`. It is a migrated LingTai
Tool Protocol v2 family (`../CONTRACT.md`): its model-facing root is a closed
object whose properties are exactly `action`, `input`, `reasoning`, and
`summarize`, with `additionalProperties: false` and `action`, `input`, and
`reasoning` required. The action domain, in order, is: `check`,
`dismiss_channel`, `dismiss_event`, `dismiss_ref`, `add`, `drop`, `edit`,
`list`, `manual`. Read/clear actions keep the pre-existing prefix stable;
hook-registry management (`add`/`drop`/`edit`/`list`) is administrative and
follows; `manual` closes the enum.
Each action value
is simultaneously the child's canonical name and its dispatch key; there is no
mapping layer.

`input` is the one strict object for the selected action. The root MUST expose
every action's exact input shape before invocation and MUST correlate the
`action` const to that action's own input schema, on both the Chat Completions
and Responses wires. Per-action inputs are:

- `check` — strictly empty.
- `dismiss_channel` — `channel` (required), plus nullable `force` and `reason`.
  `event_id` and `ref_id` are absent from this branch.
- `dismiss_event` — `event_id`, plus nullable `channel`, `force`, `reason`.
- `dismiss_ref` — `ref_id`, plus nullable `channel`, `force`, `reason`.
- `add` — `name`, `channel`, `source`, `description`, `how_to_modify`, and
  `how_to_cancel` (all required), plus nullable `version` and `instructions`.
- `drop` — `name` (required).
- `edit` — `name` (required), plus nullable `version`, `source`, `description`,
  `channel`, `how_to_modify`, `how_to_cancel`, and `instructions`.
- `list` — strictly empty.
- `manual` — strictly empty.

Declared optional fields use the provider-compatible nullable representation.
An explicit `null` MUST be treated as absent by the action implementation, so
`channel` still defaults to `system` for the targeted verbs and a null `reason`
does not satisfy the post-molt acknowledgement requirement.

`reasoning` and `summarize` are root-only cross-cutting envelope controls and
MUST NOT appear in any action's `input` or reach any action implementation.

Observable action contracts are:

- `check` returns `{_notification_placeholder: true, message}`; the turn-loop
  adapter may stamp `_meta.agent_meta.notifications.attention` and `_meta.agent_meta.guidance.transient` onto
  that same dict.
- `dismiss_channel` requires `channel`, rejects event/ref targets, and delegates
  a whole-mirror clear to notification Core.
- `dismiss_event` requires `event_id`; `dismiss_ref` requires `ref_id`; each
  defaults `channel` to `system` and delegates targeted removal to Core.
- A dismiss no-op (`status: "ok"`, `cleared: false`) carries a machine-readable
  `cause` so agents never retry blind (#716): `"already_empty"` (whole-channel
  clear found the channel already empty) or `"no_matching_event"`
  (event_id/ref_id matched no pending event). The values are the Core constants
  `DISMISS_CAUSE_ALREADY_EMPTY`/`DISMISS_CAUSE_NO_MATCHING_EVENT`
  (`src/lingtai/kernel/notifications.py:911-920`), stamped by the same branch
  that decides each no-op — not recomputed in the tool layer. `cause` is absent
  on successful (`cleared: true`) dismissals, and stale-version refusals remain
  a `status: "error"` contract without `cause`.
- `manual` reads only
  `<agent>/.library/intrinsic/capabilities/notification-manual/SKILL.md`.
  Success contains exactly `{status: "ok", notification_manual, manual_path}`.
  Absence contains exactly `{status: "degraded", notification_manual: "",
  manual_path, error}`, where `error` is `notification manual missing —
  initializer may have failed or capability not installed correctly`. Other
  filesystem/decoding errors propagate.
- `add` validates the manifest and appends it to the hook registry. Success
  returns `{status: "ok", reason: "added", name}`; `duplicate_name` and
  `channel_in_use` are `status: "error"` results that leave the registry
  unchanged. A channel that is a built-in static channel
  (`system`/`email`/`soul`/`goal`/`molt`/`nudge`/`post-molt`/`bash`/`btw`/`cron`/`tool_loop_guard`)
  or a Store-reserved non-channel stem (`hooks`/`large_result_acks`) is refused
  with `reason: "invalid_manifest"` and a clear message.
  Guards [N002](BEHAVIORS.md#behavior-n002) (registered channel passes through)
  and [N003](BEHAVIORS.md#behavior-n003) (lifecycle validation).
- `edit` updates the named hook's fields. Success returns
  `{status: "ok", reason: "edited", name}`; unknown names return
  `reason: "not_found"` and a channel move onto another hook's channel returns
  `reason: "channel_in_use"`. A channel move onto a built-in static channel or
  a Store-reserved non-channel stem is refused with `reason:
  "invalid_manifest"` and a clear message, exactly as `add` refuses the same
  channel. An empty `name` is refused with `reason: "invalid_manifest"` too,
  exactly as `add` refuses an empty manifest name. An `edit` providing no
  non-null fields returns
  `{status: "ok", reason: "no_change", name}` without touching the registry.
- `drop` removes the named hook and revokes its channel. Success returns
  `{status: "ok", reason: "dropped", name}`; unknown names return
  `reason: "not_found"`. An empty `name` is refused with
  `reason: "invalid_manifest"`, exactly as `add` refuses an empty manifest
  name. Dropping registration never kills the hook process —
  cancellation is the owner's job, documented in the manifest's
  `how_to_cancel`. Guards [N003](BEHAVIORS.md#behavior-n003) (lifecycle
  validation).
- `list` returns `{status: "ok", hooks: [...]}` with the persisted manifests
  in registry order, or an empty list when the registry is absent. When the
  registry exists but is corrupt (invalid JSON) or unreadable, `list`, `add`,
  `drop`, and `edit` all return a `hook_registry_load_failed` error result
  instead of misreporting the registry or raising.
- Unknown or absent actions return `{status: "error", message}` naming the
  unknown notification action.
- An invalid envelope — a non-object `input`, a non-boolean `summarize`, an
  unknown root field, or an `input` key belonging to another action's branch —
  returns `{status: "failed", error_code: "INVALID_ARGUMENT", message}` and MUST
  fail before the selected action's implementation runs.

Every action's success and error result stays canonical and raw. The Host owns
only outer invocation and presentation metadata; no action result is nested
inside another action-result envelope.

There is no aggregate `dismiss`, no `summarize` action, no `items` property, no
source checkout fallback, and no compatibility alias. No public `parameters`,
`arguments`, or payload alias is admitted.

## Adapters

`lingtai.tools.registry.INTRINSICS` is the composition wiring that installs the
package as a mandatory tool. `handle()` is the driving dispatch adapter for the
nine actions; it composes schema and envelope dispatch onto the generic
`lingtai.tools.tool_family` infrastructure, which is optional infrastructure
rather than a required base class (`../CONTRACT.md` "Implementation
independence"). The turn-loop notification post-hook completes `check` with the
single canonical model-visible payload. The three dismiss handlers adapt tool
arguments into `lingtai.kernel.notifications.dismiss_channel(...,
invoked_by="notification")`, where notification Core owns allowlists, guards,
stale checks, protected channels, acknowledgement policy, and Store use. The
four hook-registry handlers adapt tool arguments into Core's
`add_hook`/`drop_hook`/`edit_hook`/`list_hooks`, which validate manifests,
enforce name/channel uniqueness, and write `.notification/hooks.json` through
Store family 8. Core keeps a per-workdir module-level mirror of registered hook
channels (`_REGISTERED_HOOK_CHANNELS`, keyed by the agent's working directory),
serialized under `_HOOK_REGISTRY_LOCK`; `sync_hook_registry` re-seeds it
whenever `hooks.json`'s `(st_mtime_ns, st_size)` stat changes — including
out-of-band writes from another process (sibling CLI, Telegram server, hook
installer) — and marks a workdir seeded only after a successful load, logging a
transient failure (`notification_hook_registry_error`, `phase=...`) for retry on
the next sync.

The `manual` action is the reserved family child built by
`tool_family.manual.build_manual_child` over the shared
`tools/_manual.py::load_installed_manual` loader: one `is_file` check and one
UTF-8 read at the fixed path. It does not call notification Core,
`NotificationStorePort`, the post-hook, or a producer. That child's canonical
`content`/`structuredContent` result is returned by the family dispatcher
verbatim; flattening it to this Port's pinned `notification_manual` shape is a
Host presentation step that runs strictly after dispatch, never inside the
child. Agent initialization copies the bundled first-level
`notification-manual` skill tree into the installed per-agent intrinsic library.

`handle()` strips the kernel-injected `_tc_id` before envelope validation.
`base_agent.tools._dispatch_tool` adds that field to every intrinsic's args as
pre-existing kernel plumbing; it is not a public root field and only
`context.molt` consumes it.

The kernel's IDLE/ASLEEP notification-sync pair is deliberately
byte-shape-identical to a voluntary `check`, so its synthesized call args carry
this same envelope. It is spliced onto the wire rather than dispatched, so it
is not a second inbound adapter.

## Contract rules

- `manual` MUST NOT read, create, clear, fingerprint, acknowledge, or otherwise
  mutate `.notification/` or producer state, and MUST NOT emit notification logs.
- Missing installed guidance is degraded, never a silent successful empty body;
  source-tree fallback and compatibility response aliases are forbidden.
- `check` remains a write-free placeholder path. Dismiss behavior and result
  shapes remain those of the canonical notification Core helper.
- Dismissal affects notification mirrors only. Producer guards, non-force stale
  refusal, protected-channel refusal, post-molt reasons, and unrelated-event
  preservation remain in force.
- `system` owns the `summarize` **action** and exposes no notification/dismiss
  alias. The root `summarize` boolean on this family is the unrelated
  cross-cutting result post-processing control, not an action. Because this
  family advertises it, `notification` MUST stay listed in
  `kernel/tool_result_summary.py::_LTP_V2_MIGRATED_FAMILIES`; otherwise the
  model would be shown a control the kernel silently ignores. The notification
  tool owns no producer publication action.
- `check` and `manual` are read-only. The three dismiss actions mutate
  notification mirror state; `add`/`drop`/`edit` mutate the hook registry and
  the registered-channel allowlist. The family MUST NOT present a posture
  weaker than its strongest action: a read-only annotation for the whole
  family would hide those mutations and is forbidden.
- Hook channels are per-agent: `is_channel_allowed`/`validate_allowed_channel`
  consult the mirror keyed by the agent's workdir, and every kernel call site
  passes that workdir. Without a workdir (no agent context) hook channels are
  NOT allowed — only the static set and the `mcp.*` prefix pass.
  Guards [N001](BEHAVIORS.md#behavior-n001) (unregistered channel blocked +
  warn-and-flag) and [N002](BEHAVIORS.md#behavior-n002) (registered channel
  passes through).
- Envelope validation MUST precede action I/O. Cross-action input, unknown root
  fields, and unknown actions MUST be rejected with a stable typed failure and
  no notification read or write.
- Schema descriptions are canonical English and language-independent. Action
  identifiers and properties have no localized aliases. All three owned
  glossaries require review when this enum changes; the LTP v2 envelope
  restructures how arguments are carried, and the hook-registry change adds
  four new action values (`add`/`drop`/`edit`/`list`) to the enum.
- `contract_version` is `3`: the hook-registry change adds four new action
  values (`add`/`drop`/`edit`/`list`) to the closed action enum and a new
  per-action `input` for each — a breaking Port-contract change for callers
  even though the tool name, the five pre-existing action values, and every
  pre-existing result shape are preserved. (Version `2` recorded the LTP v2
  migration that moved every action argument from flat root properties into a
  per-action `input` object and added required `reasoning`.)

## Contract tests

`tests/test_notification_tool.py` proves mandatory registration and wiring, the
ordered nine-action schema, the closed LTP v2 root, each action's strict input
branch and its `allOf` action/input correlation, Chat/Responses wire parity,
the `manual` branch matching the shared ManualTool child, canonical
description, absent aggregate actions, manual success/degraded envelopes and
fixed path, no-double-wrap flattening, read-only state/log behavior, check
placeholder shape, all atomic dismiss semantics, hook add/drop/edit/list
lifecycle and whitelist gating, null-optional defaulting,
cross-action rejection before I/O, `_tc_id` tolerance, the kernel summarize
allowlist entry, Core guards, and absence of system compatibility aliases. `tests/test_system_dismiss.py` protects shared
operational dismissal behavior. `tests/test_tools_package_data.py` verifies tool
and documentation package data. Architecture, Anatomy drift, glossary, and skill
validators cover the linked document and manual graphs.

## Maintenance

Read the paired Anatomy for current symbol locations, wiring, composition, state,
and verified citations. Keep implementation, schema, registry wiring, focused
tests, glossaries, and the manual/reference graph synchronized. Do not duplicate
manual procedures here or expand this slice into Store, producer, system, or
summarization changes.
