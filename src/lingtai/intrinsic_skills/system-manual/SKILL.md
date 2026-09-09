---
name: system-manual
description: >
  Short router for runtime, lifecycle, identity, refresh transactions, presets,
  settings, update/mismatch diagnosis, LLM adapters, and operating procedures.
version: 1.23.0
last_changed_at: "2026-09-09T00:00:00Z"
tags: [lingtai, agent, runtime, procedures, substrate, system, lifecycle, alarm, memory, communication, skills, settings, molt, summarize, nudge, updates, refresh, preset, llm, adapters, codex, websocket]
related_files:
- src/lingtai/prompts/substrate/substrate.md
- src/lingtai/prompts/procedures/procedures.md
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/tools/system/karma.py
- src/lingtai/tools/system/schema.py
- src/lingtai/tools/system/CONTRACT.md
- src/lingtai/tools/system/ANATOMY.md
- src/lingtai/tools/system/settings.py
- tests/test_system_declared_plugin.py
- src/lingtai/kernel/nudge/ANATOMY.md
- src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/refresh-precheck/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/external-attach-diagnostic/SKILL.md
- src/lingtai/llm/_register.py
- src/lingtai/llm/openai/adapter.py
- src/lingtai/intrinsic_skills/system-manual/reference/tool-plugin-settings/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/settings-inventory/SKILL.md
- tests/test_skills.py
maintenance: |
  Tracks the routed source/resources it summarizes; keep refresh-precheck as the
  single refresh-transaction owner and runtime-update-checks as the update,
  source, install, nudge, and mismatch owner.
---

# System Manual — Task Router

`system-manual` routes System's lifecycle, identity, presets, settings, and peer
controls. Schema owns action names, fields, and types;
read one route below for unfamiliar or consequential work.

## Task routes

| Task | Read |
|---|---|
| Runtime/lifecycle, alarms, MCP, memory, presets, or peer recovery | [substrate manual](reference/substrate-manual/SKILL.md) |
| Action discipline, authorization, collaboration, or deliverables | [procedures manual](reference/procedures-manual/SKILL.md) |
| Any refresh transaction: same-runtime reload, preset swap/revert, source/venv cutover, or failure recovery | [refresh pre-check](reference/refresh-precheck/SKILL.md) |
| Update/source/install/nudge/mismatch diagnosis and cutover handoff, including `source_drift` | [runtime update checks](reference/runtime-update-checks/SKILL.md) |
| Settings and SHOW ownership | [settings inventory](reference/settings-inventory/SKILL.md) |
| Environment variables and runtime controls | [environment variables](reference/environment-variables/SKILL.md) |
| Provider transport and adapter behavior | [LLM adapters](reference/llm-adapters/SKILL.md) |
| SQLite traces and log queries | [SQLite log query](reference/sqlite-log-query/SKILL.md) |
| Trajectory mining and recurring patterns | [trajectory mining](reference/trajectory-mining/SKILL.md) |
| Goal lifecycle and goal records | [goal manual](reference/goal-manual/SKILL.md) |
| Physical address/workdir rename | [physical rename](reference/how-to-change-name/SKILL.md) |
| External attach diagnostics | [external-attach diagnostic](reference/external-attach-diagnostic/SKILL.md) |
| ToolFamily/plugin settings | [ToolFamily settings](reference/tool-plugin-settings/SKILL.md) |
| Context, notification, MCP, shell, daemon, avatar, soul, skills, or knowledge | Read that owning tool's manual; System does not own its procedure. |

## Everyday use

Use `action` with only that action's `input` fields and a short `reasoning`;
`summarize` is optional. Fetch the installed manual with
`system(action="manual", input={})` and keep it unsummarized. `settings` is
read-only and its `comment` points to the owner; `presets` is allowed-only, so
pass an exact returned path. Availability is not authority. Normal waiting is
IDLE; positive `sleep.delay` is only a last-resort alarm without a reliable
completion notification, and pending notifications require intentional `force`.
`name_set` is immutable, `name_nickname` mutable, and neither renames the
address/workdir. `notification` owns reads/dismissals; `context` owns
summarize/rebuild/molt and provider replay.

## Consequential use

- **Refresh/preset:** `reference/refresh-precheck/SKILL.md` is the single
  owner of same-runtime reload, preset swap/revert, source/venv cutover, and
  failure recovery: one targeted preflight, exactly one refresh, and one
  targeted receipt. Route update/source/install/nudge/mismatch diagnosis through
  `reference/runtime-update-checks/SKILL.md` first. Refresh never installs,
  upgrades, fetches, or repairs code.
- **Peer recovery:** use the exact target working-directory address within authority and
  diagnose/communicate first. `lull`, `interrupt`, `suspend`, `cpr`, and `clear`
  require `admin.karma=True`; `nirvana` also requires `admin.nirvana=True` and
  permanently destroys the target. Read `reference/substrate-manual/SKILL.md`.

### Cache-miss budget

`system-manual#cache-miss-budget` remains an alias for
`reference/settings-inventory/SKILL.md#cache-miss-budget`; query SHOW first.

### Runtime policy (v2)

`system-manual#runtime-policy-v2` remains an alias for
`reference/settings-inventory/SKILL.md#runtime-policy-v2-document-shape`.
