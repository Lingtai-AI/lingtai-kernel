---
name: telegram-task-card-manual
description: |
  Nested telegram-mcp-manual reference for the programmable Telegram Task Card
  (`task_card` tool). Read this when a task needs a truthful, task-specific
  watcher: inspect the actual task and producer evidence, design a small
  renderer, and use the start | inspect | retry | stop lifecycle without
  prescribing a fixed layout or data source.
last_changed_at: 2026-07-19T00:00:00Z
related_files:
- src/lingtai/mcp_servers/telegram/SKILL.md
- src/lingtai/mcp_servers/telegram/task_card/controller.py
- src/lingtai/mcp_servers/telegram/task_card/interface.py
- src/lingtai/mcp_servers/telegram/task_card/ANATOMY.md
maintenance: |
  Tracks the Telegram Task Card renderer/controller behavior and task-specific
  watcher method it documents; update when that integration or its guidance changes.
---

# Programmable Telegram Task Card — task-specific watcher manual

The `task_card` tool lets you surface a truthful, latest-reported snapshot on
Telegram's one tracked resident Task Card. You supply a small Python renderer
under your working directory. The controller runs it on an interval and forwards
only its validated data to the programmable slot beside Telegram's automatic
activity slot. The renderer is a view of evidence you keep current; it is not an
autonomous progress monitor.

This manual teaches a method, not a renderer template. The task, its producer, and
the evidence they expose determine the watcher's fields, update cadence, and
presentation. Do not copy a fixed layout or assume one data source works for every
job.

## When to reach for a watcher

During a Telegram-originated turn, use a watcher when a meaningful task will run
long enough that a person would otherwise see a silent gap—for example, while you
wait for a background job, daemon, build, or other producer. First inspect the
actual task and producer evidence. A quick or invisible operation does not need a
watcher, and collecting updates on every turn is not a goal.

The watcher should make the current task understandable without pretending to know
more than its producer reports. Choose only facts that demonstrate movement or an
honest lack of movement, and say when a fact is unavailable.

## The watcher information contract

For the current task, design a concise frame that communicates these facts when
reliably available:

1. **Purpose** — what the watcher is for and what outcome the task is pursuing.
2. **Time lapse** — how long the task or current stage has been running, based on a
   reliable recorded start or stage time. If that evidence is unavailable, say
   `unavailable`; do not infer duration from renderer refreshes.
3. **Recent activity / last meaningful movement** — what actually changed most
   recently, where that evidence came from, and when it happened or was last
   checked. A refreshed card is not itself activity.
4. **Token usage** — show usage only from a trustworthy producer/ledger. If it is
   unavailable, say `unavailable` rather than guessing or substituting a counter.
5. **Current state, next gate, and blocker** — name the state the producer supports,
   the next evidence-based transition or review gate, and any blocker. Do not call
   work `running`, `done`, or `healthy` without evidence; say `unknown` or
   `unavailable` when that is the truth.
6. **Feedback and improvement** — after meaningful real use, ask whether the
   watcher helped (see **Feedback and reuse loop**).

These are information requirements, not a visual template. The frame may use any
layout that keeps the facts legible and bounded. Do not add decorative fields just
to make a card look active.

## Inspect the task and producer before writing the renderer

Before choosing a source, identify:

- the task's purpose, stages, gates, and known blockers;
- the producer that owns state (job, daemon, build system, queue, or another
  workflow) and its documented read/check surface;
- the authoritative start/activity/token fields, their units, and their freshness;
- what the producer does **not** expose, so the watcher can state `unavailable`;
- how you will record a small, orchestrator-owned snapshot after launch and after
  each meaningful producer event, justified check, or terminal result.

Prefer the producer's documented status/check result or an orchestrator-owned
snapshot over private tool internals. Never let a passive renderer consume a
one-shot poll, reach into versioned forensic state, or treat a wall-clock redraw as
proof of progress. If the task has no reliable activity or token signal, report
that limitation plainly and choose another truthful signal rather than inventing
one. Write snapshots atomically when the orchestrator owns them, so a renderer
never mistakes a half-written update for a real state.

## Renderer contract

A renderer is an ordinary Python file inside the agent working directory. Each run
must exit `0` and print exactly one JSON object to stdout:

- `title` is a string (optional);
- `lines` is an array of at most 20 strings (optional);
- `footer` is a string (optional);
- at least one of those values must be non-empty.

Nonzero exit, timeout, empty or multi-object output, a non-object, or wrong field
types are handled failures. The controller uses `sys.executable`, the working
directory as `cwd`, a per-run timeout, and symlink-resolved containment for
`renderer_path`. Telegram receives only the validated card data, never renderer
code. Keep secrets, credentials, raw logs, and unbounded output out of the frame;
the manager also redacts at its boundary but cannot make an unsafe source safe.

## Safe custom renderer example

The following is a runnable, stdlib-only example—not a required data source or
layout. It reads an orchestrator-owned `task_snapshot.json`; replace that source,
labels, and arrangement only after inspecting the actual producer. The example
clips strings, accepts an object only, uses `unavailable` for absent facts, and
prints exactly one bounded Task Card object. Keep the renderer in your working
directory, and keep secrets and raw logs out of its snapshot.

