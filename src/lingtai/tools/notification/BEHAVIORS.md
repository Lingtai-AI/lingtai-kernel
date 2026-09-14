---
name: notification-tool-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/tools/notification/schema.py
  - src/lingtai/tools/notification/settings.py
  - src/lingtai/kernel/notifications.py
  - tests/test_daemon_attention_delay.py
  - tests/test_notification_settings.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  notification tool behavior clause changes, update the guarding LABT here in
  the same change. N001-N003 guard the notification hook-registry clauses
  added by PR #1337 (unregistered channel blocked, registered channel
  passes through, lifecycle validation). N004 guards the `delay` clause for the
  aggregate `daemon` target (attention masked, truth readable, independent
  channels still wake, expiry restores attention); update it whenever that
  clause or `coherent_attention_read`'s daemon mask changes.
  N005 guards the read-only five-field Notification settings projection.
---
# Notification Tool Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/notification/CONTRACT.md` (manual read-only, check/current
state, narrow dismiss actions, hook-registry allowlist, Store semantics
preserved). Pinned pytest commands must run from the repo root with the
project's Python.

## Behavior N001 — unregistered channels are blocked; manual stays read-only

- **id**: N001
- **title**: notification check reports current state without mutating producer state, and an unregistered hook channel is blocked with a `blocked_channel:<channel>` system event
- **guards**: `notification-tool` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with a populated `.notification/`
- **estimate**: ≈ 15 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_notification_tool.py -q` and capture the outcome.
2. Call `notification(action="manual", input={}, reasoning="probe")` and record the result; hash `.notification/` before and after.
3. Call `notification(action="check", input={}, reasoning="probe")` and confirm it reports current channel state without mutating it.
4. Publish an event to an unregistered channel (e.g. write `.notification/unregistered.json`) and confirm a `blocked_channel:<channel>` system event is emitted.

### Expected evidence
- [ ] Step 1: the notification-tool suite passes.
- [ ] Step 2: `manual` returns installed per-agent guidance; `.notification/` is byte-identical.
- [ ] Step 3: `check` reports current state without mutating it.
- [ ] Step 4: the unregistered channel is blocked and the system event names it.

### Pass / Fail
Pass when the suite passes, read-only manual/check observations hold, and the unregistered channel is blocked with the named system event. Fail on a mutating `manual`, on `check` changing state, or on an unregistered channel passing through; record the evidence trail in the task report.

## Behavior N002 — registered hook channels pass through

- **id**: N002
- **title**: an event published to a registered hook channel passes through to notifications
- **guards**: `notification-tool` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a scratch agent working directory `<scratch>` with a registered hook channel (see `notification(action="add", ...)`)
- **estimate**: ≈ 10 minutes

### Steps
1. Register a hook channel (e.g. `notification(action="add", input={"name": "probe", "channel": "mcp.probe", ...})`).
2. Publish an event to `.notification/mcp.probe.json`.
3. Call `notification(action="check", input={}, reasoning="probe")` and confirm the event is reported.

### Expected evidence
- [ ] The hook manifest is written to `.notification/hooks.json` and the channel is allowlisted.
- [ ] The published event appears in `notification(action="check")` output.

### Pass / Fail
Pass when the registered channel's event is reported. Fail if it is blocked or dropped; record the evidence trail in the task report.

## Behavior N003 — hook-registry lifecycle validation

- **id**: N003
- **title**: notification hook add/drop/edit/list validate inputs and keep the manifest consistent
- **guards**: `notification-tool` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a scratch agent working directory `<scratch>` with `.notification/` writable
- **estimate**: ≈ 10 minutes

### Steps
1. Call `notification(action="add", input={"name": "dup", "channel": "mcp.dup", ...})` twice and confirm the second add is refused as a duplicate.
2. Call `notification(action="edit", input={"name": "dup", ...})` to change its description, then `list` to confirm the edit landed.
3. Call `notification(action="drop", input={"name": "dup"})` and confirm `list` no longer shows it and the channel is revoked from the effective allowlist.

### Expected evidence
- [ ] Duplicate add refused; edit reflected in list; drop removes manifest and revokes the channel.
- [ ] `.notification/hooks.json` stays valid JSON throughout.

### Pass / Fail
Pass when add/edit/drop behave as above and the manifest stays valid. Fail on silent acceptance of duplicates or a manifest left inconsistent; record the evidence trail in the task report.

## Behavior N004 — delaying `daemon` masks attention only

- **id**: N004
- **title**: a live `delay` on the aggregate `daemon` channel keeps daemon truth readable and lets independent channels wake the parent, and its expiry restores daemon attention with one `delay-alarm` mirror
- **guards**: `notification-tool` § Behavior (`delay`)
  ([CONTRACT.md](CONTRACT.md#behavior))
- **supersedes**: `tests/test_daemon_attention_delay.py` (mask, hook-wake, and expiry assertions)
- **runner**: any LingTai agent with the `notification`, `daemon`, and `shell` tools
- **prerequisites**: an agent working dir with a writable `.notification/`; no live delay (call `notification(action="delay", input={"channel": "daemon", "seconds": 0}, reasoning="reset")` first if unsure); no `channels.daemon.alarm_threshold` in `<workdir>/notification.json`
- **estimate**: ≈ 5 minutes

### Steps
1. Call `daemon(action="emanate", input={"tasks": [{"task": "Reply with exactly: DONE", "tools": []}]}, reasoning="probe")`; record `ids[0]` as `<id1>` and wait for its terminal notice.
2. Call `notification(action="delay", input={"channel": "daemon", "seconds": 60}, reasoning="probe")`.
3. Call `notification(action="add", input={"name": "probe", "channel": "probe", "source": "external", "description": "carrier", "how_to_modify": "edit the manifest", "how_to_cancel": "stop the writer"}, reasoning="probe")`.
4. Emanate a second identical task as `<id2>`. While the delay is live, call `notification(action="check", input={}, reasoning="probe")` and list `.notification/daemon/` with the `shell` tool (`ls -la .notification/daemon/`).
5. Write one event to `.notification/probe.json` (the registered hook channel) and observe whether it is delivered/injected.
6. Wait out the remaining 60-second window (the heartbeat/timer publishes expiry), then call `notification(action="check", input={}, reasoning="probe")` and read `.notification/delay-alarm.json` with the `shell` tool (`cat .notification/delay-alarm.json`).

### Expected evidence
- [ ] Step 2 returns `{"status": "ok", "action": "delayed", "channel": "daemon", ...}`.
- [ ] Step 4: `check` still reports the daemon channel and both `<id1>.json` and `<id2>.json` exist under `.notification/daemon/`; the `<id2>` arrival injected no notification pair and did not wake the agent.
- [ ] Step 5: the `probe` hook-channel event is delivered normally while the daemon delay is still live.
- [ ] Step 6: `.notification/delay-alarm.json` holds exactly one high-priority mirror naming target `daemon` with its requested/actual duration, and daemon attention is restored (a further daemon arrival wakes/injects again).
- [ ] No daemon mini-file, terminal receipt, or `daemons/<id>/daemon.json` was rewritten or removed at any step.

### Pass / Fail
Pass when all evidence is observed and no forbidden side effect occurs. Fail if the daemon channel disappears from `check`/`.notification/daemon/` while delayed, if a daemon arrival wakes or injects during the window, if the registered hook channel is suppressed too, or if expiry leaves the mask in place or publishes no `delay-alarm`; record the evidence trail in the task report.

## Behavior N005 — Notification settings are read-only five-field disclosure

- **id**: N005
- **title**: settings shows two exact effective rows and routes changes to owner procedures
- **guards**: `notification-tool` § Port (`settings`)
  ([CONTRACT.md](CONTRACT.md#port))
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; no ambient Notification test environment overrides
- **estimate**: ≈ 2 minutes

### Steps
1. Run `python -m pytest -q tests/test_notification_settings.py` from `<repo>`.
2. Call `notification(action="settings", input={}, reasoning="inventory")`.
3. Confirm each `comment` resolves to the named heading in the installed Notification manual.
4. Call ordinary `notification(action="check", input={}, reasoning="non-regression")`.

### Expected evidence
- [ ] The row keys are exactly `notification.max_chars` and `notification.delay_max_seconds`, in that order.
- [ ] Every row has exactly `key`, `current`, `default`, `configurable`, and `comment`; defaults are `10000` and `600`, and both rows are configurable through authorized procedures outside SHOW.
- [ ] The cap reflects live environment → System-v2 file hook → default precedence, while the delay ceiling reflects live environment → default.
- [ ] `settings` accepts only `input={}`, performs no write, and an unavailable current value fails the whole action with the fixed bounded failure.
- [ ] Both comments name real manual headings containing the omitted semantics and change procedures.
- [ ] Ordinary `check` retains its existing placeholder behavior.

### Pass / Fail
Pass when the focused suite and all expected evidence hold. Fail on an extra row or field, stale file-layer current, a mutation form, partial unavailable output, a dangling comment target, or changed `check` behavior; record the evidence trail in the task report.
