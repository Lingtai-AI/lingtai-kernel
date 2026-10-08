---
name: bash-scheduled-work
description: >
  Nested shell-manual reference for recurring, time-driven work: host scheduler
  choice, LingTai wake-by-mailbox-drop, short prompts, script hygiene, macOS
  launchd, Linux systemd timers, crontab, and process-tree hazards.
version: 1.1.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/bash/_async_supervisor.py
maintenance: |
  Tracks cron-driven scheduled work; update when the scheduler integration or
  wake contract changes.
---

# Scheduled work

Open this for recurring or time-triggered work. The host scheduler is an
external wake mechanism; Shell has no built-in recurring scheduler.

## Pick the trigger

- **Time-driven substantive work** (“every hour, do X”): use a host scheduler.
- **Event-driven work with a deadline** (“when Y arrives, respond quickly”): use
  the event source or mailbox/watch, not cron.
- **Periodic work inside an active turn**: compare a stored timestamp in the
  turn loop, not an external scheduler.

Prefer an event watch when it is the real trigger; polling every cycle burns
turns when nothing changed.

## Wake-by-mailbox contract

For an explicitly human-approved schedule using the human sender, publish one
complete message to the human outbox (retain the scheduler `identity.via`; this
is not an interactive human instruction or permission to expand the schedule):

```text
<project>/.lingtai/human/mailbox/outbox/<uuid>/message.json
```

Use a fresh UUID and a message like:

```json
{
  "id": "<uuid>", "_mailbox_id": "<uuid>", "from": "human",
  "to": ["<agent-address>"], "cc": [], "subject": "<short subject>",
  "message": "Use the named skill; this is the time-bound context.",
  "type": "normal", "received_at": "<UTC timestamp>",
  "identity": {"address": "human", "agent_name": "human",
                "via": "<scheduler-name>-cron"}
}
```

The kernel claims the human outbox folder into `human/mailbox/sent/<uuid>/` and
copies it to the agent's `mailbox/inbox/<uuid>/`; message handling is a separate
step. Do not promise that delivery interrupts a long active turn; a live asleep
listener can wake normally, but mail is not recovery for a stopped process.
Do not refresh merely to accelerate mail. Only when the owner explicitly
approves this schedule's refresh, publish the message first, then `touch
<agent>/.refresh` and exit. Follow `system-manual` for lifecycle prechecks;
the kernel's refresh watcher owns relaunching.
Never parse or remove `.agent.lock`, wait for its path to vanish, or launch a
second relaunch process: path existence is not the kernel's flock and causes
duplicate agents.

Keep the scheduled prompt short: time-bound context plus the name of a
versioned skill in `.library/custom/<name>/SKILL.md`; the skill owns the recipe.
Do not embed a long command sequence in a prompt replayed every hour.

## Script hygiene

- Make each fire idempotent; use a cycle marker when doing substantive work.
- Audit the previous cycle and append `[fire]`, `[audit]`, and `[err]` records to
  a log. Do not rely only on scheduler stdout/stderr.
- Start scripts with `set -euo pipefail`; explicitly write `cmd || true` when a
  failure is intentional.
- Use absolute binary paths or set `PATH`; launchd/systemd/cron inherit sparse
  environments. Verify the expected artifact, commit, or message after work.
- Do not silently prune logs or work products. Deletion needs an explicit
  human-approved dry-run and cleanup plan.

## macOS launchd

Use a user LaunchAgent at `~/Library/LaunchAgents/<label>.plist`:

```xml
<plist version="1.0"><dict>
  <key>Label</key><string>ai.example.my-job</string>
  <key>ProgramArguments</key><array>
    <string>/bin/bash</string><string>/Users/you/.scripts/my-job.sh</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/you/.scripts/my-job.out</string>
  <key>StandardErrorPath</key><string>/Users/you/.scripts/my-job.err</string>
</dict></plist>
```

Use one trigger (`StartCalendarInterval` or `StartInterval`), then validate and
load/test it:

```bash
plutil -lint ~/Library/LaunchAgents/ai.example.my-job.plist
launchctl load ~/Library/LaunchAgents/ai.example.my-job.plist
launchctl list ai.example.my-job
launchctl start ai.example.my-job
launchctl unload ~/Library/LaunchAgents/ai.example.my-job.plist
```

After editing a loaded plist, unload/reload it to apply the new definition.
`StartCalendarInterval` catches at most one missed fire after sleep;
`StartInterval` does not provide the same catch-up. Choose the schedule with
that limitation in mind. launchd can reap a child process tree
when the `ProgramArguments` process exits: `&`/`disown` are not sufficient. Prefer
the kernel refresh watcher; if a human-authorized workflow truly needs a child
to outlive the job, use a reviewed double-fork/full-detach launcher (PPID=1)
and verify its lifetime rather than assuming it.

## Linux systemd timer

Create a oneshot service and timer under `~/.config/systemd/user/`:

```ini
# my-job.service
[Service]
Type=oneshot
ExecStart=/bin/bash /home/you/.scripts/my-job.sh
StandardOutput=append:/home/you/.scripts/my-job.out
StandardError=append:/home/you/.scripts/my-job.err

# my-job.timer
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
```

Activate and inspect it:

```bash
systemctl --user daemon-reload
systemctl --user enable --now my-job.timer
systemctl --user list-timers
systemctl --user status my-job.service
journalctl --user -u my-job.service
```

`Persistent=true` catches up after the machine was off; omit it when catch-up
work would be wrong.

## crontab fallback

When systemd is unavailable:

```cron
0 * * * * /bin/bash /home/you/.scripts/my-job.sh >> /home/you/.scripts/my-job.log 2>&1
```

`crontab -e` uses five fields (minute, hour, day-of-month, month, weekday)
and an especially sparse `PATH`; use absolute paths and keep the script
idempotent.
