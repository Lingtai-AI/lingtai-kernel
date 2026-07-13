---
name: telegram-task-card-manual
description: |
  Manual for the programmable Telegram Task Card (`task_card` tool). Read this to
  surface your latest reported state snapshot — a bash async job, a daemon task,
  a build — on the resident Telegram Task Card by supplying a small Python
  renderer whose stdout is one Task Card JSON object. Covers the renderer
  contract, the snapshot truthfulness model, the two co-located renderer
  templates (bash async, daemon) shipped as skill assets, the
  start | inspect | retry | stop lifecycle, path/timeout/validation rules,
  fail-loud error wakes, and how the /taskcard toggle interacts.
last_changed_at: "2026-07-13T12:15:00-07:00"
---

# Programmable Telegram Task Card — manual (what / how / why)

The `task_card` tool lets you surface your own **latest reported snapshot** on
Telegram's one tracked resident Task Card. You write a small Python **renderer**
file under your working directory; the controller runs it on an interval and
projects its output onto the Task Card's **programmable** slot, side by side with
the automatic tool-activity slot. The one tracked resident target carries both
slots. The renderer reflects a state snapshot you keep current — it is not an
autonomous progress feed.

## When to reach for this

During a **Telegram-originated turn**, when you launch a meaningful
**long-running `bash(async=true)` job or daemon task** and then go idle to await
its result, add a human-visible programmable Task Card watcher so the human
watching Telegram sees the latest reported snapshot instead of a silent gap. Two
ready templates cover exactly these two cases — see **Two ready templates** below.
This is the default for long, human-visible background work; skip it only for
quick or invisible jobs.

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

### The state source: read a snapshot file you own, not tool internals

The renderer receives **no arguments** and runs with your working directory as
its cwd, so it locates state by a fixed relative path. Point it at a small
**state snapshot file that you (the orchestrator) keep truthful** — not at a
tool's private internals:

- A `bash(async=true)` job's own state under `system/jobs/<job_id>/` is **private
  to the Bash capability**, and `bash(action="poll")` is a **one-shot, consuming**
  read (the first terminal poll marks the job consumed). A passive renderer must
  not touch either.
- A daemon run's `daemons/<id>/daemon.json` is a **versioned forensic** artifact,
  not a stable machine API; the sanctioned surface is `daemon(action="check",
  id=...)`, which is read-only and safe to poll.

So the honest pattern is: **you** own a tiny JSON snapshot in the working
directory and rewrite it from your own turn — right after the launch returns your
`job_id`/`id`, after each **meaningful** `poll` / `check`, and at the **terminal**
result or completion notification. The renderer shows only the **latest reported
snapshot** — the frame it prints reflects what you last recorded, not autonomous
job progress. It never invents or introspects tool state.

**Truthfulness contract (both templates enforce this):** a live/terminal card is
shown **only** when the snapshot carries both a nonempty **identity** (`job_id`
for bash, `id` for daemon) **and** an exact allow-listed **state** string. A
missing file, non-JSON, non-object, missing identity/state, an unknown state, or
a wrong-typed field renders an explicit `awaiting orchestrator update` frame —
never a fabricated `starting`/`running`. So an empty or half-written snapshot can
never claim progress.

Write the snapshot **completely** each time: build the whole JSON object in
memory and write it in one operation. Writing to a temporary file in the same
directory and then `os.replace`-ing it over the snapshot makes the update atomic,
so the renderer never reads a partially written object. Keep secrets and raw log
bodies out of the snapshot — the card is a progress view, not a data channel. The
manager also redacts at the render boundary, but that cannot rescue a snapshot you
fill with secrets in violation of the schema.

### Two ready templates (locate, copy, adapt, bind)

Two co-located, stdlib-only renderer templates implement exactly this pattern.
Each prints exactly one bounded Task Card object, enforces the truthfulness
contract above, and stays valid even when the snapshot is missing, partial, or
malformed:

- **`render_bash_async.py`** — for a `bash(async=true)` job. Reads
  `task_card_state.json`. Requires `job_id` (str) + `status` (str, one of
  `starting|running|done|failed|cancelled`); also surfaces optional `title`,
  `exit_code` (int), `stage`, `updated_at`, `note`.
- **`render_daemon.py`** — for a daemon task (emanation). Reads
  `daemon_card_state.json`. Requires `id` (str) + `state` (str, one of
  `running|done|failed|cancelled|timeout`); also surfaces optional `title`,
  `current`, `elapsed_s` (finite number), `last_activity`, `health`
  (`alive|stalled|unknown`), `updated_at`, `note`.

**Locate and copy the asset.** The asset lives next to this manual under
`task_card/assets/`. Resolve it relative to the **absolute manual path** that the
Telegram `manual` action returns (`telegram(action='manual')` → its `path`; this
manual sits at `task_card/SKILL.md` beside that directory), then **copy it into
your working directory** — the controller confines `renderer_path` to the agent
working directory, so the renderer must physically live there. Do not reference
the source-tree or installed-package path directly. Rename the copy per job if you
run several, and adapt only its `STATE_FILE` and labels; each file's docstring
documents its full snapshot schema and orchestrator update points.

Then bind it:

```json
{"action": "start", "renderer_path": "render_bash_async.py", "interval_s": 5}
```

