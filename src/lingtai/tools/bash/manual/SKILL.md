---
name: shell-manual
description: >
  **Read before running a long-lived agent/coding CLI as a shell subprocess**,
  or before setting up cron/launchd/systemd timers or scheduled reminders.
  Routes shell-side async+poll supervision, host-scheduler setup, LingTai
  wake-by-mailbox-drop, one-shot reminders, and safe cleanup. Per-backend CLI
  operational detail (command shapes, flags, env contracts) for daemon-backed
  CLIs lives in `daemon-manual` → `reference/cli-backends/SKILL.md`.
version: 1.15.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/bash/__init__.py
- src/lingtai/tools/bash/_tool_family.py
- src/lingtai/tools/bash/CONTRACT.md
- src/lingtai/tools/bash/ANATOMY.md
- tests/test_shell_settings.py
- src/lingtai/tools/bash/manual/reference/async-jobs/SKILL.md
- src/lingtai/tools/bash/manual/reference/scheduled-work/SKILL.md
- src/lingtai/tools/bash/manual/reference/notification-reminders/SKILL.md
- src/lingtai/tools/bash/manual/reference/debugging-cleanup/SKILL.md
- src/lingtai/tools/task_card/manual/SKILL.md
- src/lingtai/tools/daemon/manual/SKILL.md
- src/lingtai/tools/daemon/shell_prompt_events.py
maintenance: |
  Tracks the shell capability's short operational router and its nested
  references; update the route and the owning reference when shell guidance
  changes.
---

# Shell manual — router

Use the schema for a short, deterministic command. Read one focused route before
long-lived/coding-CLI work, scheduling, an unfamiliar workflow, or recovery.
The selected dialect, policy, and working-directory sandbox remain boundaries.

## Route by task

| Task | Read one direct reference |
|---|---|
| Long command/CLI; `job_id`, `poll`, `cancel`, reminder, completion, relaunch, or async recovery | [async jobs](reference/async-jobs/SKILL.md) |
| Recurring or time-triggered work | [scheduled work](reference/scheduled-work/SKILL.md) |
| One future self-wakeup | [notification reminders](reference/notification-reminders/SKILL.md) |
| Silent, duplicated, failed, or retired scheduler | [debugging and cleanup](reference/debugging-cleanup/SKILL.md) |
| Unfamiliar dialect, working directory, timeout, or policy boundary | First success and Settings inventory below |
| Backend-specific CLI flags, environment, or parser behavior | `daemon-manual` → `reference/cli-backends/SKILL.md` |

## First success

1. For a bounded command, call `shell(action="run", input={"command": "..."},
   reasoning="...")` synchronously. Keep `working_dir` inside the sandbox; for
   an external checkout, leave it at the granted root and use
   `cd /absolute/path && ...` in the command. Dialect is fixed and policy still
   applies.
2. For work that may take minutes, set `input.async=true` and keep the returned
   `job_id`. On completion or a genuine health-check trigger, poll once for
   exact output; do not poll to stay active. Follow up with
   `shell(action="poll", input={"job_id": "..."}, reasoning="...")`; cancel only
   within authority with `shell(action="cancel", input={"job_id": "..."}, reasoning="...")`.
   A reminder means “may still be running,” not completion; cancellation is not cleanup.
3. Judge `exit_code`, `ok`, `command_status`, and `warning`; top-level `status`
   only records spawn/terminal handling, not inner command success. `status:
   "error"` means Shell could not run it. Prefer bounded `rg --files` and parse
   JSONL line by line.

Unknown or late async state stays unknown: never invent an exit code,
completion, or cancellation before durable terminal truth. Keep the job ID
pollable after lease/return failure. Completion is authoritative; a reminder is
fallback. Retain artifacts unless the human authorizes cleanup. Optional
progress is channel-neutral; use `task_card(action="manual", input={})`.
Shell creates no watcher.

## Settings inventory

Call `shell(action="settings", input={}, reasoning="inspect applied Shell
settings")` for read-only rows. The live schema/settings response owns values and
configurability; this section owns only procedures. SHOW has no set/reset
authority and an unavailable value fails the whole action without partial rows.

| Row | Meaning, source and authorized change |
|---|---|
| `shell_kind` | Setup-selected `posix`, `powershell`, `cmd`, `gitbash` or `wsl`; valid capability value wins, then case-insensitive `LINGTAI_SHELL`, then platform discovery. Invalid values fall through; default is null because discovery has no universal shell. Change the capability/launcher or `setup(shell_kind=...)`, rebuild/relaunch the owning Agent, recheck SHOW. |
| `sync_timeout_default_seconds` | Built-in 30, immutable; nullable call timeout uses it. A finite non-negative per-call timeout does not reconfigure it. |
| `sync_timeout_max_seconds` | `LINGTAI_TOOL_TIMEOUT_MAX_SECONDS` read each call/SHOW: positive finite values win over 120, invalid/missing values use 120, and values below 30 are floored at 30. Change only the authorized process/launcher environment; relaunch if snapshotted. Work above the ceiling uses async. |
| `result_max_chars` | Per-stream stdout/stderr capture limit, default 50000. Only an authorized embedding owner can pass positive `ShellManager(max_output=...)` before rebuilding the manager. Normal capability setup exposes no key or environment override. |
| `async_default` | Built-in false, immutable; `input.async` selects one call. |
| `async_reminder_default_seconds` | Built-in 1800, immutable. For one async run, `input.reminder` must be finite/non-negative within the platform timer bound; it is fallback, not completion. |
| `command_policy` | Both values stay redacted. Authorized setup selects `yolo=true`, then `policy_file`, then packaged policy; rebuild/relaunch and verify SHOW. No rules/paths are disclosed. |

SHOW is strict-empty and read-only; there is no Shell settings file or mutation
verb. Verify an authorized change through its owning construction procedure and
SHOW. Result limits affect disclosure, not authority; no setting permits a
working-directory escape.
