---
name: task_card-manual-settings
description: >
  Focused Task Card settings reference for the read-only SHOW inventory,
  owner-document validation, cadence, safety ceilings, migration, and body cap.
version: 0.1.0
last_changed_at: "2026-09-09T00:00:00Z"
tags: [lingtai, task-card, settings, cadence, ceilings, migration]
related_files:
- src/lingtai/tools/task_card/manual/SKILL.md
- src/lingtai/tools/task_card/__init__.py
- src/lingtai/tools/task_card/CONTRACT.md
maintenance: |
  Tracks the five Task Card owner policies and their SHOW/change procedure;
  update with the owner manual and contract when defaults, validation, or seams change.
---

# Task Card settings and cadence

## SHOW only

Call `task_card(action="settings", input={}, reasoning="inspect Task Card settings")`.
It returns exactly five rows, in this order: `interval_s`, `timeout_s`,
`max_refreshes`, `reminder_turns`, `max_body_chars`. Each row contains only
`key`, `current`, `default`, `configurable`, and `comment`. SHOW reads effective
values; it does not write, migrate, or provide a set/reset form. An unavailable
inventory fails as a whole, with no partial rows or raw exception detail.

A configurable row permits an authorized owner edit of `taskcard/taskcard.json`
outside SHOW, preserving sibling fields. After that edit, call SHOW again. All
numeric fields reject booleans. Paths, body/status/watch contents, notification
state, and unknown owner fields are never projected. Invalid or missing fields
fall back independently to their built-in defaults.

## interval-s

Polling cadence for a new/resumed watch. Valid owner value: finite number `>= 1`;
default `5`. An explicit `start.interval_s` obeys only that floor (slower values
are honored). A running watch keeps its captured cadence; resume carries the
stored cadence rather than substituting a new default.

## timeout-s

One renderer execution ceiling, not total watch lifetime. Valid owner value:
finite number `>= 0.1`; default `10`. Omitted `start.timeout_s` uses the ceiling;
an explicit value may lower it and is capped at that ceiling when larger. A live
watch keeps its captured ceiling; resume re-clamps its stored value to current
owner policy.

## max-refreshes

Refresh ceiling for a new/resumed watch. Valid owner value: positive integer;
default `2000`. An explicit value may lower but never exceed the configured
ceiling. Before the intrinsic document exists, SHOW may preview a genuinely
customized legacy Telegram ceiling, but does not write it.

## reminder-turns

Completed text turns between absent/stale reminders. Valid owner value: positive
integer; default `10`. It is read at each completed-text-turn seam.

## max-body-chars

Maximum rendered body accepted by the producer. Valid owner value: integer `>=100`;
default `2000`. It is read on each publication. Oversized output is refused, never
truncated, and the last valid body remains on disk. The separate resident
projection still caps text at 2000 characters; see [projection](notifications.md).

## Resolution and one-way migration

The five fields resolve independently from `taskcard/taskcard.json`; malformed
siblings do not discard valid values. The first resolution when that intrinsic
file does not exist may read `telegram/taskcard.json` only to carry forward a
valid customized positive `max_refreshes` different from the retired design's
untouched default `1000`. It then writes the intrinsic document whether anything
migrated or built in. Later legacy changes are never consulted. If the intrinsic
file already exists but is malformed, it remains the sole owner and fields fall
back to built-ins.

Thus `interval_s` cannot be below `1`, `timeout_s` cannot be below `0.1`, counts
stay positive, and explicit timeout/refresh requests cannot exceed their configured
ceilings.
