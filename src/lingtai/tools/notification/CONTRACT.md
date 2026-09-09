---
name: notification-tool
contract_version: 8
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - src/lingtai/tools/notification/settings.py
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/tools/registry.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/kernel/tool_plugin/__init__.py
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/kernel/base_agent/turn.py
  - src/lingtai/agent.py
  - ENVIRONMENT_VARIABLES.md
  - tests/test_notification_tool.py
  - tests/test_notification_settings.py
  - tests/test_notification_delay_alarm.py
  - tests/test_daemon_attention_delay.py
  - tests/test_system_dismiss.py
  - tests/test_tools_package_data.py
  - src/lingtai/tools/notification/glossary-en.md
  - src/lingtai/tools/notification/glossary-zh.md
  - src/lingtai/tools/notification/glossary-wen.md
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/tools/notification/manual/SKILL.md
  - src/lingtai/tools/notification/manual/reference/channel-model/SKILL.md
  - src/lingtai/tools/notification/manual/reference/dismissal-safety/SKILL.md
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

The always-on official `notification` tool is the sole agent-callable
notification surface. It exposes nine operational actions: four hook-registry actions
(`add`/`drop`/`edit`/`list`), the four pre-existing actions for reading or
atomically clearing notification mirrors (`check` and the three atomic dismiss
actions), and consumer-only `delay`, plus strictly read-only `settings` and
`manual` actions for progressive disclosure. It owns no producer state. The
hook-registry actions mutate the
Notification Store's family-8 hook-manifest registry
(`load_hook_manifests`/`update_hook_manifests`/`stat_hook_registry`,
`.notification/hooks.json`);
the read and dismiss actions introduce no Store operation.

Hook channels are **per-agent**: the effective allowlist is the static set ∪ the
`mcp.` prefix ∪ the agent's own registered hook channels, and a hook channel is
allowed only for the agent whose workdir registered it — never process-global.

## Behavior

