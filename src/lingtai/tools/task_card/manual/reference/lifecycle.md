---
name: task_card-manual-lifecycle
description: >
  Focused Task Card lifecycle reference for renderer gates, truthful progress,
  artifact ordering, restart resume, and stop/remove distinction.
version: 0.1.0
last_changed_at: "2026-09-09T00:00:00Z"
tags: [lingtai, task-card, renderer, lifecycle, progress, restart]
related_files:
- src/lingtai/tools/task_card/manual/SKILL.md
- src/lingtai/tools/task_card/__init__.py
- src/lingtai/tools/task_card/ANATOMY.md
- src/lingtai/tools/task_card/CONTRACT.md
maintenance: |
  Tracks the Task Card renderer and lifecycle procedure; update with the owner
  manual and contract when artifact ordering, path gates, or watch recovery changes.
---

# Task Card lifecycle and truthful producer use

## Use a watch when it helps

Start proactively for meaningful long-running, multi-step, or parallel work when
a durable view helps a human follow it. Skip quick single-step or ritual updates.
Keep the watch only while its renderer is truthful and current. If the refresh
ceiling expires while work continues, start a new watch.

## Owned artifacts and renderer gate

The producer owns `taskcard/taskcard.md` (full body), exact `taskcard/status`,
`taskcard/watch.json` (active-watch restart descriptor), and its agent-wide
`taskcard/taskcard.json` policy. It does not own transport IDs or consumer state.

`renderer_path` must resolve to an existing regular Python file inside the agent
working directory. The producer runs `sys.executable <renderer>` with that
working directory as cwd; only exit `0` with non-empty stdout succeeds. Stderr is
not the body. The configured body cap is refusal, never truncation.

`start` runs the renderer first, atomically publishes the complete body, writes
exact `active`, then starts the updater and saves the descriptor. A second start
fails closed. `retry` atomically replaces only the body; status stays `active`
until the refresh budget is exhausted. A non-exhausting renderer/publication
failure keeps the last valid body and records producer error; success clears it.
The final exhausted attempt retires the watch and emits the limit event.

## Stop, remove, and resume

- `inspect` only reports the current watch, paths, status, body, and error.
- `stop` writes `inactive` before asking the updater to stop, joins it, clears
  the descriptor, and preserves the body. A successful stop releases the watch
  handle; continuation uses a new start, not retry of the stopped id. If the thread remains alive, report a
  retryable failure and leave the watch retryable.
- `remove` has empty input: it retires the one watch as `stop` does, waits for
  quiescence, then deletes `taskcard/taskcard.md`. It leaves exact `inactive`,
  clears restart state, blocks rather than deleting during a live watch, and is
  idempotent when no body exists. Never delete the body with Shell or File.
- Agent shutdown writes `inactive`, stops the thread, and preserves the descriptor
  with its remaining refresh budget unless deliberately stopped, removed, or
  exhausted. Setup resumes the same watch id, renderer, cadence, ceilings, and
  remaining budget, with timeout/refresh ceilings clamped to current owner policy.
  Missing, corrupt, escaped, gone, or exhausted descriptors do not resume a watch.
  The current discard path clears the descriptor but can leave an existing
  `active` status file unchanged: inspect actual artifacts; do not treat that
  status alone as a live updater or assume discard settled it to `inactive`.
  This is an existing implementation limitation, not permission to delete state.
  A transient renderer failure during valid resume keeps the old body and lets
  the live watch retry.

## Truthful progress

Read the underlying work state and include only evidence available to the
renderer. Do not claim that a consumer sent, edited, retried, or delivered the
card; consumers only read the producer artifacts and apply their own rules.
