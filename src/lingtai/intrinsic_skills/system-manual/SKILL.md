---
name: system-manual
description: >
  Second-layer router for LingTai's progressive-disclosure operating manuals.
  Read this when resident substrate/procedures are too compact and you need the
  right lower reference. Route here for the expanded substrate/runtime model,
  lifecycle and `system` tool actions, the init.json composition and preset
  runtime model route, action/procedure discipline and skill routing, tool-result
  summarization, SQLite/`log.sqlite` trace inspection and trajectory mining,
  runtime/kernel update checks and nudges, environment variables, goal
  notifications, molt/memory questions, MCP/addon ownership, collaboration
  topology, and resident prompt design. The nested catalog below names each
  reference and its exact trigger.
version: 1.8.0
tags: [lingtai, agent, runtime, procedures, substrate, system, lifecycle, memory, communication, skills, molt, summarize, nudge, updates, runtime-checks, preset]
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/prompts/substrate/substrate.md
- src/lingtai/prompts/procedures/procedures.md
- src/lingtai/intrinsic_skills/system-manual/reference/substrate-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/procedures-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
- src/lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md
- src/lingtai/kernel/nudge/ANATOMY.md
- src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# System Manual — Progressive Disclosure Router

`system-manual` is the second layer of LingTai operating guidance. The resident
`substrate` and `procedures` prompts keep only the short rules every agent must
hold constantly. This skill routes from those compact rules to the reference node
that carries the actual detail.

Use this file first when the question is about LingTai's agent runtime, resident
prompt design, lifecycle, memory, communication, tool routing, system operations,
runtime trace inspection, runtime/kernel update checks, or nudge handling. Then open the referenced lower node.


## Nested reference catalog

`system-manual` owns the following nested skill-references. Their frontmatter is
kept here so the router advertises lower nodes without promoting them to
standalone top-level skills. Open the listed `SKILL.md` when the router table
selects that topic.

```yaml
- name: substrate-manual
  location: reference/substrate-manual/SKILL.md
  description: |
    Nested system-manual reference for the expanded substrate/runtime model:
    body/extensions, lifecycle states, `system` tool actions, communication and
    notification discipline, memory layers and the molt model, collaboration
    topology, MCP/addon ownership, idle/soul, preset tiers, and (§11) the
    canonical `init.json` composition and preset runtime model.
- name: procedures-manual
  location: reference/procedures-manual/SKILL.md
  description: |
    Nested system-manual reference for expanded action discipline: progressive
    disclosure, responsiveness, external side-effect authorization, the daemon
    workflow methodology, depositing work into pad/knowledge/skills/character,
    idle/lifecycle procedure, skill routing, HTML deliverables, artifact
    sharing, and issue reporting.
- name: summarize-manual
  location: reference/summarize-manual/SKILL.md
  description: |
    Nested system-manual reference and canonical owner of tool-result
    summarization: the three compression modes (a-priori `summary=true`,
    a-posteriori `system(action="summarize")`, molt), summarize cadences,
    delayed provider reconstruction and the 0.85/1.0 rebuild boundaries,
    original-result recovery by `tool_call_id`, and summarize versus molt.
- name: sqlite-log-query
  location: reference/sqlite-log-query/SKILL.md
  description: |
    Nested system-manual reference for SQLite/`log.sqlite` runtime trace
    inspection and trajectory/anomaly mining: `lingtai-agent log doctor`,
    `lingtai-agent log query`, `lingtai-agent log rebuild`, JSONL
    source-of-truth rules, read-only SQL safety, offline rebuild/WAL caveats,
    the events/chat_entries/token_entries schema, query recipes, cheap-model
    daemon strategy, finding schema, digests, redaction rules, and
    event_summary.py.
- name: runtime-update-checks
  location: reference/runtime-update-checks/SKILL.md
  description: |
    Nested system-manual reference for the kernel update/nudge lifecycle:
    runtime and source discovery, `kernel_version` and `source_drift`,
    heartbeat dispatch, `.notification/nudge.json` envelopes, sync/wake/dismiss,
    packaged versus editable/source runtimes, installer ownership and human
    confirmation, refresh boundaries, and read-only diagnosis.
- name: environment-variables
  location: reference/environment-variables/SKILL.md
  description: |
    Nested system-manual reference cataloguing every LingTai environment
    variable: purpose, default, accepted values, scope, read point,
    reload/restart behavior, invalid-value handling, implementation anchor,
    and security caution.
- name: goal-manual
  location: reference/goal-manual/SKILL.md
  description: |
    Nested system-manual reference for goal notifications: protected
    `.notification/goal.json` as the active-goal source of truth, `/goal`
    guided setup, recommended fields, idle goal reminders, and
    cancellation/completion semantics.
```