As you rewrite the snapshot, the card's programmable slot follows the latest
reported snapshot. Starting a watch **drives the programmable slot of the
`TelegramManager`-owned single resident Task Card**; the manager reuses the one
resident card it already tracks, or **creates its single resident** if none is
tracked yet — it never starts a second manager or a second card. `TelegramManager`
stays the one render/compose/persistence/transport owner; the controller is a thin
driver that forwards validated frames.

### The lifecycle: start | inspect | retry | stop

- **start** — validate and run the renderer **once, synchronously**. If that first
  run fails (bad path, timeout, non-zero exit, invalid frame) you get an
  immediate tool error and **no watch is created**. On success the first frame is
  projected and a background watch begins; you receive a `watch_id`. Optional
  `interval_s` (seconds between runs, minimum 1; default 5) and `timeout_s`
  (per-run timeout; default 10). **Initial partial:** if the first frame was sent
  and is visible but its resident id could not be durably saved, `start` still
  returns `status: ok` with your `watch_id`, plus `partial: true`,
  `resident_persist_failed: true`, and the sent `message_id` — the watch is fully
  usable (`inspect`/`retry`/`stop` all work) and the error clears on the next
  accepted update. A send that returns no usable message id (or a
  malformed/cross-route id) is instead a plain error with **no** watch — the addon
  never invents or adopts an unknown card.
- **inspect** — report a watch's state (`watching`, `error`, `stopping`, or
  `stop_failed`), its `last_valid_frame`, `last_valid_frame_at` (a UTC ISO-8601
  timestamp of when that frame was captured — set on the first accepted frame and
  every later accepted/recovered one, and left unchanged while attempts fail), and
  the current `error` (if any). Pass the `watch_id`.
- **retry** — re-run the renderer **now** for an active (failed or healthy) watch
  instead of waiting for the next interval, then report the fresh state. Once you
  have asked to `stop`, `retry` continues the stop path only — it re-checks
  quiescence and re-attempts the clear, and will **not** re-run your renderer.
  Pass the `watch_id`.
- **stop** — stop the renderer thread and clear **only** the programmable frame;
  the automatic slot and the resident message remain, and renderer files are
  **never** deleted. The watch is removed and `stopped` is returned **only after**
  the watcher thread has actually stopped **and** the clear is accepted — `stop`
  never reports `stopped` while a renderer or an in-flight update is still
  running. If the renderer, or an update projection, has not stopped yet (still
  running past the join budget) or the clear fails (a
  transient backend edit failure), `stop` returns a truthful, retryable
  `stop_failed` error and **keeps** the watch; call `stop` again (or `retry`) to
  re-check quiescence and re-attempt the clear — it never re-runs your renderer.
  A renderer or an update that returns after you asked to stop cannot resurrect
  the watch: its result is dropped, and an update that may already have landed is
  cleared for you so the late frame does not linger. When the programmable slot
  is the only content on the card, stopping
  shows a stable `— WATCH STOPPED —` marker (an empty Telegram message is not
  allowed) and leaves the resident message reusable. Pass the `watch_id`.

### When a watch fails

The watch keeps its **last valid frame** and does not tear itself down on a
transient failure. Each new failure *episode* raises one deduped, fail-loud
system-notification wake (`task_card.error`, high priority) carrying only a
stable code, a safe message, and watch metadata — never renderer output. Repeated
identical failures inside one episode stay silent; the next good frame emits one
`recovered` wake and resumes. Use `inspect` to read the error, fix the renderer,
and `retry`.

Failure codes you may see: `renderer_timeout`, `renderer_nonzero_exit`,
`invalid_frame`, `renderer_failed`, `backend_edit_failed`,
`resident_persist_failed` (the card is visible but its resident id was not durably
saved — retryable; clears on the next accepted update), and — for a `stop`
that is not yet complete — `stop_thread_alive` (the renderer thread is still
running; retry `stop` once quiescent) or `stop_finalize_failed` (the clear was
rejected; retry `stop`). All are retryable.

## WHY it is designed this way

- **Telegram never runs your code as a card.** The controller runs your renderer
  as a normal subprocess and forwards only a **validated data object** to Telegram
  over the private reverse channel. Telegram renders text, never executes a
  function. This keeps a clear trust boundary: the transport sees data, not code.
- **One owner, one tracked resident target.** `TelegramManager` remains the single
  render / compose / persistence / transport owner, including the hard-at-most-one
  resident that stays at the chat's last message (it rotates old-first when a newer
  message appears below it, and can briefly leave zero cards rather than two). The
  controller is a thin driver; there is no competing rendering system and no
  cross-channel abstraction.
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
`src/lingtai/mcp_servers/telegram/task_card/SKILL.md` in the source tree. From the
Telegram MCP manual (`telegram(action='manual')`), the **Programmable Task Card**
section routes here. The two renderer templates ship as co-located skill assets
under `task_card/assets/` (`render_bash_async.py`, `render_daemon.py`); resolve
them relative to the absolute manual `path` the `manual` action returns and copy
one into your working directory, as **Two ready templates** describes — that path,
not any fixed source-tree location, is the usable one. The paired `CONTRACT.md`
states the interface promise and `ANATOMY.md` maps the structure.