Guarded by: [K005](../../kernel/BEHAVIORS.md#behavior-k005),
[K006](../../kernel/BEHAVIORS.md#behavior-k006),
[N005](BEHAVIORS.md#behavior-n005)

LingTai agents MUST use `manual` only to retrieve installed guidance, `check` to
request current notification state, and the narrowest producer-specific or
atomic dismiss action after handling a notification. They MUST NOT treat generic
dismissal as mutation of producer canonical state, bypass protected channels, or
route large-result compaction through this tool.

Coding agents MUST preserve all nine operational actions, Store semantics,
notification Core guards, producer state, and the absence of `system`
notification/dismiss aliases. They MUST keep `manual` read-only, fixed to the
installed per-agent path, and independent of check/dismiss delivery state.
They MUST keep `settings` SHOW-only and route each row to its exact manual
section instead of returning configuration or mutation instructions inline.
Procedures and safety explanations live in the linked notification manual and
nested references rather than in this contract.

## Port

The inbound agent-tool Port is named `notification`. It is a migrated LingTai
Tool Protocol v2 family (`../CONTRACT.md`): its model-facing root is a closed
object whose properties are exactly `action`, `input`, `reasoning`, and
`summarize`, with `additionalProperties: false` and `action`, `input`, and
`reasoning` required. The action domain, in order, is: `check`,
`dismiss_channel`, `dismiss_event`, `dismiss_ref`, `add`, `drop`, `edit`,
`list`, `delay`, `settings`, `manual`. Read/clear actions keep the pre-existing prefix stable;
hook-registry management (`add`/`drop`/`edit`/`list`) is administrative and
follows; consumer-only `delay` follows them; `settings` is injected immediately
before `manual`, which closes the enum.
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
- `delay` — `channel` and integer `seconds` (both required); `seconds=0` cancels,
  and a nonzero value is bounded by the live
  `LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS` cap (default 600).
- `settings` — strictly empty; SHOW has no set/reset or mutation branch.
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
  `<agent>/.library/intrinsic/capabilities/notification/SKILL.md`.
  Success contains exactly `{status: "ok", notification_manual, manual_path}`.
  Absence contains exactly `{status: "degraded", notification_manual: "",
  manual_path, error}`, where `error` is `notification manual missing —
  initializer may have failed or capability not installed correctly`. Other
  filesystem/decoding errors propagate.
- `add` validates the manifest and appends it to the hook registry. Success
  returns `{status: "ok", reason: "added", name}`; `duplicate_name` and
  `channel_in_use` are `status: "error"` results that leave the registry
  unchanged. A channel that is a built-in static channel
  (`system`/`email`/`soul`/`goal`/`molt`/`nudge`/`post-molt`/`bash`/`btw`/`cron`/`daemon`/`delay-alarm`/`tool_loop_guard`)
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
- `delay` validates an allowed target channel and refuses `delay-alarm`; one
  active delay is persisted per agent and a nonzero call replaces a prior live
  delay explicitly. `seconds=0` cancels the matching live target early; every
  action invocation live-reads `LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS` (default
  600) for its finite nonzero cap. Missing/blank/invalid/non-positive values log
  a diagnostic fallback to 600 rather than permitting unbounded silence. Delay
  never rewrites or clears the target producer file: while live, the coherent
  consumer snapshot/fingerprint omits only that target (including voluntary
  `check`), while all other channels stay normal. The aggregate `daemon`
  channel is the one target delay suppresses *attention* for instead of hiding:
  its payload, byte-exact delivered version, and bounded `agent_state.daemon`
  summary stay current and readable (snapshot, `check`, delivery, non-forced
  dismissal), while its attention entry collapses to the single constant token
  `daemon:delayed=1` so daemon appends, alarm crossings, and clears cannot move
  the wake fingerprint. Registered hook channels and every other channel keep
  byte-exact change detection and wake normally throughout. The durable
  `alarm_fired` latch is unchanged, so a crossing during the delay alarms when
  it expires — deferred, never dropped. Guards
  [N004](BEHAVIORS.md#behavior-n004) (daemon delay masks attention only while
  independent channels still wake). At expiry/recovery it stops
  filtering and writes exactly one high-priority latest-only `delay-alarm`
  mirror identifying target, requested/actual duration, byte-level changed/no-
  change, and only conservative producer-reported/retained-event statistics.
  Its private `.notification/.delay_state.json` state is atomically replaced
  under the established cross-process Store lock; a daemon process timer and
  heartbeat/sync recovery share a stable request id so stale callbacks/restarts
  cannot append duplicate alarms. Malformed/unreadable delay state fails open to
  target visibility, never silence.
- `settings` returns exactly two rows, in order, under the sole top-level key
  `settings`. Their keys are `notification.max_chars` and
  `notification.delay_max_seconds`; `current` comes through the live
  `NotificationStatePort.read_settings()` adapter from the same effective
  resolvers the payload builders and delay action consume. The cap therefore
  includes live environment → Agent/System-v2 file hook → default precedence;
  the delay ceiling includes live environment → default precedence. Defaults
  are `10000` and `600`; both rows are non-sensitive and `configurable: true`.
  Their exact comments are
  `notification-manual#block-size-cap-persistent-and-attention-lanes` and
  `notification-manual#consumer-delay-and-expiry-alarm`. Every row projects
  exactly `key`, `current`, `default`, `configurable`, and `comment`. Source,
  precedence, canonical names, accepted values, apply timing, authorization,
  and change/verification procedures live only in those manual sections.
  Provider, unavailable-current, malformed-row, or serialization failure fails
  the whole action with the generic fixed bounded result; partial rows are
  forbidden. Guards [N005](BEHAVIORS.md#behavior-n005).
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

`notification` is wired through the declared host-plugin route, not
`registry.INTRINSICS`. `registry.BUILTIN_TOOLS` and `CORE_DEFAULTS` keep it
available on every normal Agent; `setup(agent)` hands its static
`ToolPluginDeclaration` to `register_agent_tool_plugins`. The kernel registrar
reserves the official name before binding/mounting and grants this family only
`workdir` (for its installed package manual) and `notification_state`.

`AgentNotificationStateAdapter` in
`src/lingtai/adapters/tool_plugin_host.py` translates that state port into
callbacks bound to the real agent's existing Notification Core functions. The
family's handlers therefore adapt only public arguments and results. The three
dismiss operations reach `dismiss_channel(..., invoked_by="notification")`; the
four hook verbs reach Core's persisted registry operations; and `delay` reaches
Core's durable consumer-delay/timer/alarm policy. No tool-local Store, producer,
delivery, or dismissal helper is permitted.

The reserved `manual` child is built by
`tool_family.manual.build_manual_child(host.workdir, DECLARATION.manual)` over
the shared `tools/_manual.py::load_installed_manual` loader. It reads exactly
`<agent>/.library/intrinsic/capabilities/notification/SKILL.md`, never Core or a
producer. Its canonical child result is flattened to notification's pinned
`notification_manual` shape strictly after dispatch.

The reserved `settings` child is injected by generic ToolFamily composition
from boolean `DECLARATION.settings=True`. The bound zero-argument provider
closes over only the zero-argument `read_settings` callback extracted from
`NotificationStatePort`; `settings.py::notification_settings` turns its two
effective scalars into `SettingRow` values. The schema-only family uses a no-I/O
empty provider. Neither path receives a mutating port, writer, Agent, Store, or
configuration object.

The existing IDLE/ASLEEP notification-sync pair is intentionally
byte-shape-identical to a voluntary `check`. It is spliced onto the wire rather
than dispatched, so it is not a second inbound adapter.

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
- `context` owns the `summarize` **action**; `system` exposes no notification/dismiss
  alias. The root `summarize` boolean on this family is the unrelated
  cross-cutting result post-processing control, not an action. Because this
  family advertises it, `notification` MUST stay listed in
  `kernel/tool_result_summary.py::_LTP_V2_MIGRATED_FAMILIES`; otherwise the
  model would be shown a control the kernel silently ignores. The notification
  tool owns no producer publication action.
- `check`, `settings`, and `manual` are read-only. The three dismiss actions mutate
  notification mirror state; `add`/`drop`/`edit` mutate the hook registry and
  the registered-channel allowlist; `delay` atomically mutates only private
  consumer delay state and (on expiry) the separate `delay-alarm` mirror.
  The family MUST NOT present a posture
  weaker than its strongest action: a read-only annotation for the whole
  family would hide those mutations and is forbidden.
- `settings` accepts only `input={}` and MUST NOT change process environment,
  files, notification state, delay state, hook manifests, or producer state.
  `configurable` means an authorized external owner procedure exists; it is not
  mutation authority for SHOW.
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
- `contract_version` is `8`: Notification opts into the merged read-only
  five-field settings seam for its two existing controls, with live Agent-hook
  current truth and `settings` immediately before `manual`. Version `7` made
  notification an official declared host plugin with a package-owned manual
  and a narrow Core-state port. Version `6`: a `delay` whose target is the aggregate `daemon`
  channel now masks that channel's attention token instead of omitting it from
  the coherent consumer read, so daemon truth, delivered version, and dismissal
  keep working while it is delayed. Non-daemon targets are unchanged.
- `contract_version` `5`: `delay` resolves its nonzero duration cap live
  from `LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS` (default 600) rather than a
  static schema maximum, so callers must use the current action contract rather
  than cache a fixed bound. Version `4` added the consumer-only closed-enum
  action and strict branch; version `3` added hook-registry actions; version `2`
  recorded the LTP v2 envelope migration.

## Contract tests

`tests/test_tool_plugin_declaration.py` proves the live official mount, package manual, and Core-backed dismissal; `tests/test_notification_tool.py` proves the
ordered eleven-action schema, the closed LTP v2 root, each action's strict input
branch and its `allOf` action/input correlation, Chat/Responses wire parity,
the `manual` branch matching the shared ManualTool child, canonical
description, absent aggregate actions, manual success/degraded envelopes, delay schema ordering, and
fixed path, no-double-wrap flattening, read-only state/log behavior, check
placeholder shape, all atomic dismiss semantics, hook add/drop/edit/list
lifecycle and whitelist gating, null-optional defaulting,
cross-action rejection before I/O, `_tc_id` tolerance, the kernel summarize
allowlist entry, Core guards, and absence of system compatibility aliases. `tests/test_daemon_attention_delay.py` covers the daemon-target attention mask, hook-channel wake independence, bounded expiry wake, and fail-open delay state. `tests/test_notification_delay_alarm.py`
proves consumer filtering for coherent/voluntary reads, early cancellation,
replacement, durable expiry/recovery idempotence, alarm priority and conservative
statistics, and `delay-alarm` refusal. `tests/test_system_dismiss.py` protects shared
operational dismissal behavior. `tests/test_notification_settings.py` proves
the exact five-field rows and keys, live environment/System-v2 precedence,
defaults, strict input, comment targets, whole-action failure, no mutation,
family opt-in, and unchanged check. `tests/test_tools_package_data.py` verifies
tool and documentation package data. Architecture, Anatomy drift, glossary, and skill
validators cover the linked document and manual graphs.

## Maintenance

Read the paired Anatomy for current symbol locations, wiring, composition, state,
and verified citations. Keep implementation, schema, registry wiring, focused
tests, glossaries, and the manual/reference graph synchronized. Do not duplicate
manual procedures here or expand this slice into Store, producer, system, or
summarization changes.
