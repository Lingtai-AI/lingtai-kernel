# Notification hook-registry integration map (lingtai-kernel)

Goal: add notification **add/drop/edit/list** tool actions, a `.notification/hooks.json` disk registry (list of hook manifests), effective allowlist = static set ∪ `mcp.*` prefix ∪ registered hook channels, and warn-and-flag (blocked unregistered channel attempts emit a system warning event). Anchors verified against the current worktree. No source edited.

## 1. `src/lingtai/kernel/notifications.py` (958 lines)

**TOUCH:**
- Allow constants: `_NOTIFICATION_CHANNEL_ALLOWLIST` :36-48; `_NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST = ("mcp.",)` :49.
- **Predicate layer = plug-in point for registered hook channels**: `_build_allow_predicate()` :90-105 (static-set + prefix branches; add a third: registered-hook lookup); `_get_allow_predicate()` :112-116 + cached `_allow_predicate` :109; `is_channel_allowed()` :142-150 (duplicated logic — keep in sync or delegate); `validate_allowed_channel()` :153-162 (error text `allowed=`/`allowed_prefixes=`; add `registered=`); `register_notification_channel()` :165-170 — copy this exact mutate-then-`_allow_predicate = None` cache-invalidation pattern (:167,:170) for add/drop.
- **Warn-and-flag**: `submit()` :194-245 — `validate_allowed_channel(tool_name)` :230, `store.publish` :244-245. Emit the system warning here (model `_enqueue_system_notification`, §9) when a channel is unregistered-but-attempted. `clear()` :253-268 (validate :263); `clear_with_result()` :275-284 (validate :282).
- System-event building blocks for the warning: `_system_events()` :385-390, `_system_payload_with_events()` :393-403, RMW model `clear_large_result_reminders()` :315-374 (`compare_update_channel("system", UNCONDITIONAL, _mutator)` :365).
- `dismiss_channel()` :511-927: validate :532; guarded refusal :604-630; stale refusal `_stale_channel_refusal` :453-496; system-vs-whole branches :920-923; `_signal_notification_dismissed` :941-958. Registered channels flow through automatically once `validate_allowed_channel` widens.
- Collection/payload paths live elsewhere: `meta_block.py` `build_notification_payload()` :1913-1937 (model-visible loop :1923-1929, `sources` :1921), `_collect_active_notifications_payload()` :2919-2936 (snapshot :2931), `attach_active_notifications()` :3064-3218, `_commit_notification_fp()` :3045-3061; `base_agent/__init__.py` `_sync_notifications` :1290+; `turn.py` IDLE check :1102-1112.

**Key finding**: the predicate is module-global + cached; `agent` is NOT available at predicate-build time. All 9 production call sites pass module-level `is_channel_allowed`/`_get_allow_predicate()` (§2). Registered channels therefore need a module-level registry keyed by agent workdir, consulted by `is_channel_allowed`/`_build_allow_predicate`, with cache invalidation on mutation (pattern :167-170). Do not thread per-agent predicates through the call sites.

## 2. `src/lingtai/kernel/base_agent/__init__.py` (2541 lines)

**TOUCH:**
- `NotificationStorePort` is a **required constructor dependency**: param :346, assigned :425 `self._notification_store = notification_store`. Never constructed in Core — composition roots `agent.py:142-151`, `cli.py:127-140` build the POSIX adapter.
- **No predicate at construction**; passed per call: `_sync_notifications()` :1290+ (`_allow` closure :1355-1356, `fingerprint` :1358, `snapshot` :1365; ASLEEP-wake + `_inject_notification_pair` :1385-1461); `_maintain_telegram_task_card()` :2459-2505 (fp :2473, snapshot :2478).
- Notification state attrs :624-666 (`_notification_fp` :632, `_notification_live_holder` :661, `_notification_payload_signature` :666) — add hook-registry state here or rely on the module registry.
- Other predicate call sites: `turn.py:202-206, 1102-1112`; `meta_block.py:2929-2931, 3055-3058`; `worker_recovery.py:87-90` (`_get_allow_predicate()`); `soul/flow.py:91-102`; `system/karma.py:80-86`.
- **Agent availability**: agent IS available at every call site and in every tool action handler (actions receive `agent`), but NOT inside the predicate. Mutate the registry in the new actions (they have `agent`): write `hooks.json` + update module registry + invalidate `_allow_predicate`.

