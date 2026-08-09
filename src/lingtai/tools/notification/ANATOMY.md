---
related_files:
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/registry.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/base_agent/turn.py
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/agent.py
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/reference/dismissal-safety/SKILL.md
  - tests/test_notification_tool.py
  - tests/test_system_dismiss.py
  - src/lingtai/tools/notification/glossary-en.md
  - src/lingtai/tools/notification/glossary-zh.md
  - src/lingtai/tools/notification/glossary-wen.md
maintenance: |
  tool_family is generic optional infrastructure this package composes onto;
  notification's own per-call family construction, null-stripping, and manual
  presentation adapter remain here.
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Notification Tool Anatomy

`src/lingtai/tools/notification/` is the mandatory agent-callable notification
surface. It composes nine actions: four hook-registry actions (`add`, `drop`,
`edit`, `list`), `check`, three atomic dismissal actions, and the strictly
read-only `manual` action. Notification Core owns mirror guards and Store use;
the tool owns only schema composition, envelope dispatch, the check
placeholder, argument adaptation, hook-manifest forwarding, and installed-manual
presentation.

Since the LTP v2 migration the public model-facing shape is the closed
`action` + `input` + `reasoning` + `summarize` envelope, with each action's
arguments in its own strict `input` object. Schema composition and envelope
dispatch delegate to the generic `tool_family` infrastructure; this package
retains ownership of the action implementations, the per-action schemas, and
the manual presentation shape. The public tool name and the five pre-existing
action values are unchanged; the four hook-registry actions are new.

## Components

- `schema.py` is data only: `ACTION_ORDER` is the single source for enum,
  branch, and child-registration order; `INPUT_SCHEMAS` holds each action's own
  strict `input` schema, with optional dismiss fields in the
  provider-compatible nullable representation; `ACTION_ENUM_DESCRIPTION` and
  `get_description()` hold the canonical-English prose
  (`src/lingtai/tools/notification/schema.py:48-178`). It deliberately defines
  no `get_schema`.
- `_schema_only_family()` / `_FAMILY` build the import-time `ToolFamily` used
  only to compose the schema; constructing it at import proves the fixed
  nine-child registry has no duplicate or reserved-`manual` collision
  (`src/lingtai/tools/notification/__init__.py:95-122`).
- `get_schema()` returns the composed family schema and substitutes
  notification's own action prose for the generic composer's neutral
  placeholder (`src/lingtai/tools/notification/__init__.py:125-140`).
- `_build_family()` builds the per-call dispatching `ToolFamily` with handlers
  bound to the calling `agent`, registering the shared `manual` child directly
  and unwrapped (`src/lingtai/tools/notification/__init__.py:357-405`).
- `handle()` strips kernel-injected `_tc_id`, delegates envelope validation and
  dispatch to that family, adapts the `manual` child result, and normalizes the
  generic `ACTION_REQUIRED` error back to the pinned unknown-action shape
  (`src/lingtai/tools/notification/__init__.py:407-446`).
- `_strip_nulls()` converts explicit `null` optionals back to absent so the
  handlers' `args.get(..., default)` defaulting is preserved
  (`src/lingtai/tools/notification/__init__.py:143-153`).
- `_check()` returns the dict-shaped placeholder onto which the turn loop can
  stamp the current notification payload
  (`src/lingtai/tools/notification/__init__.py:156-161`).
- `_adapt_manual_result()` flattens the shared ManualTool child's canonical
  `content`/`structuredContent` result to notification's pinned public
  `status`/`notification_manual`/`manual_path` shape, restating the exact
  contract-pinned degraded sentence
  (`src/lingtai/tools/notification/__init__.py:164-197`).
- `_dismiss_channel()` adapts a whole-channel request and retains the inner
  event/ref rejection as defense in depth behind the envelope's earlier,
  no-I/O rejection (`src/lingtai/tools/notification/__init__.py:200-239`).
- `_dismiss_event()` and `_dismiss_ref()` adapt targeted system-event removal
  while defaulting the channel to `system`
  (`src/lingtai/tools/notification/__init__.py:245-288`).
- `_add_hook()`, `_drop_hook()`, `_edit_hook()`, and `_list_hooks()` adapt the
  hook-registry actions and delegate to
  `lingtai.kernel.notifications.add_hook` / `drop_hook` / `edit_hook` /
  `list_hooks`, which validate manifests, enforce name/channel uniqueness, and
  write `.notification/hooks.json` through Store family 8
  (`src/lingtai/tools/notification/__init__.py:291-354`).
- `registry.INTRINSICS` registers `notification` as a mandatory intrinsic next
  to email, system, context, pad, lingtai, and soul
  (`src/lingtai/tools/registry.py:48-69`).

## Connections

- `BaseAgent._wire_intrinsics()` binds every registered intrinsic module's
  `handle()` into the agent tool surface
  (`src/lingtai/kernel/base_agent/__init__.py:783-796`).
- The turn loop calls `attach_active_notifications()` after ordinary tool
  results so `check` receives the canonical `_meta.agent_meta.notifications.attention` and
  `_meta.agent_meta.guidance.transient` stamp
  (`src/lingtai/kernel/base_agent/turn.py:1748-1764`;
  `src/lingtai/kernel/meta_block.py:2944`).
