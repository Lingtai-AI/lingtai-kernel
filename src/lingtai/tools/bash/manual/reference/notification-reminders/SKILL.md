---
name: bash-notification-reminders
description: >
  Nested shell-manual reference for one-shot wakeup reminders using
  `.notification/cron.json`: payload, atomic writer, shell example, and the
  rest checklist for pending work.
version: 1.1.0
last_changed_at: 2026-09-09T00:00:00Z
related_files:
- src/lingtai/tools/bash/manual/SKILL.md
- src/lingtai/tools/notification/__init__.py
- src/lingtai/tools/notification/schema.py
- src/lingtai/tools/bash/manual/reference/scheduled-work/SKILL.md
maintenance: |
  Tracks one-shot notification reminders; update when that integration changes.
---

# One-shot notification reminders

For native Shell/Daemon jobs, rely on their completion notification; do not add
a competing timer. Only for async work without a reliable completion wake, use
`system(action="sleep", input={"reason": "check pending work", "force": false,
"delay": 240}, reasoning="last-resort wake")` as a last-resort one-shot alarm; `system-manual` owns its detail.

For an already-authorized external workflow requiring the cron channel, schedule
the writer below for the chosen future time. Running it now publishes now;
it does not create a timer. A reminder writes one complete
notification envelope to `<agent-workdir>/.notification/cron.json`; it is not a
recurring scheduler, human notification, or guarantee across process death and
sleep. After acting, dismiss the `cron` channel:

```text
notification(action="dismiss_channel",
             input={"channel": "cron", "force": null, "reason": null},
             reasoning="the cron reminder is handled")
```

Use a custom writer only when the built-in wake is unsuitable. Do not use it
for a human-facing message, a delay that must survive reboot (use
launchd/systemd/crontab), or frequent polling. Set one sane wake and rest.

## Envelope

A producer that cannot import the helper must still write the full shape:

```json
{
  "header": "Cron reminder: check pending work",
  "icon": "⏰",
  "priority": "normal",
  "published_at": "2026-05-18T06:42:00Z",
  "data": {
    "source": "cron-reminder",
    "message": "Background work is still pending.",
    "todo": "Check the named job and inspect its output.",
    "reminder_id": "task-followup-2026-05-18T06-42"
  }
}
```

Keep `published_at` as a UTC ISO timestamp and make `reminder_id` stable enough
to recognize duplicates. `message` says what changed; `todo` says the concrete
next action. Write through a temporary sibling and rename atomically:

```python
import json, os, pathlib
from datetime import datetime, timezone

agent = pathlib.Path(os.environ["AGENT_DIR"])
notification = agent / ".notification"
notification.mkdir(exist_ok=True)
payload = {
    "header": "Cron reminder: check pending work", "icon": "⏰",
    "priority": "normal",
    "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "data": {"source": "cron-reminder", "message": "Work is pending.",
             "todo": "Check job status and output.",
             "reminder_id": os.environ.get("REMINDER_ID", "cron-reminder")},
}
target = notification / "cron.json"
tmp = target.with_suffix(".json.tmp")
tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
tmp.replace(target)
```

## Rest and wake

Before resting: record the state/check target in pad. For a custom cron wake,
save the writer as an authorized task script and schedule it once at the chosen
time using [scheduled work](../scheduled-work/SKILL.md); do not execute it
immediately and expect a delay. Then end the turn and rely on the event. On wake, handle the reminder, inspect the named job, and dismiss `cron`.
A detached `sleep` writer can be lost if the process or machine stops; use the
scheduled-work reference for longer or recurring delays.