## Router table

| Need / keywords | Read |
|---|---|
| Expanded substrate; body/extensions; bash vs daemon vs avatar vs MCP; lifecycle states; ACTIVE/IDLE/ASLEEP/SUSPENDED; same-channel communication; basic notifications; memory layers; molt model; idle/soul; preset tiers; `system` operations | `reference/substrate-manual/SKILL.md` |
| `init.json` composition/owner map; preset runtime model; raw vs resolved `system/manifest.resolved.json`; preset identity/path; TUI/library discovery vs `system(action="presets")` allowed-only catalog; main-agent swap/revert/refresh; daemon `tasks[].preset` explicit/omitted path; external CLI backend preset skip | `reference/substrate-manual/SKILL.md` §11 |
| Expanded procedures; progressive disclosure; writing skills/knowledge; action discipline; responsiveness; skill routing; HTML deliverables; artifact sharing; issue reporting; when to read which manual | `reference/procedures-manual/SKILL.md` |
| Tool-result summarization; large-result ranking via agent_meta; progressive disclosure of raw outputs; original-result recovery; summarize vs molt | `reference/summarize-manual/SKILL.md` |
| SQLite; `log.sqlite`; LingTai runtime logs; JSONL traces; `lingtai-agent log doctor`; `lingtai-agent log query`; `lingtai-agent log rebuild`; events/chat_entries schema; daemon/chat-history trace indexing; WAL/live-read caveats; SQL recipes; trajectory/anomaly mining; improvement digests; cheap-model strategy | `reference/sqlite-log-query/SKILL.md` |
| Notifications; direct `notification(action='manual')`; check/dismiss_channel/dismiss_event/dismiss_ref/manual; `.notification/<channel>.json`; channel allowlist; top-level `instructions`; protected channels; generic vs producer dismiss; stale-version/force; legacy `large_tool_result` dismiss | `notification-manual` |
| Kernel update lifecycle; runtime/source discovery; `kernel_version` and `source_drift`; heartbeat nudge dispatch; `.notification/nudge.json`; durable state; sync/wake/dismiss mechanics; packaged vs editable/source installs; refresh vs TUI-managed update; verification/troubleshooting | `reference/runtime-update-checks/SKILL.md` |
| Environment variables; Nudge controls; accepted values; read/reload behavior; invalid-value fallback; security cautions | `reference/environment-variables/SKILL.md` |
| Goal notifications; `.notification/goal.json`; active goal source of truth; goal `instructions`; idle goal reminder; cancel/complete goal | `reference/goal-manual/SKILL.md` |
| Molt mechanics, pad tending, session journals, post-wipe recovery | `psyche-manual` |
| Soul tool; soul flow opt-in (`LINGTAI_SOUL_FLOW_ENABLED`); disabled-flow behavior; `delay_seconds` as cadence-not-off-switch; inquiry/config/voice/dismiss; privacy/cost rationale | `soul-manual` |
| Authoring/publishing skills or changing skill catalog behavior | `skills-manual` |
| Knowledge-entry layout and private durable memory | `knowledge-manual` |
| MCP registration/activation/addon ownership | `mcp-manual` |
| Bash/cron/host scheduling details | `shell-manual` |
| Daemon lifecycle/inspection/debugging | `daemon-manual` |
| Avatar spawning/management/escalation | `avatar-manual` |
| Kernel architecture/code truth | `lingtai-kernel-anatomy`, then cited code |

## How to choose between resident prompt, this router, and references

- If the resident prompt already answers the question, act.
- If the resident prompt names a broad system/runtime/procedure topic, read this
  router to choose the lower reference.
- If this router names a reference, read that reference before improvising.
- If a reference points to anatomy/code/tests, descend there for ground truth.

## Substrate and procedures are separate on purpose

`substrate` describes what an agent *is* and how the runtime behaves;
`procedures` describes how an agent *acts*. Keep the resident prompt compact,
keep this file a router, and put detailed explanations in the nested references
above rather than collapsing them back into one monolithic body.

## Maintaining this router

When resident substrate/procedures gain new concepts, add a routing hint here and
put detail in a nested reference. When a reference grows too large or needs
companion scripts and assets, split it into another `reference/<name>/SKILL.md` folder and list
its frontmatter summary in both this nested reference catalog and the router
table. Keep this file short enough to scan.
