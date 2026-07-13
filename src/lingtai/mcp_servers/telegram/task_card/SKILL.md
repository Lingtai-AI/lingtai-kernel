---
name: telegram-task-card-manual
description: |
  Manual for the programmable Telegram Task Card (`task_card` tool). Read this to
  bind live state — a bash async job, a build, a countdown — to the resident
  Telegram Task Card by supplying a small Python renderer whose stdout is one
  Task Card JSON object. Covers the renderer contract, a safe runnable example,
  the start | inspect | retry | stop lifecycle, path/timeout/validation rules,
  fail-loud error wakes, and how the /taskcard toggle interacts.
last_changed_at: "2026-07-12T21:30:00-07:00"
---

# Programmable Telegram Task Card — manual (what / how / why)

The `task_card` tool lets you attach your own live view to the single resident
Telegram Task Card. You write a small Python **renderer** file under your working
directory; the controller runs it on an interval and projects its output onto the
Task Card's **programmable** slot, side by side with the automatic tool-activity
slot. One resident message carries both.

## WHAT it is

- A model-facing controller with four actions: `start`, `inspect`, `retry`,
  `stop`.
- A binding between *your state* (whatever your renderer reads — a job log, a
  status file, a queue depth) and *the resident Task Card*.
- English-only. There is no i18n here and none should be added.

The resident Telegram Task Card has two independent slots:

- **automatic** — the kernel's own tool-activity rows, heartbeat, and any API
  errors, managed for you during Telegram-originated turns. You do not drive it.
- **programmable** — the slot this tool owns. Updating one slot never disturbs
  the other; with both present the programmable block appears under a `— WATCH —`
  header.

## HOW to use it

### The renderer contract

A renderer is an ordinary Python file inside your working directory. Each run, it
must print **exactly one** JSON object to stdout and exit `0`. The object may
carry any of:

- `title` — a string headline (optional).
- `lines` — an array of strings, at most 20 (optional).
- `footer` — a string footer (optional).

At least one of the three must be non-empty. Anything else is rejected:

- non-zero exit, a timeout, empty stdout, output that is not a single JSON
  object (multiple concatenated objects included), a non-object, or a wrong field
  type (e.g. `lines` containing a number) all fail validation.
- The renderer runs with the **runtime interpreter** (`sys.executable`) with your
  working directory as the process `cwd`, under a per-run timeout.
- `renderer_path` must resolve (symlinks included) to a regular file **inside**
  the working directory. Path traversal or an absolute escape is refused.

Secrets are redacted at the render boundary by the Telegram manager, and raw
renderer output never appears in error wakes — but keep secrets out of the card
anyway; it is a progress view, not a data channel.

### A safe runnable example

Save this as `render_card.py` in your working directory. It reads a plain status
file your long-running job updates and prints one Task Card object:

```python
# render_card.py — prints exactly one Task Card JSON object to stdout.
import json
import pathlib

status_file = pathlib.Path("job_status.txt")  # relative to the working dir
state = status_file.read_text().strip() if status_file.exists() else "starting"

print(json.dumps({
    "title": "Nightly backup",
    "lines": [
        f"stage: {state}",
        "host: db-primary",
    ],
    "footer": "auto-refreshing",
}))
```

Then bind it:

```json
{"action": "start", "renderer_path": "render_card.py", "interval_s": 5}
```

As your job rewrites `job_status.txt`, the card's programmable slot follows it.

### The lifecycle: start | inspect | retry | stop

- **start** — validate and run the renderer **once, synchronously**. If that first
  run fails (bad path, timeout, non-zero exit, invalid frame) you get an
  immediate tool error and **no watch is created**. On success the first frame is
  projected and a background watch begins; you receive a `watch_id`. Optional
  `interval_s` (seconds between runs, minimum 1; default 5) and `timeout_s`
  (per-run timeout; default 10).
- **inspect** — report a watch's state (`watching`, `error`, `stopping`, or
  `stop_failed`), its `last_valid_frame`, `last_valid_frame_at` (a UTC ISO-8601
  timestamp of when that frame was captured — set on the first accepted frame and
  every later accepted/recovered one, and left unchanged while attempts fail), and
  the current `error` (if any). Pass the `watch_id`.
- **retry** — re-run the renderer **now** for a failed (or healthy) watch instead
  of waiting for the next interval, then report the fresh state. Pass the
  `watch_id`.
- **stop** — stop the renderer thread and clear **only** the programmable frame;
  the automatic slot and the resident message remain, and renderer files are
  **never** deleted. The watch is removed and `stopped` is returned **only after**
  the clear is accepted. If the clear fails (a transient backend edit failure),
  `stop` returns a truthful, retryable `stop_failed` error and **keeps** the watch
  so you can call `stop` again — the renderer thread is already stopped, so the
  retry only re-attempts the clear. When the programmable slot is the only content
  on the card, stopping shows a stable `— WATCH STOPPED —` marker (an empty
  Telegram message is not allowed) and leaves the resident message reusable. Pass
  the `watch_id`.

### When a watch fails

The watch keeps its **last valid frame** and does not tear itself down on a
transient failure. Each new failure *episode* raises one deduped, fail-loud
system-notification wake (`task_card.error`, high priority) carrying only a
stable code, a safe message, and watch metadata — never renderer output. Repeated
identical failures inside one episode stay silent; the next good frame emits one
`recovered` wake and resumes. Use `inspect` to read the error, fix the renderer,
and `retry`.

Failure codes you may see: `renderer_timeout`, `renderer_nonzero_exit`,
`invalid_frame`, `renderer_failed`, `backend_edit_failed`, and — when a `stop`
could not clear the programmable frame — `stop_finalize_failed` (retryable: call
`stop` again).

## WHY it is designed this way

- **Telegram never runs your code as a card.** The controller runs your renderer
  as a normal subprocess and forwards only a **validated data object** to Telegram
  over the private reverse channel. Telegram renders text, never executes a
  function. This keeps a clear trust boundary: the transport sees data, not code.
- **One owner, one resident message.** `TelegramManager` remains the single
  render / compose / persistence owner. The controller is a thin driver; there is
  no competing rendering system and no cross-channel abstraction.
- **Synchronous first frame, fail-loud after.** A bad renderer fails *before* you
  get a handle, so a broken watch never lingers. After the handle exists,
  failures are surfaced as deduped wakes rather than silently dropping the card,
  and the last good frame stays on screen.
- **Confined and bounded.** Renderer-path confinement, a per-run timeout, a line
  cap, and strict single-object validation keep a runaway or hostile renderer
  from escaping the workdir or flooding the card.

## Interaction with `/taskcard off`

`/taskcard off` hides delivery of **both** slots at the Telegram presentation
boundary. It does **not** stop the mechanics: your renderer keeps running,
watches keep ticking, the last-valid frame keeps updating, and error/recovery
bookkeeping continues. Turning it back on needs no restart — the next projection
updates the resident card again. Do not infer the toggle state from whether an
old card is visible; read the current `taskcard` value.

## Reaching this manual

This manual is co-located with the unit at
`src/lingtai/mcp_servers/telegram/task_card/SKILL.md`. From the Telegram MCP
manual (`telegram(action='manual')`), the **Programmable Task Card** section
routes here. The paired `CONTRACT.md` states the interface promise and `ANATOMY.md`
maps the structure.
