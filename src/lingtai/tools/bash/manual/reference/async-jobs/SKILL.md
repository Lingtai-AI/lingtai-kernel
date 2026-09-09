---
name: bash-async-jobs
description: >
  Nested shell-manual reference for durable asynchronous shell jobs: detached
  daemon routing, reminder/completion wakeups, relaunch-safe polling,
  cancellation, and shell-side coding-CLI supervision.
version: 1.1.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/_async_supervisor.py
- src/lingtai/tools/bash/_async_process.py
- src/lingtai/tools/bash/_state_lock.py
- src/lingtai/tools/task_card/manual/SKILL.md
maintenance: |
  Tracks durable async shell jobs and the shell-side coding-CLI topic; update
  this reference and its parent router when that lifecycle or routing changes.
---

# Async shell jobs

Open this after the router for a background command, coding CLI, completion or
reminder wake, relaunch recovery, cancellation, or detached-daemon supervision.
The engine and process adapters own execution; this page owns agent procedure.

## Start and follow up

A command that may run for minutes must not block the turn. Keep `working_dir`
inside the granted sandbox; for an external checkout use an explicit `cd` in the
command. Use the registered envelope, keep the returned ID, and use `poll` only
after completion/reminder notification or a genuine health check:

```text
shell(action="run", input={"command": "<long command>", "async": true,
                             "reminder": 1800}, reasoning="start it")
shell(action="poll", input={"job_id": "job-<id>"}, reasoning="check it")
shell(action="cancel", input={"job_id": "job-<id>"}, reasoning="stop it")
```

Run-only fields stay in `run.input`; `poll` and `cancel` take only `job_id`.
If `_advisory` says a poll already executed, stop tight polling: handle
messages, do other work, or wait for a state-change trigger. A channel-neutral Task Card is optional;
read `task_card(action="manual", input={})` before operating a watcher. Shell creates no watcher.

## Detached-daemon route

A selected detached daemon has no Agent notification store, mailbox, heartbeat,
or `.notification` route. Its jobs live under that run's private
`<run>/shell-jobs`, while command cwd remains the granted parent workdir. A
reminder/completion is a bounded same-run prompt event only while the daemon is
live; it is not a parent wake or `daemon_common` checkpoint. Queue-full
admission is retried by the live manager with capped backoff. Events carry a job
ID, not stdout/stderr; poll for exact output at the next safe model-send
boundary. Read `daemon-manual` for daemon lifecycle and recovery.

## Reminder, completion, and health

`reminder` is a last-resort wake delay chosen for the expected duration (default
1800 seconds), not proof of completion. Durable startup and `return_handoff`
leases prevent a relaunch from publishing the crash fallback while the first
manager is before supervisor spawn or before its successful return transition.
A successful run arms `returned_at + reminder`; if that bounded transition
expires, Shell returns a pollable recovery error with the durable ID/PID rather
than false success.

A due non-terminal job publishes `bash.reminder:<job_id>` to
`.notification/system.json`; exact supervisor terminal commit suppresses that
watchdog and publishes authoritative completion to `.notification/bash.json`.
These refs provide bounded/current-sink deduplication, not global exactly-once
delivery. A sink write may remain as historical evidence after a later commit.
This reminder is distinct from `.notification/cron.json` workflow reminders.

When a reminder fires, poll once and check that logs/output are growing, the
recorded process is alive, and work is not waiting for an interactive prompt or
provider error. If there is no progress, diagnose it; cancel or change path only within task
authority and report the blocker;
do not keep waiting by reflex.

## Relaunch-safe status and cancellation

A missing command PID is not terminal proof while the recorded supervisor may
still commit. Poll keeps the job recoverably `running` until bounded lease or
supervisor-loss evidence proves otherwise. Legacy live-PID records stay
uncancellable because incarnation cannot be proved; after death, one poll may
return `exit_status_known: false` and `exit_code: null`. Never invent `-1`, an
exit code, completion, or cancellation.

Cancellation is a durable supervisor request: TERM, bounded KILL escalation,
then exact terminal commit. It reports `cancelled` only after that commit.
A cancellation timeout/error remains pollable and remindable; do not call it
stopped or retry the underlying command without reconciling its outcome.
Terminal poll and successful cancel are atomic one-shot consumers; the durable
record and logs remain for evidence. Later consumers receive `Job already
finished`. New IDs use full UUID4 hex; eight-hex IDs are legacy read support.

## Coding-CLI baseline

Before relying on any installed coding CLI, run its own `--help`; flags and
parser behavior belong to `daemon-manual` → `reference/cli-backends/SKILL.md`.
Choose a CLI for an answer/transcript or exact model/flag; choose a daemon for
parallel work, a branch/diff, or work likely to outlive the turn. A CLI has no
LingTai job protocol: `async` is the Shell/OS wrapper that owns timeout, logs,
cancellation, and recovery. Keep inline calls short, checkpoint worktree and
recovery instructions before dispatch, and prefer several bounded calls to one
monolith. For backend promotion criteria, use the same daemon reference.