## 3. `src/lingtai/kernel/notification_store/CONTRACT.md` (136 lines)

- Seven families :57-65; **must-change rule** :52-53 "MUST NOT add ... **eighth operation family** ..." and :57 "exactly seven operation families"; `contract_version` 1 (:3) → bump.
- **`load_ack_refs`/`update_ack_refs` (families 6/7, :64-65; Port :178-197) IS the model**: single non-channel JSON file, atomic read→pure-mutator→write-or-clear under the store's in-process+cross-process mutex, best-effort on absence (:103-109). New family 8: `load_hook_manifests() -> list[dict]` (absent/malformed → `[]`) and `update_hook_manifests(pure_mutator: Callable[[list[dict]], tuple[list[dict], bool, object]])` with a typed result. `hooks.json` is invisible to snapshot/fingerprint today (stem `hooks` fails `is_channel_allowed`) — no mirror collision.
- Alternative: sibling `HookRegistryPort` avoids the seven→eight surgery but adds a second required injection at :346,:425 and every composition root. Family-8 recommended (matches stated design).

## 4. `tests/test_notification_tool.py` (1175 lines)

**TOUCH** — extend these exact patterns for add/drop/edit/list:
- `_call()` helper :61-71 (dispatches through `notif_intrinsic.handle()`) — reuse as-is.
- **`_ACTIONS` list :112** (pinned everywhere): `_ACTIONS = ["check", "dismiss_channel", "dismiss_event", "dismiss_ref", "manual"]` — insert new actions in ACTION_ORDER order.
- Schema tests: `test_notification_schema_exposes_atomic_actions` :115-118; root-envelope :121-136; `test_each_action_input_branch_is_strict_and_exact` :139-159 (**`expected_props` dict :150-156**); enum prose :189-196 (loops `_ACTIONS`, asserts `f"{action}:"`); canonical English :206-227; no-kitchen-sink :199-203.
- Wire parity `test_family_schema_survives_chat_and_responses_wires` :905-936 (branch titles + `allOf` consts == `_ACTIONS`).
- `test_every_action_dispatches_through_the_family` :939-963 — add one call per new action (add→edit→drop→list round trip on `_StubAgent`/store helpers).
- `test_large_result_guard_every_atomic_action` :511-534 (dismiss verbs only; not for new actions). Fixtures: `_StubAgent` (`tests/_notification_helpers.py:17`), `publish_test_payload`/`snapshot_notifications`/`fingerprint_notifications` (`tests/_notification_store_helpers.py:45-50`).

## 5. `tests/test_notification_store.py` (664) + `tests/_notification_store_helpers.py` (206)

**TOUCH:**
- `TestSevenFamilyConformance.test_exact_seven_operation_families` :77-86 — **pins `NotificationStorePort.__abstractmethods__` to the 7-name set**; add the two new method names. Mirror ack tests :174-186 for hook manifests. Class :76-186; `conforming_store` fixture :70-73.
- `TestPosixContractErrorsAndEnvelope` :189-303 (allowlist snapshot :302); `_CoreAgent` :306-317; `_system_events` :320-322; `TestAtomicCoreRedCounterexamples` :325-588 (guard :533; concurrency :326-349,:417,:500-528); `TestCompositionAndProvenance` :591+ (submit signature pin :592-607).
- `FakeNotificationStore` `_notification_store_helpers.py:40-164` implements **all seven abstract methods** — breaks until the two new methods are added (mirror `load_ack_refs` :145-147 / `update_ack_refs` :149-164 with `_hook_manifests`/`_hook_present` state). Seeding pattern `replace_ack_refs_for_test` :202-206 → add `replace_hook_manifests_for_test`. Helpers :167-206 pass module `is_channel_allowed` (:181/:185) — unchanged.

## 6. Tests pinning allowlist logic (breakage surface)

- **No test references `_NOTIFICATION_CHANNEL_ALLOWLIST`/`_NOTIFICATION_CHANNEL_PREFIX_ALLOWLIST` directly**; none asserts unregistered channels stay invisible.
- Indirect predicate users: `_notification_store_helpers.py:28,181,185`; `test_notification_store.py:35,302,504`; `test_notification_sync.py:1340,1358` (fp equality — only breaks if hook channels appear in those workdirs); `test_system_dismiss.py:29,110-131` (invalid names hit the allowlist check; `reason in ("invalid_channel","missing_channel")` — keep those reasons for unregistered-but-valid channels), :135-140.
- `tests/conftest.py:39-44` **snapshots/clears `_GENERIC_DISMISS_GUARDED` for isolation** — the new module-level hook registry needs the identical treatment.
- `test_notification_tool.py:653-706` (guarded/stale/force/protected) use only allowlisted channels — unaffected unless registry mutates mid-test.

