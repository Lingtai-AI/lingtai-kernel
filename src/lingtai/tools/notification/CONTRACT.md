---
name: notification-tool
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
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
surface. It exposes four operational actions for reading or atomically clearing
notification mirrors plus one strictly read-only `manual` action for progressive
disclosure. It owns no producer state and introduces no Notification Store
operation.

## Behavior

LingTai agents MUST use `manual` only to retrieve installed guidance, `check` to
request current notification state, and the narrowest producer-specific or
atomic dismiss action after handling a notification. They MUST NOT treat generic
dismissal as mutation of producer canonical state, bypass protected channels, or
route large-result compaction through this tool.

Coding agents MUST preserve all four operational actions, Store semantics,
notification Core guards, producer state, and the absence of `system`
notification/dismiss aliases. They MUST keep `manual` read-only, fixed to the
installed per-agent path, and independent of check/dismiss delivery state.
Procedures and safety explanations live in the linked notification manual and
nested references rather than in this contract.

## Port

The inbound agent-tool Port is named `notification`. Its input is an object with
required canonical-English `action` and optional dismiss fields `channel`,
`force`, `event_id`, `ref_id`, and `reason`. The action domain, in order, is:
`check`, `dismiss_channel`, `dismiss_event`, `dismiss_ref`, `manual`.

Observable action contracts are:

- `check` returns `{_notification_placeholder: true, message}`; the turn-loop
  adapter may stamp `_meta.notifications` and `_meta.notification_guidance` onto
  that same dict.
- `dismiss_channel` requires `channel`, rejects event/ref targets, and delegates
  a whole-mirror clear to notification Core.
- `dismiss_event` requires `event_id`; `dismiss_ref` requires `ref_id`; each
  defaults `channel` to `system` and delegates targeted removal to Core.
- `manual` reads only
  `<agent>/.library/intrinsic/capabilities/notification-manual/SKILL.md`.
  Success contains exactly `{status: "ok", notification_manual, manual_path}`.
  Absence contains exactly `{status: "degraded", notification_manual: "",
  manual_path, error}`, where `error` is `notification manual missing —
  initializer may have failed or capability not installed correctly`. Other
  filesystem/decoding errors propagate.
- Unknown or absent actions return `{status: "error", message}` naming the
  unknown notification action.

There is no aggregate `dismiss`, no `summarize`, no `items` property, no source
checkout fallback, and no compatibility alias.

## Adapters

`lingtai.tools.registry.INTRINSICS` is the composition wiring that installs the
package as a mandatory tool. `handle()` is the driving dispatch adapter for the
five actions. The turn-loop notification post-hook completes `check` with the
single canonical model-visible payload. The three dismiss handlers adapt tool
arguments into `lingtai.kernel.notifications.dismiss_channel(...,
invoked_by="notification")`, where notification Core owns allowlists, guards,
stale checks, protected channels, acknowledgement policy, and Store use.

`_manual` is a bounded installed-resource adapter: it performs one `is_file`
check and one UTF-8 read at the fixed path. It does not call notification Core,
`NotificationStorePort`, the post-hook, a producer, or a shared loader. Agent
initialization copies the bundled first-level `notification-manual` skill tree
into the installed per-agent intrinsic library.

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
- `system` owns `summarize` and exposes no notification/dismiss alias. The
  notification tool owns no producer publication action.
- Schema descriptions are canonical English and language-independent. Action
  identifiers and properties have no localized aliases. All three owned
  glossaries require review when this enum changes; the additive `manual` action
  introduces no new localized concept.
- Adding `manual` is additive, so `contract_version` remains `1`; a future
  breaking Port change follows the root version rule.

## Contract tests

`tests/test_notification_tool.py` proves mandatory registration and wiring, the
ordered five-action schema, canonical description, absent aggregate actions,
manual success/degraded envelopes and fixed path, read-only state/log behavior,
check placeholder shape, all atomic dismiss semantics, Core guards, and absence
of system compatibility aliases. `tests/test_system_dismiss.py` protects shared
operational dismissal behavior. `tests/test_tools_package_data.py` verifies tool
and documentation package data. Architecture, Anatomy drift, glossary, and skill
validators cover the linked document and manual graphs.

## Maintenance

Read the paired Anatomy for current symbol locations, wiring, composition, state,
and verified citations. Keep implementation, schema, registry wiring, focused
tests, glossaries, and the manual/reference graph synchronized. Do not duplicate
manual procedures here or expand this slice into Store, producer, system, or
summarization changes.