```python
#!/usr/bin/env python3
import json
from pathlib import Path

STATE = Path("task_snapshot.json")  # example only; choose evidence for this task
MAX_SNAPSHOT_BYTES = 16_384
MAX_TEXT = 160


def text(value):
    if not isinstance(value, str):
        return "unavailable"
    value = "".join(ch for ch in value if ch == " " or ch.isprintable()).strip()
    if not value:
        return "unavailable"
    return value[: MAX_TEXT - 1] + "…" if len(value) >= MAX_TEXT else value


def load_snapshot():
    try:
        with STATE.open("rb") as stream:
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES:
            return {}
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return {}
    return data if isinstance(data, dict) else {}


def main():
    data = load_snapshot()

    labels = (
        ("purpose", "purpose"),
        ("time lapse", "time_lapse"),
        ("last movement", "last_movement"),
        ("token usage", "token_usage"),
        ("state", "state"),
        ("next gate", "next_gate"),
        ("blocker", "blocker"),
    )
    lines = [f"{label}: {text(data.get(key))}" for label, key in labels]
    card = {
        "title": text(data.get("title")) if data.get("title") else "Task watcher",
        "lines": lines[:20],
        "footer": "snapshot reported by orchestrator",
    }
    print(json.dumps(card, ensure_ascii=False))


if __name__ == "__main__":
    main()
```

A real renderer may use another safe source, calculate a duration from an
authoritative timestamp, or omit a field that has no truthful value. It must still
print exactly one JSON object and avoid claiming progress that the producer did
not report. Start it with the controller only after adapting and testing it:

```json
{"action": "start", "renderer_path": "watcher.py", "interval_s": 5, "timeout_s": 10}
```

## Lifecycle: `start` | `inspect` | `retry` | `stop`

- **`start`** validates the path and runs the renderer once synchronously. A bad
  first run returns an error and creates no watch. On an accepted first frame it
  projects the programmable slot and returns a `watch_id`; the watcher then runs
  at the requested interval (minimum 1 second, default 5). Each run uses
  `timeout_s` as its renderer timeout (minimum 0.1 second, default 10). A validated
  visible frame whose durable resident-id write failed is surfaced as a retryable partial,
  not mislabeled as full success. An indeterminate or malformed transport id is a
  plain error and no unknown card is adopted.
- **`inspect`** reports `watching`, `error`, `stopping`, or `stop_failed`, including
  the last valid frame, its UTC ISO-8601 `last_valid_frame_at`, and the current
  error. A failed attempt does not overwrite the last valid frame or timestamp.
- **`retry`** runs the renderer immediately for an active watch. After `stop` has
  been requested it continues only the stop path; it never starts another render.
- **`stop`** quiesces the watcher and clears only the programmable slot. It returns
  `stopped` and removes the watch only after the watcher thread is quiescent and
  the backend accepts the clear. A live thread or transient clear failure returns
  a retryable `stop_failed` and retains the watch; call the same stop again. A late
  result after stop is dropped and compensated if it could have landed. Renderer
  files are never deleted, and the automatic slot is not disturbed.

The Telegram manager remains the single owner of the tracked resident, composition,
transport, persistence, and the independent automatic slot. `/taskcard off` hides
both slots at presentation time while everything here keeps running — see
**TASKCARD STATE** in the parent [`telegram` manual](../SKILL.md).

## Terminal cleanup and fail-loud behavior

A passive renderer cannot stop itself. When the producer reaches a terminal state,
record that terminal evidence and immediately call
`task_card(action="stop", watch_id="<watch_id>")`. Terminal evidence must still
distinguish success, failure, cancellation, timeout, or an unavailable outcome
according to the producer's contract. Do not restart or duplicate the watch, and
do not leave a completed watcher resident merely because its card can still
refresh.

After a handle exists, a renderer or backend failure preserves the last valid frame
and emits one deduplicated, fail-loud `task_card.error` wake per failure episode,
then one recovery wake after the next good frame. Use `inspect` to read the safe
error and `retry` after correcting the producer snapshot or renderer. Raw renderer
output and secrets never belong in a wake.

## Feedback and reuse loop

After a meaningful real use-cycle or stage boundary, ask the user a focused question,
for example: “Did this watcher help you understand the task, and what should change?”
Do not ask every turn or turn feedback collection into ritual or harassment. First
adapt this watcher to the answer—its facts, freshness, wording, or noise level—then
consider whether the lesson is genuinely reusable. Deposit only that reusable,
source-grounded method as a skill; do not turn a one-off task layout into a product
contract or a fixed template.

## Why these boundaries exist

A watcher is useful when it makes producer evidence legible during a meaningful wait.
It is harmful when a static renderer, guessed token count, or redraw timestamp
creates the appearance of movement. The controller therefore owns validation,
confinement, lifecycle, and fail-loud recovery, while the Agent owns judgment about
the task, evidence source, facts to show, and how to improve the current watcher.

The paired [`CONTRACT.md`](CONTRACT.md) defines the stable interface and behavior
promises; this manual defines how an Agent should design and operate a watcher
without weakening those promises. The paired [`ANATOMY.md`](ANATOMY.md) maps the
controller, resident, transport, and producer connections.