- All three dismissal handlers delegate to
  `lingtai.kernel.notifications.dismiss_channel(...,
  invoked_by="notification")`; Core owns allowlists, producer guards,
  stale-version checks, protected channels, post-molt acknowledgement, and
  targeted event/ref removal (`src/lingtai/kernel/notifications.py:785`).
  The four hook-registry handlers delegate to Core's
  `add_hook`/`drop_hook`/`edit_hook`/`list_hooks`, which own manifest
  validation, uniqueness, and the family-8 Store writes
  (`src/lingtai/kernel/notifications.py:284-393`).
- `Agent._install_intrinsic_manuals()` copies the kernel-shipped
  `system-manual` skill tree into the per-agent intrinsic library that the
  `manual` child reads through `tool_family.manual.build_manual_child` and the
  shared `tools/_manual.py::load_installed_manual` loader
  (`src/lingtai/agent.py:311-372`).
- `base_agent.tools._dispatch_tool()` injects `_tc_id` into every intrinsic's
  args; only `context.molt` consumes it, so `handle()` strips it before the
  closed envelope is validated (`src/lingtai/kernel/base_agent/tools.py:28-35`).
- `kernel/tool_result_summary.py::_LTP_V2_MIGRATED_FAMILIES` lists
  `notification`, so the advertised root `summarize` boolean is actually
  honored as the a-priori summary control rather than silently ignored
  (`src/lingtai/kernel/tool_result_summary.py:150-183`).
- The notification manual is the progressive-disclosure router for procedures;
  its channel-model and dismissal-safety children hold protocol and safety
  depth. The paired Contract defines the normative tool Port and invariants.

## Composition

- **Parent:** `src/lingtai/tools/` (see `src/lingtai/tools/ANATOMY.md`).
- **Generic infrastructure:** `src/lingtai/tools/tool_family/` supplies
  `ChildTool`/`ToolFamily` schema composition and envelope dispatch plus the
  reserved `manual` child. It is optional infrastructure this package composes
  onto, not a base class it inherits from; unlike `web`, which owns a
  per-Agent manager, this intrinsic builds its dispatching family per call
  because `agent` only arrives per call.
- **Core dependency:** `src/lingtai/kernel/notifications.py` and the notification
  Store behind it. The four hook-registry actions (`add`/`drop`/`edit`/`list`)
  mutate the Store's family-8 hook-manifest registry
  (`load_hook_manifests`/`update_hook_manifests`, `.notification/hooks.json`);
  the read and dismiss actions add no Store operation.
- **Turn-loop adapter:** `src/lingtai/kernel/base_agent/turn.py` completes the
  `check` placeholder with model-visible state.
- **Installed-resource adapter:** `src/lingtai/agent.py` installs the intrinsic
  skill tree consumed by `manual`.
- **Sibling ownership:** `system` retains `summarize`; producer tools retain
  their own canonical read/dismiss operations.

## State

- `_check()` is in-memory and write-free.
- The `manual` child reads one fixed installed text file and does not inspect or
  mutate `.notification/`, Notification Store state, producer state,
  fingerprints, acknowledgements, or notification logs.
- Envelope validation runs before any handler, so an unknown action, an unknown
  root field, or an `input` key belonging to another action fails with no
  notification I/O at all.
- Dismiss handlers own no state directly. Through notification Core they clear
  notification mirrors or remove targeted system events while leaving producer
  canonical state untouched.
- Hook-registry handlers own no state directly either. Through notification
  Core they read/mutate `.notification/hooks.json` (Store family 8) and refresh
  the module-level registered-hook channel mirror that widens the allow
  predicate; `drop` revokes the channel and `edit` moves it. Read-only `list`
  never mutates.

## Notes

- There is no aggregate `dismiss`, no `summarize` action, no source-checkout
  fallback, and no `system` notification/dismiss compatibility alias. The
  shared manual loader is now deliberately used (via the reserved `manual`
  child), replacing this package's former private path construction.
- The kernel may synthesize the same `notification(action="check")` call/result
  shape at an idle boundary; that delivery plumbing is not another agent-callable
  action (`src/lingtai/kernel/base_agent/__init__.py:1255-1461`;
  `src/lingtai/kernel/base_agent/__init__.py:1582-1844`). Because that pair is
  deliberately byte-shape-identical to a voluntary read, its synthesized call
  args carry the same minimal LTP v2 envelope (`action`, `input: {}`, and a
  `reasoning` string); the optional public `summarize` control is valid but
  absent here. No `injection_seq` or other internal freshness field is admitted,
  since a provider/model can copy assistant-turn call args verbatim into a new
  real call, and `_ROOT_FIELDS` rejects keys outside the public root allowlist
  with `INVALID_ARGUMENT: unsupported notification argument`. Freshness/novelty
  against byte-equality is carried on the result side (`content`/`metadata`)
  instead, which is never fed back as call args.
- Large tool results are ranked and compacted through
  `context(action="summarize")`. Notification dismissal retains only the legacy
  reminder escape hatch described by the manual.
- Changes to notification read/dismiss semantics must also check
  `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md`; changes to Port behavior
  must update the paired Contract and focused tests in the same PR.
