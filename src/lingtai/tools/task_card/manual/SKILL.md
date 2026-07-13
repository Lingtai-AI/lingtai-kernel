---
name: task-card-manual
description: >
  Model-facing manual for the public `task_card` tool: run a Python renderer
  under your own working directory on an interval and project its output onto the
  resident Telegram Task Card's programmable slot. Read this before calling
  task_card(action="start"). Covers start/inspect/retry/stop, the one-JSON-object
  renderer protocol, workdir/symlink confinement, automatic vs programmable slots,
  start-time failure vs after-start error/recovery notifications, last-valid-frame
  behavior, and what not to do.
version: 1.0.0
last_changed_at: "2026-07-12T00:00:00-07:00"
---

# Task Card Manual — the public `task_card` tool

`task_card` lets you drive the **programmable slot** of the single resident
Telegram Task Card. You write a small Python **renderer** file inside your own
working directory; the controller runs it on an interval and projects its output
onto the card. Telegram never runs your code — the controller runs it locally
with the runtime interpreter, validates the output, and forwards only validated
data over the private Telegram reverse channel.

This tool exists **only when a Telegram MCP client is connected**. On agents with
no Telegram channel, `task_card` is not registered — do not assume it is globally
available.

## What / How / Why

- **What:** a resident, self-updating status card in the Telegram chat, sourced
  from a renderer you control.
- **How:** `start` validates and runs the renderer once (synchronously), then
  watches it and re-projects each valid frame; `inspect`/`retry`/`stop` manage the
  running watch.
- **Why this boundary:** the agent must be able to publish live status without
  ever handing executable code to Telegram, and without competing with the
  automatic tool-activity card. So the renderer runs **locally** and its stdout is
  a **strict data schema** (one JSON object). The Telegram manager stays the single
  owner of rendering, composition, and persistence; the controller sends it
  validated data only.
- **Trust boundary — the renderer is NOT sandboxed.** Only the *renderer path* is
  confined: it is resolved (symlinks included) and must stay under the agent
  working directory, and the subprocess `cwd` is that working directory. The
  renderer's Python code itself runs with the **full permissions of the runtime
  process** — host filesystem, network, and environment, exactly like any other
  code the runtime executes. The runtime does **not** sandbox or restrict it, and
  the workdir confinement applies to the path only, not to what the code may do
  once running. So run **only trusted renderer code**, and keep it reading only
  trusted workdir state by practice — not because the runtime technically prevents
  broader access. The strict one-JSON-object stdout schema and redaction-by-
  construction (raw renderer output/secrets never enter errors or wakes) still hold.

## Actions

| Action | Inputs | Result |
|---|---|---|
| `start` | `renderer_path` (required); optional `interval_s` (≥1, default 5), `timeout_s` (default 10) | Runs the renderer once now. On success returns `{status:"ok", watch_id, state:"watching"}` and begins watching. On any first-run failure returns `{status:"error", message}` and creates **no** watch. |
| `inspect` | `watch_id` | `{status:"ok", state, last_valid_frame, last_valid_frame_at, error}` — current health + last good frame. |
| `retry` | `watch_id` | Re-runs the renderer immediately, then returns the same shape as `inspect`. Use after you fix a failing renderer. |
| `stop` | `watch_id` | Ends the watch and clears **only** the programmable slot. Renderer files are never deleted. |

## The renderer protocol

A renderer is a plain Python script. Each run **must print exactly one JSON
object** to stdout with any of these fields:

- `title` — string (optional)
- `lines` — array of strings (optional, at most 20)
- `footer` — string (optional)

At least one of `title` / `lines` / `footer` must be present. Anything else is a
frame error: non-JSON, multiple JSON values, a non-object (array/number/string),
wrong field types, empty stdout, a nonzero exit, or exceeding the timeout.

### Safe renderer example

Create the file inside your working directory (e.g. `task_card_status.py`):

```python
import json
from pathlib import Path

# Read your OWN state from files under the workdir — never shell-evaluate
# untrusted input, and never exec/os.system on external data.
done = len(list(Path("reports").glob("*.md"))) if Path("reports").is_dir() else 0

# Print EXACTLY ONE JSON object. Do not log anything else to stdout.
print(json.dumps({
    "title": "Nightly audit",
    "lines": [f"reports written: {done}", "phase: reviewing"],
    "footer": "updates every 15s",
}))
```

Then start the watch:

```
task_card(action="start", renderer_path="task_card_status.py", interval_s=15)
```

`renderer_path` is resolved **relative to your working directory** and confined to
it: after resolving symlinks, a path that escapes the workdir (via `..`, an
absolute path, or a symlink pointing outside) is rejected. This confines the
*path* only, not the running code (see the trust boundary above): keep the
renderer and everything it reads inside the workdir by practice.

## Automatic vs programmable slots — one resident message

There are two composed slots on **one** resident Telegram Task Card message:

- the **automatic** slot (kernel-owned) shows live tool activity during
  Telegram-originated turns — you do not manage it;
- the **programmable** slot is what `task_card` drives.

They render into a single message. Your `start`/`stop` only touch the programmable
slot; the automatic slot is left intact. `stop` clears just your slot.

## Failure model

- **At start (synchronous):** the first renderer run must succeed. A nonzero
  exit, timeout, non-object or multiple-object stdout, or an invalid field type is
  an immediate tool error and **no watch is created** — fix the renderer and call
  `start` again.
- **After start (watching):** if a later run fails, the watch **keeps the last
  valid frame on the card** and raises one notification per distinct error code
  (a `task_card.error` system notification). Repeated identical failures are
  deduped. When the renderer produces a valid frame again, one recovery
  notification is sent. Notifications carry a stable code/message and safe watch
  metadata only — never raw renderer output or secrets.
- **`inspect`** shows `state` (`watching`/`error`), the `last_valid_frame`, and the
  current `error` (if any). **`retry`** forces an immediate re-run.

## What not to do

- Do **not** point `renderer_path` outside the working directory (no `..`, no
  absolute escape, no symlink to an external path) — it will be rejected.
- Do **not** print more than one JSON value, and do **not** log progress/debug
  text to stdout. Stdout must be exactly one Task Card JSON object. Send logs to
  stderr or a file instead.
- Do **not** put secrets or raw sensitive output into `title`/`lines`/`footer` —
  the card text is delivered to the chat.
- Do **not** shell-evaluate or `exec` untrusted input inside the renderer; read
  your own workdir state.
- Do **not** assume `task_card` is available without Telegram — if the tool is
  absent, this agent has no Telegram channel.
- Do **not** rely on `stop` to delete files — it clears the programmable slot
  only; renderer files persist.
