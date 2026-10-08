---
name: task-card-behavior-tests
behavior_version: 2
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/task_card/CONTRACT.md
  - src/lingtai/tools/task_card/ANATOMY.md
  - src/lingtai/tools/task_card/__init__.py
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/kernel/tool_plugin/__init__.py
  - src/lingtai/mcp_servers/task_card/resident.py
  - tests/test_task_card_controller.py
  - tests/test_task_card_notifications.py
  - tests/test_tool_settings_contract.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  task-card behavior clause changes, update the guarding LABT here in the same
  change.
---
# Intrinsic Task Card Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/task_card/CONTRACT.md` (start writes body then exact active,
second start fails closed, stop/remove semantics, watch persistence, the
read-only settings provider, and the typed notification boundary). Pinned
pytest commands must run from the repo root with the project's Python.

## Behavior TK001 — start writes the body atomically before exact active, and a second start fails closed

- **id**: TK001
- **title**: start writes the body atomically before exact active, and a second start fails closed
- **guards**: `intrinsic-task-card` § Behavior
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch agent working directory `<scratch>` with a renderer script inside it
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_task_card_controller.py tests/test_task_card_resident_shared.py -q` and capture the outcome.
2. In `<scratch>`, start a watch with a renderer that prints a body; verify `taskcard/taskcard.md` is written atomically before `taskcard/status` becomes exact `active`, and that `taskcard/watch.json` is persisted.
3. Start a second watch while the first is active and record the result; then call `remove` twice and record both results.

### Expected evidence
- [ ] Step 1: the task-card controller and resident suites pass, pinning route/slot, old-first rotation, peer adoption, and failure-state transitions.
- [ ] Step 2: the body file precedes the exact-`active` status write; the watch descriptor survives for restart resume; renderer failures preserve the last valid body and emit deduped typed `task_card.error` error/recovered notifications.
- [ ] Step 3: a second `start` fails closed (at most one active watch per agent); `remove` retires the watch first (writes `inactive`, joins the updater), then deletes the body; a repeated `remove` is idempotent and never an error.

### Pass / Fail
Pass when the suites pass and the ordering/idempotency observations hold. Fail on `active` before the body write, on a second concurrent watch, or on a non-idempotent `remove`; record the evidence trail in the task report.

## Behavior TK002 — typed Task Card notifications preserve wire parity and reject foreign fields

- **id**: TK002
- **title**: typed Task Card notifications preserve wire parity and reject foreign fields
- **guards**: `intrinsic-task-card` § Notification boundary
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>` and the family-owned notification test fixture
- **estimate**: ≈ 10 minutes

### Steps
1. From `<repo>`, run `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_task_card_notifications.py` and capture the outcome.
2. Inspect the recorded events for error, recovered, and refresh-limit operations, produced through the production `AgentTaskCardNotificationsAdapter` (the kernel `TaskCardNotificationsPort`); verify the exact source, explicit `system` channel, idempotency key, priority, and bounded `extra` fields, followed by one reminder submit and clear.
3. Attempt to construct an event with a foreign `source`, pass a foreign `channel`/`extra` keyword to a typed operation, pass a foreign `source`/`channel`/`extra`/`priority` keyword to each native port operation, hand the family adapter a port that offers a generic `enqueue_system_notification`, and submit malformed reminder turns; record that each fails before publication.
4. Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_tool_plugin_declaration.py::test_official_task_card_manager_holds_only_the_native_notification_operations` and confirm the live bound manager holds only the typed view over a granted port whose public surface is exactly `clear_reminder`, `publish_error`, `publish_limit`, `publish_recovered`, `submit_reminder`.

### Expected evidence
- [ ] Step 1: the family-owned typed notification suite passes.
- [ ] Step 2: error/recovered/limit output matches the established producer wire forms, including recovered-on-`task_card.error` state parity and `task_card.limit` refresh identity, with the production adapter (not a test double) between the typed events and the recorded publisher.
- [ ] Step 3: source/channel/foreign-field and malformed-reminder negatives fail closed at both the typed forms and the native operations; a generic-publisher port is refused; no `enqueue_system_notification` operation is visible on the granted port or the retained family adapter.
- [ ] Step 4: `1 passed` — the live Agent grants exactly the five closed operations and the manager keeps no lifecycle port, host, or Agent.

### Pass / Fail
Pass when the typed suite passes, all three event forms retain their exact wire parity, and every foreign-field/source/channel attempt fails before publication at both boundaries. Fail if a caller can choose a source/channel, inject arbitrary publisher metadata, reach a generic publisher through the granted port, or alter the established event identity.

## Behavior TK003 — settings SHOW stays exact, read-only, and owner-bounded

- **id**: TK003
- **title**: settings SHOW stays exact, read-only, and owner-bounded
- **guards**: `intrinsic-task-card` § Behavior rule 15
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>` and the focused Task Card fixtures
- **estimate**: ≈ 10 minutes

### Steps
1. From `<repo>`, run `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/test_task_card_controller.py tests/test_tool_settings_contract.py` and capture every outcome.
2. Inspect the five rows and verify exact key order, fresh current/default values, configurable flags, five-field-only projection, and exact owner-manual anchors.
3. Change valid and invalid owner-document fields, preview a customized legacy ceiling before migration, force the provider to fail, and attempt a nonempty settings input.
4. Verify SHOW creates or changes no owner document, returns no partial inventory on failure, omits paths/body/watch/unknown fields, and leaves ordinary Task Card lifecycle behavior unchanged.

### Expected evidence
- [ ] Step 1: the owner and generic settings suites pass with the exact cumulative production opt-in set.
- [ ] Step 2: every row is exactly `key`/`current`/`default`/`configurable`/`comment`, and every comment names a real Task Card manual heading.
- [ ] Step 3: current values follow the runtime's owner-document validation and built-in fallback, while a pre-migration custom legacy ceiling is previewed without a write; invalid input and unavailable truth fail closed.
- [ ] Step 4: no write, migration, operational-state leak, partial row, or ordinary lifecycle change is observed.

### Pass / Fail
Pass when the five rows remain exact and truthful, change procedures stay outside SHOW, failure is bounded and whole-action, and existing Task Card behavior is unchanged. Fail on an extra field, stale value, writer, path/body leak, missing manual target, unrelated owner opt-in, or lifecycle regression.
