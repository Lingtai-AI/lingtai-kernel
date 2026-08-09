# Hook-registry PR status (feat/notification-hook-registry)

Status summary for the notification hook-registry + whitelist PR. Core
implementation landed in `d6deaae9`; this branch's remaining workstreams
(tests, docs, orphan verification, commits) are complete below.

## Commits

| Hash | Message | Workstream |
|---|---|---|
| `d6deaae9` | feat(notification): hook registry + whitelist (add/drop/edit/list) | core implementation (pre-existing, parent-provided) |
| `af269996` | test(notification): hook registry lifecycle + whitelist gate tests (family 8) | 1. NEW TESTS |
| `46f6130b` | docs(notification): hook registry family-8 contract/anatomy + manual updates | 2. DOCS/CONTRACT/ANATOMY |
| HEAD | docs(notification): hook-registry PR status summary (this file) | 4. status file |

## Workstream 1 — NEW TESTS (committed `af269996`)

- `tests/conftest.py`: autouse `_isolate_notification_hook_registry` fixture
  (snapshot/restore of `_REGISTERED_HOOK_CHANNELS`, `_HOOK_REGISTRY_SEEDED`,
  `_BLOCKED_CHANNEL_WARNED` + allow-predicate invalidation) — the identical
  treatment the map (§6) prescribed alongside `_GENERIC_DISMISS_GUARDED`.
- `tests/test_notification_store.py`: hook-registry family tests mirroring the
  ack-refs tests — `load_hook_manifests` absent → `[]` (extended
  `test_missing_state_contract`), malformed → `[]` (two tests incl. atomic add
  over a malformed file), `update_hook_manifests` atomic result/typed
  changed/value (parametrized fake+posix), atomic append-and-clear,
  concurrent family-8 appends.
- `tests/test_notification_tool.py`: `TestHookLifecycle` (15 tests) —
  add→list, duplicate_name, channel_in_use (add+edit), edit fields,
  edit/drop not_found, drop removes + revokes allowlist, add→edit→drop→list
  round trip through the family dispatcher, whitelist gate
  (registered ⇒ `is_channel_allowed` True after `sync_hook_registry`;
  unregistered stay blocked incl. `submit` refusal), warn-and-flag dedupe
  (twice → one enqueue; after `clear_blocked_channel_warning` → second;
  allowed channels never flag), fake-store seeding, reset isolation +
  re-seed, malformed-registry sync to empty.

## Workstream 2 — DOCS/CONTRACT/ANATOMY (committed `46f6130b`)

- `src/lingtai/kernel/notification_store/CONTRACT.md`: `contract_version` 1 → 2,
  "exactly eight operation families", Port list gains family 8
  (`load_hook_manifests`/`update_hook_manifests`), "eighth" → "ninth" MUST-NOT
  rule, hook-registry load/update contract rules mirroring the ack rules,
  eight-family conformance paragraph.
- `src/lingtai/kernel/notification_store/ANATOMY.md`: eight families,
  `UpdateHookManifestsResult` component, hooks.json in State, adapter citation
  refreshed to current lines.
- `src/lingtai/kernel/ANATOMY.md`: notification-store node updated from seven
  to eight families (family 8 named), citations refreshed.
- `src/lingtai/tools/notification/ANATOMY.md`: nine actions, nine-child
  registry, four hook handlers, family-8 Store-operation note (replacing
  "This tool does not add a Store operation"), State section, stale
  citations refreshed.
- `src/lingtai/tools/notification/CONTRACT.md`: `contract_version` 2 → 3,
  eight operational actions, action-domain order list, per-action inputs for
  add/drop/edit/list, observable hook contracts, posture note revised
  (add/drop/edit mutate the registry), glossary-review note, contract-tests
  paragraph.
- `src/lingtai/tools/notification/glossary-{zh,wen,en}.md`: review-only — they
  enumerate no actions (only the `notification` term); no change needed.
- `src/lingtai/intrinsic_skills/notification-manual/SKILL.md`: new
  "Hooks & whitelist" section (setup flow, manifest fields, drop/edit/list
  semantics, warn-and-flag, comm_watcher worked example), nine-action quick
  start, routing-table row, version 0.6.0 → 0.7.0.
- `.../reference/channel-model/SKILL.md`: effective allowlist = static set ∪
  `mcp.*` prefix ∪ registered hook channels from `.notification/hooks.json`;
  unknown files still ignored; blocked attempts visible via warn-and-flag;
  hooks.json in footprint; version → 0.3.0.
- `.../reference/dismissal-safety/SKILL.md`: hook channels + producer-guard
  interplay; drop does not kill processes (`how_to_cancel` is the owner's
  job); version → 0.3.0.
- `tests/_notification_store_helpers.py`: stale "seven-family" module
  docstring → "eight-family".

## Workstream 3 — ORPHAN VERIFICATION (no commit; nothing to fix)

- `pytest tests/test_architecture_documents.py` and
  `tests/test_anatomy_drift_checker.py` run; the only architecture failure is
  the **pre-existing** `src/lingtai/kernel/agent_readme/ANATOMY.md` orphan
  (introduced by `98920491`, verified identical on the pristine baseline).
- Every doc changed/added by this PR stays reachable from an ANATOMY.md
  `related_files` list (notification-store CONTRACT↔ANATOMY, tool
  CONTRACT↔ANATOMY, tool ANATOMY lists all three notification-manual SKILL
  files, kernel ANATOMY is the root).

## Test counts

- `tests/test_notification_store.py`: **45** collected (was 39) — 44 passed,
  1 pre-existing deselected (telegram ServerRequestContext ImportError).
- `tests/test_notification_tool.py`: **68** collected (was 52) — 68 passed.
- Combined run: **112 passed, 1 deselected** (2.1 s).
- New tests in this PR: **22 cases** (15 tool lifecycle/whitelist + 7 store
  hook-family cases; the two conformance tests parametrize fake+posix).

## Remaining open items

1. Pre-existing test failure (task says ignore; do not fix):
   `test_notification_store.py::TestCompositionAndProvenance::test_telegram_server_composes_one_store_and_injects_same_instance`
   — mcp library `ServerRequestContext` ImportError.
2. Pre-existing environment failures (verified identical on pristine
   baseline): the two `test_anthropic_send_none_*` tests in
   `tests/test_notification_sync.py` (anthropic SDK not installed); the
   `tests/test_tools_package_data.py` wheel/sdist suite (build/install env);
   `tests/test_docs_governance.py` (`agent_readme` orphan +
   `tmp-notification-hook-map.md` lacks frontmatter);
   `tests/test_tool_glossary.py::TestSchemaInvariance::test_glossary_metadata_and_body_never_reach_provider_wire`.
3. Pre-existing architecture orphan:
   `src/lingtai/kernel/agent_readme/ANATOMY.md` is not linked from any parent
   ANATOMY `related_files` (introduced by `98920491`); fixing it is outside
   this PR's scope.
4. Stale comment left untouched because the file is on the do-not-touch list:
   `src/lingtai/tools/notification/__init__.py:104` still says "fixed
   five-child registry" (should be nine-child).
5. Not done per instructions: no push, no PR opened, no fable run.
