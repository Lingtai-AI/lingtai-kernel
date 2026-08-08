---
related_files:
- src/lingtai/tools/task_card/__init__.py
- src/lingtai/tools/task_card/ANATOMY.md
- src/lingtai/tools/task_card/CONTRACT.md
maintenance: |
  Keep this manual aligned with the intrinsic task_card capability's actual
  action surface, the exact taskcard/status and taskcard/taskcard.md file
  contract, and the one-card-per-agent lifecycle. Update it with the paired
  Anatomy/Contract whenever the renderer contract, file paths, or stop/order
  semantics change.
---

# task_card manual

Use `task_card` to maintain one agent-local declarative Task Card artifact.

## When to use it

Start a watch proactively — without waiting to be asked — whenever a human is
following meaningful long-running, multi-step, or parallel work and a durable
progress view would materially help them track it. Skip it for quick
single-step work: starting a watch you will stop moments later is ritual
noise, not progress reporting. Only keep a watch running while you can make
its renderer output truthful and current; a stale or inaccurate card misleads
more than no card at all. Optionally `retry` once more to publish a final
state before winding down. Use `stop` to pause a watch while preserving its
last body for a possible later `retry`/inspection; use `remove` once the
underlying work is completed, cancelled, or abandoned, so `/taskcard` and
other consumers cannot keep exposing a stale card.

The capability owns exactly two files under your working directory:

- `taskcard/status`
- `taskcard/taskcard.md`

While a watch is active it also keeps one restart descriptor at
`taskcard/watch.json`, so the card survives `refresh`/molt/agent-stop: the next
boot rehydrates the same watch (same `watch_id`, renderer, cadence, ceilings,
and remaining refresh budget), reruns the renderer to refresh the body, marks
`active`, and resumes the updater. `stop`, `remove`, and refresh-limit
exhaustion clear the descriptor because those are deliberate ends of the watch;
a stale descriptor (renderer gone, budget exhausted, corrupt file) is cleared
on boot and leaves the card `inactive` rather than silently resurrecting a
dead watch.

Actions are `start`, `inspect`, `retry`, `stop`, `remove`, and `manual`.

## Resident meta projection and the 2000-char cap

The card body is projected into the agent's own meta block as
`_meta.agent_meta.taskcard`, so the human (via Telegram/Feishu/etc) and the
agent always see the same card. Projection is **change-gated**: an unchanged
body is not re-injected every turn; only material body/status changes or the
first appearance attach a fresh payload. When no card is present the meta block
carries a generic hint (`no taskcard present, consider maintaining one, see
task_card manual`).

A rendered body longer than **2000 chars is refused**, never truncated. This
keeps the card a bounded, high-attention goal. Treat the card itself as a
progressive-disclosure summary: keep the top of the card to the current goal,
status, and the single next step, and push complex progress detail into files
(reports, logs, checklists) referenced from the card rather than into the card
body. If a renderer tries to publish more than 2000 chars, `start`/`retry`
refuse that update and keep the last valid body.

## Absent / stale reminders

After every `reminder_turns` completed text turns (default **10**, configured
at `taskcard/taskcard.json`), the producer emits a system notification
("Task Card reminder") telling the agent to check whether the card is **absent
or stale**, then update it or retire it only if useful. This is the loop that
keeps the shared agent-human view honest without re-injecting the card body
itself: the resident meta projection stays change-gated (identical bytes are
not re-sent every turn), while the reminder re-surfaces the *question* on a
coarse cadence. When a card is absent, treat the reminder as the prompt to
decide whether the current work is meaningful enough to warrant a card; when a
card is present but its body has not changed for many turns, treat it as the
prompt to update it or `remove` it if the underlying task is done.

The counter resets whenever the card is successfully published (`start`, a
successful watch refresh, or restart resume), so an actively refreshing watch
never reaches the threshold — the reminder surfaces only when the card is
absent, retired, or its updates have stopped landing.

`start` runs a Python renderer under your working directory. The renderer must
exit `0` and print a nonempty full body to stdout; that body is written to
`taskcard/taskcard.md`. After the body is written atomically, the capability
writes `taskcard/status` as the exact text `active`.

`retry` reruns the renderer now for the same watch. Successful updates replace
only `taskcard/taskcard.md` atomically; the status stays `active`.

`stop` writes `taskcard/status` as `inactive` before stopping the updater. The
last body stays on disk. Consumers treat non-`active` status as no-op. `stop`
is for pausing/preserving a card you may still `retry` or reinspect — it is
not lifecycle cleanup.

`remove` is the terminal lifecycle action: it retires any active watch exactly
like `stop` (write `inactive`, then wait for the updater to quiesce) and then
deletes `taskcard/taskcard.md`, so it never races the updater into recreating
a body it just removed. It takes no `watch_id` — it targets your one artifact,
not a specific in-memory watch — so it still works after a restart lost the
watch handle. Call `remove`, not a shell/file-tool delete, once the underlying
work is completed, cancelled, or abandoned, so `/taskcard` and other consumers
cannot keep exposing a stale card. `remove` is idempotent: calling it again
after a successful removal, or when no watch was ever started, still leaves
`status` at exact `inactive` and reports no body to delete, never an error. If
the watch will not quiesce (the same failure `stop` can report), `remove`
reports that failure and leaves the body untouched so you can retry once the
updater is actually stopped.

## Cadence and safety defaults

`start` accepts optional `interval_s`, `timeout_s` (one renderer execution,
not the watch's whole lifetime), and `max_refreshes`. Omitted values use this
agent's configured defaults, persisted at `taskcard/taskcard.json`: `interval_s:
5`, `timeout_s: 10`, `max_refreshes: 2000`, `reminder_turns: 10`, unless an
operator has configured different values in that file.

`timeout_s` and `max_refreshes` are safety ceilings: an explicit value may
lower the configured ceiling but never exceed it — a request above the
ceiling is silently capped to it, it is not an error. `interval_s` has no
ceiling; only the absolute floor of 1 second applies, so requesting a slower
(larger) interval than the default is always honored — a numerically larger
interval is a safer, not a forbidden, choice.

Guidelines:

- Keep one active watch per agent. A second `start` is refused until the first
  watch is stopped.
- Restart a watch that expires mid-task. Exhausting `max_refreshes` retires the
  watch and sends one `task_card.limit` notification; if the underlying work is
  still ongoing, `start` a new watch rather than letting the card go dark.
- Write the body you want projected. The producer is channel-neutral and does
  not own Telegram/Feishu/portal layout details.
- Keep renderer output truthful and complete. Projection channels may compare
  file content byte-for-byte and update only on real changes. A channel that
  skips unchanged bytes still performs a real update whenever your renderer's
  output actually changes — choose `interval_s` and how often your renderer's
  output changes deliberately, since some consumer transports (e.g. Telegram)
  enforce their own message-edit/send rate limits on real changes.