## 7. `tools/notification/ANATOMY.md` (181) + `CONTRACT.md` (226) — phrases to update

**ANATOMY.md**: :37-41 "It composes **five actions**: `check`, three atomic dismissal actions, and the strictly read-only `manual` action."; :48-49 "all **five action values** are unchanged"; :61-63 "fixed **five-child registry**"; :135-136 "**This tool does not add a Store operation.**"; :144-155 State; :159-162 Notes. Also review `services/LICC_NOTIFICATION_CONTRACT.md` (related_files :10, own doc test).
**CONTRACT.md**: :44-48 "It exposes **four operational actions** ... and introduces **no Notification Store operation**."; :58 "preserve **all four operational actions**"; :71-74 action domain order list; :81-86 per-action inputs; :188-191 "posture weaker than its strongest action" (add/drop/edit mutate the registry — revisit posture); :195-199 glossaries review on enum change; :200-203 `contract_version` 2 → bump to 3.

## 8. Glossary files

`src/lingtai/tools/notification/glossary-{en,zh,wen}.md` (15/18/18 lines) — **no action enumeration**; body policy forbids translating/duplicating schema/params/actions; only term is `notification`. Review-only on schema change (zh maintenance note ~:13-14). Ignore `build/lib/` copies.

## 9. Repo-wide searches

- **`_GENERIC_DISMISS_GUARDED`/`register_generic_dismiss_guard`**: def `notifications.py:66,173-181,184-186`; use :604; email registers `tools/email/__init__.py:42-47`; isolation `tests/conftest.py:39-44`; tests `test_system_dismiss.py:29,135-140`, `test_notification_store.py:37,533`.
- **`large_result_acks`/family 6/7**: `notifications.py:292-307,315-374`; Port `notification_store/__init__.py:178-197`; adapter `adapters/posix/notification_store.py`; fake `_notification_store_helpers.py:145-164,198-206`; tests `test_large_result_rescan.py`, `test_system_dismiss.py`, `test_notification_store.py`, `test_notification_sync.py`. This is the family to mirror.
- **Seven-family assumption sites** (all must move to eight, or stay untouched with a sibling Port): `notification_store/__init__.py:116` (docstring "Seven-family persistence boundary"), `notification_store/CONTRACT.md:53,57,122`, `notification_store/ANATOMY.md:34`, `kernel/ANATOMY.md:115`, `tests/test_notification_store.py:77-86`, `tests/_notification_store_helpers.py:3,203`. (`migrate/migrate.py:10`, `init_schema.py:207` "seven" are unrelated — different Port / `seven_tier`.)
- **System-warning model**: `base_agent/messaging.py:66-190` `_enqueue_system_notification` — appends to `.notification/system.json` via `compare_update_channel("system", UNCONDITIONAL, _mutator)` (:183), caps 20 events (:157), returns event_id; BaseAgent wrapper :1103. Use for warn-and-flag.

## Exact strings for editing

`ACTION_ORDER` (`src/lingtai/tools/notification/schema.py:48`):
```python
ACTION_ORDER = ("check", "dismiss_channel", "dismiss_event", "dismiss_ref", "manual")
```
`ACTION_ENUM_DESCRIPTION` tail (`schema.py:145-165`) ends exactly:
```python
    "dismiss_ref: remove system event(s) by ref_id from "
    ".notification/system.json (channel defaults to 'system' when null).\n\n"
    "manual: call notification(action='manual', input={}) to return the "
    "installed notification-manual skill body. This action is strictly "
    "read-only and does not read or change notification state."
) + "\n\n" + LARGE_RESULT_DISMISS_ACTION_NOTE
```
Pinned test list (`tests/test_notification_tool.py:112`):
```python
_ACTIONS = ["check", "dismiss_channel", "dismiss_event", "dismiss_ref", "manual"]
```
`get_description()` (`schema.py:168-169`, one line) also enumerates the actions in call examples — must gain the four new verbs.
