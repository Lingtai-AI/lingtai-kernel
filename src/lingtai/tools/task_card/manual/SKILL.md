---
name: task_card-manual
description: >
  Read when a Task Card needs a truthful renderer, lifecycle recovery, settings,
  or projection boundary; routine schema-sufficient calls can use the action directly.
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/task_card/__init__.py
- src/lingtai/tools/task_card/ANATOMY.md
- src/lingtai/tools/task_card/CONTRACT.md
- src/lingtai/tools/task_card/manual/reference/lifecycle.md
- src/lingtai/tools/task_card/manual/reference/settings.md
- src/lingtai/tools/task_card/manual/reference/notifications.md
- src/lingtai/kernel/tool_plugin/CONTRACT.md
maintenance: |
  Keep this entry aligned with the intrinsic task_card action/settings surface,
  exact taskcard/status and taskcard/taskcard.md paths, one-watch lifecycle, and
  the focused references it routes to. Update it with the paired Anatomy/Contract
  whenever those contracts or the renderer boundary change.
---

# task_card manual

`task_card` is one channel-neutral producer per agent: it writes the full body to
`taskcard/taskcard.md` and exact `active`/`inactive` to `taskcard/status`.

## First action

Routine schema-sufficient calls do not need this manual. For meaningful
long-running, multi-step, or parallel work, start a truthful watch:

```python
task_card(action="start", input={"renderer_path": "renderer.py"},
          reasoning="publish truthful progress for the long-running work")
```

Use `action`, strict action `input`, and root `reasoning`; root `summarize` is
optional presentation control (leave it false for exact manual text or paths).
`renderer_path` must resolve after symlink resolution to an existing regular
Python file inside the working directory. It must exit successfully with
nonempty stdout, which is the full body. Over-limit output is refused, not
truncated; the producer atomically writes the body before exact `active`. Only
one watch is allowed. Skip quick single-step or ritual work and any renderer
that cannot stay truthful.

## Lifecycle decisions

Use the schema for action fields; `inspect` reports current watch/body/error.
`stop` pauses and preserves the body; `remove`
retires then deletes it after completion, cancellation or abandonment. Retry a
failed stop/remove once quiescent; never bypass it with Shell/File deletion.
Start a new watch when expired work continues. Read [lifecycle](reference/lifecycle.md)
for shutdown/resume and stale-state caveats.

## Settings anchors

`settings` is read-only: five numeric policies in `taskcard/taskcard.json`, not a
writer or migration trigger. Edit that document only through its authorized
owner procedure, preserving siblings, then SHOW again. See the anchors below.

### interval-s
See [interval-s](reference/settings.md#interval-s).

### timeout-s
See [timeout-s](reference/settings.md#timeout-s).

### max-refreshes
See [max-refreshes](reference/settings.md#max-refreshes).

### reminder-turns
See [reminder-turns](reference/settings.md#reminder-turns).

### max-body-chars
See [max-body-chars](reference/settings.md#max-body-chars).

## Focused routes

- [lifecycle and recovery](reference/lifecycle.md) — ordering, retry/error,
  stop/remove, restart resume, expiry, and truthful renderer use.
- [settings and cadence](reference/settings.md) — SHOW, five policies,
  validation floors/ceilings, and one-way legacy bootstrap.
- [notifications and projection](reference/notifications.md) — typed events,
  reminders, resident projection, and consumer limits.

## Truth boundary

Report only evidence the renderer can read; never fabricate progress or claim a
consumer sent, edited, retried, or delivered the card. Task Card owns artifacts
and typed notifications, not transport IDs, layouts, transport retries, or
another channel's display guarantee.
