---
name: notification-behavior-tests
behavior_version: 2
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/notification/CONTRACT.md
  - src/lingtai/tools/notification/ANATOMY.md
  - src/lingtai/tools/notification/__init__.py
  - src/lingtai/kernel/notifications.py
  - src/lingtai/intrinsic_skills/notification-manual/SKILL.md
  - tests/test_notification_tool.py
maintenance: |
  This file records agent-executable behavior tests (LABTs) for the notification
  hook-registry whitelist. Update it whenever the notification CONTRACT's
  observable behavior clauses change (add/drop/edit/list lifecycle, warn-and-flag
  on blocked channels, per-agent allowlist). The root BEHAVIORS.md owns the LABT
  specification and tridirectional linkage; keep this file linked from root
  BEHAVIORS.md related_files exactly once and keep every LABT self-contained.
---
# Notification Tool Behavior Tests

LingTai Agent Behavior Tasks proving the notification hook-registry whitelist
contract does not drift. Each LABT is self-contained: an agent can execute it
verbatim against a real runtime and judge pass/fail from observable evidence.

## Behavior B001 — unregistered channel is blocked and flagged

- **id**: B001
- **title**: unregistered channels are blocked with a visible warning
- **guards**: `notification-tool` § Behavior / Contract rules
  (warn-and-flag: unregistered channels do not pass through)
- **supersedes**: `tests/test_notification_tool.py::test_hook_*` (complements)
- **runner**: any LingTai agent with the `notification` tool and shell access
  to its own working directory
- **prerequisites**: agent working directory exists;
  `.notification/hooks.json` is absent or empty (no hooks registered);
  runtime kernel includes PR #1337 (hook registry)
- **estimate**: ~2 min

### Steps
1. Write an unregistered channel file:
   `cat > .notification/unregistered_test.json <<'EOF'
   {"header": "blocked test", "icon": "🧪", "priority": "high",
    "published_at": "<now-iso>", "data": {"message": "should be blocked"}}
   EOF`
2. Run `notification(action='check', input={})` (or wait for the idle sync).
3. Observe the `system` channel for a `notification_hook` event with
   `ref_id: blocked_channel:unregistered_test`.
4. Confirm the `unregistered_test` channel itself does NOT appear in
   `_meta.agent_meta.notifications.attention`.
5. Clean up: `rm .notification/unregistered_test.json` and dismiss the system
   event.

### Expected evidence
- [ ] System event present: `source: notification_hook`,
      `ref_id: blocked_channel:unregistered_test`, body naming the channel and
      pointing to `notification(list)` / `notification(add)`.
- [ ] The unregistered channel's payload did NOT pass through (absent from
      attention/persistent payloads).
- [ ] `.notification/unregistered_test.json` removed after cleanup.

### Pass / Fail
Pass when the system warning event exists with the correct ref_id AND the
blocked payload did not surface. Fail if the payload passes through, the event
is missing, or the event names the wrong channel. No forbidden side effect:
`add`/`drop`/`edit`/`list` are not invoked by this LABT.

## Behavior B002 — registered hook channel passes through

- **id**: B002
- **title**: registered hook channels pass through without warning
- **guards**: `notification-tool` § add / Contract rules
  (registered hook channels enter the effective allowlist)
- **supersedes**: `tests/test_notification_tool.py::test_hook_*` (complements)
- **runner**: any LingTai agent with the `notification` tool and shell access
  to its own working directory
- **prerequisites**: agent working directory exists; runtime kernel includes
  PR #1337; the agent can write `.notification/<channel>.json`
- **estimate**: ~2 min

### Steps
1. Register a temporary hook:
   `notification(action='add', input={'name': 'test_hook',
   'channel': 'test_hook', 'source': 'smoke-test',
   'description': 'temporary pass-through verification',
   'how_to_modify': 'notification edit test_hook',
   'how_to_cancel': 'notification drop test_hook'})`.
   Expect `{status: "ok", reason: "added", name: "test_hook"}`.
2. Publish to the registered channel:
   `cat > .notification/test_hook.json <<'EOF'
   {"header": "registered test", "icon": "✅", "priority": "high",
    "published_at": "<now-iso>", "data": {"message": "should pass through"}}
   EOF`
3. Run `notification(action='check', input={})` (or wait for the idle sync).
4. Confirm the `test_hook` channel appears in
   `_meta.agent_meta.notifications.attention` with the published payload and
   that NO `blocked_channel:test_hook` system event exists.
5. Clean up: `notification(action='drop', input={'name': 'test_hook'})` and
   `rm .notification/test_hook.json`.

### Expected evidence
- [ ] `add` returned `{status: "ok", reason: "added", name: "test_hook"}`.
- [ ] The registered channel's payload appeared in the notification surface.
- [ ] No blocked-channel system event was emitted for `test_hook`.
- [ ] Registry clean after `drop` (list shows empty or no `test_hook`).

### Pass / Fail
Pass when the registered channel's payload surfaces without a warning event.
Fail if the payload is blocked, a warning is emitted, or `add`/`drop` return an
error. Forbidden side effect: the hook must not outlive the test (drop it).

## Behavior B003 — hook registry lifecycle: add/drop/edit/list

- **id**: B003
- **title**: add/drop/edit/list lifecycle returns contract-matching reasons
- **guards**: `notification-tool` § add / § edit / § drop / § list
  (manifest validation, uniqueness, empty-name and reserved-stem refusals)
- **supersedes**: `tests/test_notification_tool.py::test_hook_add_drop_edit_list`
- **runner**: any LingTai agent with the `notification` tool
- **prerequisites**: agent working directory exists; runtime kernel includes
  PR #1337
- **estimate**: ~3 min

### Steps
1. `notification(action='list', input={})` → `{status: "ok", hooks: []}` (or
   without the test hook present).
2. `add` a hook named `test_hook` with channel `test_hook` → `reason: "added"`.
3. `add` the same name again → `status: "error"`, `reason: "duplicate_name"`,
   registry unchanged.
4. `add` a second hook with the same channel → `status: "error"`,
   `reason: "channel_in_use"`.
5. `add` a hook with channel `hooks` (Store-reserved stem) → `status: "error"`,
   `reason: "invalid_manifest"`.
6. `edit` `test_hook` changing `description` → `reason: "edited"`; list shows
   the new description.
7. `edit` with all null fields → `reason: "no_change"`.
8. `drop` `test_hook` → `reason: "dropped"`; `drop` again → `reason:
   "not_found"`; `list` is empty again.

### Expected evidence
- [ ] Every return reason matches the contract table above.
- [ ] Duplicate/conflicting/invalid `add` attempts leave the registry unchanged.
- [ ] After `drop`, the channel is revoked (an unregistered test file would be
      flagged per B001).

### Pass / Fail
Pass when all lifecycle reasons match the contract and invalid attempts are
no-ops on registry state. Fail on any mismatch. No forbidden side effect: all
hooks added during the LABT are dropped before completion.
