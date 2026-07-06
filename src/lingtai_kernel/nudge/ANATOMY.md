---
related_files:
  - CLAUDE.md
  - src/lingtai_kernel/ANATOMY.md
  - src/lingtai_kernel/nudge/__init__.py
  - src/lingtai_kernel/nudge/goal.py
  - src/lingtai_kernel/nudge/kernel_version.py
  - src/lingtai_kernel/nudge/source_drift.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# Nudge

Per-agent periodic checks that emit notification nudges or reminders when
something needs the agent's attention. Runtime/kernel update nudges share
`.notification/nudge.json` and keep throttle state in
`.notification/.nudge_state.json`; goal reminders read protected
`.notification/goal.json` and publish short dismissible events into
`.notification/system.json`. Designed so additional mechanical checks land as
small additions (e.g. MCP version drift, addon updates).

## Entry point

`run_checks(agent)` is called once per heartbeat tick from
`base_agent/lifecycle.py:_heartbeat_loop` (wrapped in try/except so a
bad check never breaks the loop). It dispatches to each check's
`check(agent) -> None` in order.

**Invariant — checks must never block on the network (issue #730).** The
heartbeat loop is the sole writer of `.agent.heartbeat`, and
`handshake.is_alive` reads a tick older than 2.0s as *dead*. The try/except
wrapper only protects against a check that *raises* — it does nothing for one
that is *slow*, and latency inside any check is latency between heartbeat
writes. A network probe on the heartbeat thread can therefore make a live agent
read as dead (mail delivery, karma, cpr all gate on `is_alive`). Any check that
needs the network must hand that work to a background daemon thread and consume
the result on a later tick — see `kernel_version.py` for the pattern.

## File layout

- `__init__.py` — dispatcher (`run_checks`), shared upsert/remove
  helpers (`upsert`, `remove`) that operate on the `nudge.json`
  multi-entry payload under a lazy per-agent lock. Runs `kernel_version.check`,
  `source_drift.check`, and then `goal.check` once per heartbeat tick.
- `kernel_version.py` — read-only runtime/update check. It first detects
  whether the installed `lingtai` distribution on disk differs from the running
  `lingtai.__version__` and emits a fast local refresh nudge. For packaged,
  non-editable/non-dev runtimes, it also performs an at-most-once-per-UTC-day
  package-index check for a newer kernel version and nudges the agent to read
  `system-manual -> reference/runtime-update-checks/SKILL.md` before asking the
  human whether to update. It stores daily throttle state in the hidden
  `.notification/.nudge_state.json` helper file, which is not a channel. The
  PyPI probe never runs on the heartbeat thread (see the invariant above): the
  due `check()` spawns a daemon worker (`_start_fetch`) that writes only into a
  process-local `_PendingFetch` slot, and a later probe (past the 60s fast gate)
  consumes the result and emits/clears the nudge. All `.nudge_state.json` writes
  stay on the heartbeat thread, keeping it the single writer; a wedged worker is
  abandoned after `_FETCH_DEADLINE_SECONDS`. The nudge therefore surfaces up to
  ~60s after the fetch completes rather than in the same tick.
- `source_drift.py` — read-only process/source freshness check. It compares the
  startup runtime fingerprint with the current on-disk fingerprint and emits a
  low-priority refresh nudge only for non-dev/non-editable/non-source runtimes;
  development checkouts are skipped so agents are not nudged into arbitrary
  in-flight source changes.
- `goal.py` — IDLE-only goal reminder check. It reads the allowlisted protected
  `.notification/goal.json`; if and only if that file exists, is active, and the
  idle delay has elapsed, it publishes one short `goal.reminder` event into
  `.notification/system.json` saying to read `goal.json` and the goal manual.
  It dedupes an existing reminder with the same `ref_id` and waits another delay
  after that reminder is dismissed.
- `ANATOMY.md` — this file.

## The shared channel

All nudges share `.notification/nudge.json` with this shape:

```json
{
  "header": "1 nudge",
  "icon": "🔔",
  "priority": "low",
  "instructions": "Call notification(action='dismiss_channel', channel='nudge') ...",
  "data": {
    "nudges": [
      {"kind": "kernel_version", "title": "...", "detail": "...", ...}
    ]
  }
}
```

Each entry's `kind` is its slot key — `upsert(agent, kind, body)`
replaces by `kind`, `remove(agent, kind)` drops by `kind`. When the
list empties, the channel file is deleted entirely so the wire surface
drops the notification cleanly. The agent dismisses everything at once
with `notification(action='dismiss_channel', channel='nudge')`.

## Adding a new nudge

1. Drop `nudge/<name>.py` with a top-level `check(agent) -> None`
   function. Inside:
   - Throttle. In-memory state on `agent._nudge_<name>_state` is fine for
     short cadence checks; use a small non-channel state file (for example
     `.notification/.nudge_state.json`) when the cadence must survive refreshes.
   - Probe whatever you need to check.
   - On hit: call `upsert(agent, "<unique_kind>", body)` where `body`
     is the per-kind payload dict you want the agent to read.
   - On clear: call `remove(agent, "<unique_kind>")` and reset your
     dedupe state.
2. Add `from . import <name>` and `<name>.check(agent)` to
   `__init__.py:run_checks`.

Keep checks small, side-effect-free except for the upsert/remove call,
and well-throttled. They run inside the heartbeat loop on a 1-second
tick.

## Why not a Check protocol / registry?

Three similar lines is better than a premature abstraction
(CLAUDE.md). At this small scale, the per-check throttle boilerplate is still cheaper
than maintaining a `Check` protocol and a registry. If the duplication starts
to hurt, lift a `_throttled_probe` helper into `__init__.py` — but the right
abstraction shape will be obvious by then.

## Wire surface

The nudge channel flows through the standard `.notification/` sync
machinery (`base_agent/__init__.py:_sync_notifications` →
`meta_block.py` → wire). No special wire path. The agent sees it in
the meta-block alongside any other active notifications.

## Failure isolation

The heartbeat-loop call site wraps `run_checks` in try/except and logs
to the kernel logger on failure. `run_checks` also dispatches each check through
`_run_one`, so a bug in one individual check is logged as `nudge_check_error`
and does not block subsequent checks. Add local try/except inside a check only
when it needs more specific cleanup or telemetry.
