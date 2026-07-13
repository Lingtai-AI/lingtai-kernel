---
related_files:
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - src/lingtai/tools/registry.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/kernel/base_agent/turn.py
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
surface. It composes five actions: `check`, three atomic dismissal actions, and
the strictly read-only `manual` action. Notification Core owns mirror guards and
Store use; the tool owns only schema, dispatch, the check placeholder, argument
adaptation, and installed-manual retrieval.

## Components

- `schema.py` defines canonical-English registration prose and the ordered
  `check` / `dismiss_channel` / `dismiss_event` / `dismiss_ref` / `manual`
  action domain (`src/lingtai/tools/notification/schema.py:27-62`).
- `handle()` selects one of the five handlers and returns a structured error for
  unknown or absent actions (`src/lingtai/tools/notification/__init__.py:174-189`).
- `_check()` returns the dict-shaped placeholder onto which the turn loop can
  stamp the current notification payload
  (`src/lingtai/tools/notification/__init__.py:52-70`).
- `_manual()` constructs the fixed installed
  `.library/intrinsic/capabilities/notification-manual/SKILL.md`
  path, returns its UTF-8 body, or returns an explicit degraded envelope when
  the installed file is absent (`src/lingtai/tools/notification/__init__.py:73-97`).
- `_dismiss_channel()` adapts a whole-channel request and rejects event/ref
  targets (`src/lingtai/tools/notification/__init__.py:100-131`).
- `_dismiss_event()` and `_dismiss_ref()` adapt targeted system-event removal
  while defaulting the channel to `system`
  (`src/lingtai/tools/notification/__init__.py:134-171`).
- `registry.INTRINSICS` registers `notification` as a mandatory intrinsic next
  to email, system, psyche, and soul (`src/lingtai/tools/registry.py:45-53`).

## Connections

- `BaseAgent._wire_intrinsics()` binds every registered intrinsic module's
  `handle()` into the agent tool surface
  (`src/lingtai/kernel/base_agent/__init__.py:783-796`).
- The turn loop calls `attach_active_notifications()` after ordinary tool
  results so `check` receives the canonical `_meta.notifications` and
  `_meta.notification_guidance` stamp
  (`src/lingtai/kernel/base_agent/turn.py:1748-1764`;
  `src/lingtai/kernel/meta_block.py:2944`).
- All three dismissal handlers delegate to
  `lingtai.kernel.notifications.dismiss_channel(...,
  invoked_by="notification")`; Core owns allowlists, producer guards,
  stale-version checks, protected channels, post-molt acknowledgement, and
  targeted event/ref removal (`src/lingtai/kernel/notifications.py:469`).
- `Agent._install_intrinsic_manuals()` copies the kernel-shipped
  `system-manual` skill tree into the per-agent intrinsic library that
  `_manual()` reads (`src/lingtai/agent.py:311-372`).
- The notification manual is the progressive-disclosure router for procedures;
  its channel-model and dismissal-safety children hold protocol and safety
  depth. The paired Contract defines the normative tool Port and invariants.

## Composition

- **Parent:** `src/lingtai/tools/` (see `src/lingtai/tools/ANATOMY.md`).
- **Core dependency:** `src/lingtai/kernel/notifications.py` and the notification
  Store behind it. This tool does not add a Store operation.
- **Turn-loop adapter:** `src/lingtai/kernel/base_agent/turn.py` completes the
  `check` placeholder with model-visible state.
- **Installed-resource adapter:** `src/lingtai/agent.py` installs the intrinsic
  skill tree consumed by `manual`.
- **Sibling ownership:** `system` retains `summarize`; producer tools retain
  their own canonical read/dismiss operations.

## State

- `_check()` is in-memory and write-free.
- `_manual()` reads one fixed installed text file and does not inspect or mutate
  `.notification/`, Notification Store state, producer state, fingerprints,
  acknowledgements, or notification logs.
- Dismiss handlers own no state directly. Through notification Core they clear
  notification mirrors or remove targeted system events while leaving producer
  canonical state untouched.

## Notes

- There is no aggregate `dismiss`, `summarize`, source-checkout fallback, shared
  manual loader, or `system` notification/dismiss compatibility alias.
- The kernel may synthesize the same `notification(action="check")` call/result
  shape at an idle boundary; that delivery plumbing is not another agent-callable
  action (`src/lingtai/kernel/base_agent/__init__.py:1255-1461`;
  `src/lingtai/kernel/base_agent/__init__.py:1562-1800`).
- Large tool results are ranked and compacted through
  `system(action="summarize")`. Notification dismissal retains only the legacy
  reminder escape hatch described by the manual.
- Changes to notification read/dismiss semantics must also check
  `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md`; changes to Port behavior
  must update the paired Contract and focused tests in the same PR.
