---
name: bash-debugging-cleanup
description: >
  Nested shell-manual reference for debugging silent scheduled jobs and retiring
  schedulers safely: scheduler, script, work, wake, launchd, cleanup, and shell
  work-footprint checks.
version: 1.1.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/manual/reference/scheduled-work/SKILL.md
maintenance: |
  Tracks scheduled-job debugging and cleanup; update when that integration or
  the shared footprint recipe changes.
---

# Debugging and cleanup

Open this when a scheduler is silent, fires twice, exits early, or must be
retired. Diagnose in order; do not assume that a successful scheduler exit means
that the work or wake reached the agent.

## Silent scheduled work

1. **Did the scheduler fire?** macOS: `launchctl list <label>` and inspect
   `PID`/`LastExitStatus`; Linux: `systemctl --user list-timers`; cron: inspect
   `/var/log/cron` or `journalctl -u cron` for `CMD`. Check unit syntax
   (`plutil -lint` or `systemctl --user status`), loaded/disabled state, sleep
   catch-up behavior, and clock time.
2. **Did the script run?** Read its own append-only log, not only scheduler
   stdout/stderr. A missing `[fire]` means an early launch failure; a `[fire]`
   without completion means an in-script failure. Keep `set -euo pipefail` and
   log the command boundary that failed.
3. **Did work land?** Compare the script's audit evidence with the expected
   artifact/commit/message. A logged success with no artifact is script logic,
   not a scheduler mystery.
4. **Did the agent see the wake?** For mailbox work, inspect human
   `mailbox/outbox/<uuid>`, then `mailbox/sent/<uuid>`, then the recipient's
   `mailbox/inbox/<uuid>`. Validate malformed JSON and confirm the recipient.
   A queued mail waits for the next turn; use the scheduler reference's
   `.refresh` rule only when prompt pickup is required.

## Retire without a janitor race

First obtain explicit authority for stopping/removing the named scheduler and
review a dry-run. Then:

1. Unload the unit (`launchctl unload <plist>` or
   `systemctl --user disable --now <timer>`), and verify it is gone.
2. Only then remove the unit and script; archive logs if history matters.
3. Record the unit name and retained evidence. Never delete the script first,
   remove `.agent.lock`, or launch a parallel relaunch: the kernel owns its
   flock and refresh watcher. On macOS, a scheduler may reap descendants when
   its process exits; read the scheduled-work process-tree warning before
   intentionally launching a long-lived child.

## Shell footprint

Shell may create scripts, logs, downloads, virtualenvs, scheduler units, and
build artifacts. Retain artifacts by default. Cleanup is command-specific: show
a dry-run and obtain explicit human authorization before destructive removal.
For inspection, call `psyche(action="skills", input={})`, then follow
`skills-manual` to `reference/cleanup-footprint-contract.md` and its shared
footprint recipe. Combine it with this task's selected paths in one
script. Inspection writes nothing. If the human selected an audit, append to
`logs/cleanup.jsonl` and record retired scheduler units.
