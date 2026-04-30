# Audit: §Source References in Avatar + Mail Anatomy Leaves

**Auditor:** audit-avatar-mail  
**Date:** 2026-04-30  
**Kernel commit:** HEAD at audit time  
**Scope:** 10 leaves — 4 avatar, 6 mail  

---

## Summary

| Leaf | ✅ | ⚠️ | ❌ | Total |
|------|----|----|-----|-------|
| `avatar/spawn` | 12 | 0 | 0 | 12 |
| `avatar/boot-verification` | 3 | 3 | 0 | 6 |
| `avatar/shallow-vs-deep` | 7 | 0 | 0 | 7 |
| `avatar/handshake-files` | 3 | 6 | 0 | 9 |
| `mail/dedup` | 4 | 0 | 0 | 4 |
| `mail/atomic-write` | 5 | 0 | 0 | 5 |
| `mail/scheduling` | 11 | 0 | 0 | 11 |
| `mail/identity-card` | 5 | 2 | 0 | 7 |
| `mail/mailbox-core` | 7 | 1 | 0 | 8 |

**Note:** The `mailbox-core` leaf was corrected between reads. The `_list_inbox`/`_read_ids` reference was initially `line 26` (❌), now corrected to `135 / 162` (✅). The counts above reflect the corrected version.
| `mail/peer-send` | 12 | 0 | 0 | 12 |
| **Totals** | **68** | **12** | **1** | **81** |

**Note (update):** The `mailbox-core` leaf's `_list_inbox`/`_read_ids` reference was corrected from `line 26` to `135 / 162` between reads. The corrected totals would be **69/12/0** (70/11/0 if the `_contacts_path` 1-line offset is also counted as resolved). The §9 section below still reflects the original audit.

**Overall:** 84% ✅, 15% ⚠️ (off by ≤3 lines), 1% ❌

---

## 1. `avatar/spawn`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | AvatarManager class | `core/avatar/__init__.py` | 85 | ✅ | `class AvatarManager:` at line 85 exactly |
| 2 | `_spawn` entry point | same | 125-316 | ✅ | `def _spawn(` at 125; last `return result` at 316 |
| 3 | Name regex + max len | same | 41-42 | ✅ | `_AVATAR_NAME_RE` at 41, `_AVATAR_NAME_MAX_LEN` at 42 |
| 4 | Name validation block | same | 138-152 | ✅ | `if (` at 138, error dict closes at 152 |
| 5 | Workdir creation + scope check | same | 179-203 | ✅ | Comment at 179, `mkdir()` at 203 |
| 6 | `_make_avatar_init` | same | 359-430 | ✅ | `@staticmethod` at 358, `def _make_avatar_init(` at 359, `return init` at 430 |
| 7 | `_prepare_deep` | same | 437-484 | ✅ | `def _prepare_deep(` at 437, comment at 483-484 |
| 8 | `_launch` (Popen) | same | 496-536 | ✅ | `@staticmethod` at 496, `return proc, stderr_path` at 536 |
| 9 | `_wait_for_boot` | same | 318-352 | ✅ | `@classmethod` at 318, `return ("slow", None)` at 352 |
| 10 | `.prompt` signal file write | same | 249-251 | ✅ | Comment at 249, `write_text(first_prompt)` at 251 |
| 11 | Ledger append | same | 114-119 | ✅ | `def _append_ledger(` at 114, `write(json.dumps(...))` at 119 |
| 12 | Duplicate detection | same | 155-167 | ✅ | `from lingtai_kernel.handshake import is_alive` at 155, dict return closes at 167 |

**Result: 12/12 ✅ — All references accurate.**

---

## 2. `avatar/boot-verification`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | `_wait_for_boot` | `core/avatar/__init__.py` | 318-352 | ✅ | Exact match |
| 2 | `_BOOT_WAIT_SECS` / `_BOOT_POLL_INTERVAL` | same | 493-494 | ✅ | `_BOOT_WAIT_SECS = 5.0` at 493, `_BOOT_POLL_INTERVAL = 0.1` at 494 |
| 3 | `boot_status` ledger field | same | 269-278 | ✅ | `ledger_extra = {"boot_status": ...}` at 269, `**ledger_extra` at 278 |
| 4 | Result construction (ok/slow/failed) | same | 281-316 | ✅ | `if boot_status == "failed":` at 281, `return result` at 316 |
| 5 | Child writes `.agent.heartbeat` | `base_agent.py` | 718-719 | ⚠️ | Line 718 is comment `# Write heartbeat file in ALL living states...`, line 719 is `try:`. The actual `hb_file.write_text(...)` is at 721. Off by 2 lines — should be 718-721 or 720-721 |
| 6 | Heartbeat loop thread start | same | 686-696 | ⚠️ | Line 686 is section comment. `_start_heartbeat` starts at 690. Thread creation is 694-698, `.start()` is 699. Range misses `name=` (697), closing `)` (698), and `.start()` (699). Should be 688-699 |

**Result: 4/6 ✅, 2/6 ⚠️ — No errors, but heartbeat references are 2-3 lines off.**

---

## 3. `avatar/shallow-vs-deep`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | Type check (`"shallow"` / `"deep"`) | `core/avatar/__init__.py` | 131 | ✅ | `if avatar_type not in ("shallow", "deep"):` at 131 |
| 2 | Shallow: bare `mkdir` | same | 203 | ✅ | `avatar_working_dir.mkdir(parents=True, exist_ok=True)` at 203 |
| 3 | Deep: `_prepare_deep` call | same | 200-201 | ✅ | `if avatar_type == "deep":` at 200, `self._prepare_deep(...)` at 201 |
| 4 | `_prepare_deep` implementation | same | 437-484 | ✅ | Full function span verified |
| 5 | Scope guard in `_prepare_deep` | same | 445-451 | ✅ | `src_resolved = src.resolve(...)` at 445, `raise ValueError(...)` closes at 451 |
| 6 | "Not copied" comment | same | 483-484 | ✅ | Comment at 483-484 |
| 7 | `_make_avatar_init` (shared by both) | same | 359-430 | ✅ | Full function span verified |

**Result: 7/7 ✅ — All references accurate.**

---

## 4. `avatar/handshake-files`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | `is_agent` | `lingtai_kernel/handshake.py` | 25-27 | ⚠️ | `is_agent` defined at lines 27-29 (def at 27, body at 28-29). Off by 2 lines |
| 2 | `is_alive` | same | 39-55 | ⚠️ | `is_alive` defined at lines 41-57. Off by 2 lines (start) and 2 lines (end) |
| 3 | `is_human` | same | 30-36 | ⚠️ | `is_human` defined at lines 32-38. Off by 2 lines |
| 4 | `resolve_address` | same | 13-22 | ⚠️ | `resolve_address` defined at lines 15-24. Off by 2 lines |
| 5 | `.agent.json` write | `lingtai_kernel/workdir.py` | 286-290 | ✅ | `def write_manifest(` at 286, `os.replace(...)` at 290 |
| 6 | `.agent.json` schema | `base_agent.py` | 1477-1501 | ⚠️ | Comment/section header at 1475-1477. `_build_manifest` def at 1479, body ends at 1503. Off by 2 lines at start and 2 at end |
| 7 | `.agent.heartbeat` write | same | 718-719 | ⚠️ | Same as boot-verification #5: comment+try at 718-719, actual write at 720-721 |
| 8 | `.agent.lock` acquire/release | `lingtai_kernel/workdir.py` | 48-84 | ✅ | `acquire_lock` at 48-70, `release_lock` at 72-84 |
| 9 | Mail liveness check | `lingtai_kernel/services/mail.py` | 141-142 | ✅ | Lines 141-142 are docstring in `send()` describing the handshake contract. Code implementation is at 158-162. Docstring is a valid contract reference |

**Result: 3/9 ✅, 6/9 ⚠️ — Systematic 2-line offset in `handshake.py` references; heartbeat/schema refs also 2 lines off.**

---

## 5. `mail/dedup`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | `_last_sent` + `_dup_free_passes` init | `core/email/__init__.py` | 240-241 | ✅ | `self._last_sent: dict[...] = {}` at 240, `self._dup_free_passes = 2` at 241 |
| 2 | Gate check logic | same | 804-824 | ✅ | Comment at 804, `return {"status": "blocked", ...}` closes at 824 |
| 3 | Counter update after delivery | same | 894-900 | ✅ | Comment at 894, `self._last_sent[addr] = (message_text, 1)` at 900 |
| 4 | Scheduled-send bypass (`_schedule` flag) | same | 806-808 | ✅ | `if args.get("_schedule"):` at 806, `duplicates = []` at 807, `else:` at 808 |

**Result: 4/4 ✅ — All references accurate.**

---

## 6. `mail/atomic-write`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | Atomic write (peer delivery) | `lingtai_kernel/services/mail.py` | 198-207 | ✅ | Comment at 198, `return f"Failed..."` at 207 |
| 2 | Polling listener (consumer) | same | 230-247 | ✅ | `def _poll_loop()` at 230, `self._seen.add(entry.name)` at 247 |
| 3 | Pseudo-agent claim atomic write | same | 326-331 | ✅ | `try:` at 326, `os.replace(...)` at 331 |
| 4 | Schedule record atomic write | `core/email/__init__.py` | 599-613 | ✅ | `def _write_schedule(` at 599, `raise` at 613 |
| 5 | Read tracking atomic write | `lingtai_kernel/intrinsics/mail.py` | 174-181 | ✅ | `def _save_read_ids(` at 174, `os.replace(...)` at 181 |

**Result: 5/5 ✅ — All references accurate.**

---

## 7. `mail/scheduling`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | `_schedules_dir` property | `core/email/__init__.py` | 269-270 | ✅ | `@property` at 268, `def _schedules_dir` at 269, `return ... "schedules"` at 270 |
| 2 | `_schedule_create` | same | 443-484 | ✅ | `def _schedule_create(` at 443, `return {...}` at 484 |
| 3 | `_schedule_cancel` | same | 486-525 | ✅ | `def _schedule_cancel(` at 486, `return {"status": "paused", ...}` at 525 |
| 4 | `_schedule_reactivate` | same | 527-553 | ✅ | `def _schedule_reactivate(` at 527, `return {"status": "reactivated", ...}` at 553 |
| 5 | `_schedule_list` | same | 555-593 | ✅ | `def _schedule_list(` at 555, `return {"status": "ok", ...}` at 593 |
| 6 | `_write_schedule` (atomic) | same | 599-613 | ✅ | `def _write_schedule(` at 599, `raise` at 613 |
| 7 | `_reconcile_schedules_on_startup` | same | 634-663 | ✅ | `def _reconcile_schedules_on_startup(` at 634, `continue` at 663 |
| 8 | `_scheduler_loop` | same | 665-672 | ✅ | `def _scheduler_loop(` at 665, `self._stop_event.wait(...)` at 672 |
| 9 | `_scheduler_tick` (main logic) | same | 674-781 | ✅ | `def _scheduler_tick(` at 674, `self._write_schedule(sched_file, record)` at 781 |
| 10 | At-most-once: increment-before-send | same | 720-722 | ✅ | `seq = sent + 1` at 720, `record["sent"] = seq` at 721, `self._write_schedule(...)` at 722 |
| 11 | Schedule action dispatch | same | 430-441 | ✅ | `def _handle_schedule(` at 430, `return {"error": ...}` at 441 |

**Result: 11/11 ✅ — All references accurate.**

---

## 8. `mail/identity-card`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | `_build_manifest` (base) | `lingtai_kernel/base_agent.py` | 1477-1501 | ⚠️ | Section comment at 1475-1477. `_build_manifest` def at 1479, `return data` at 1503. Off by 2 lines at start and 2 at end |
| 2 | `_build_manifest` (agent subclass, adds caps) | `lingtai/agent.py` | 223-238 | ⚠️ | `_SENSITIVE_KEYS` class attr at 223, `def _build_manifest` at 225, `return data` at 240. Leaf starts at class attr (not function), and misses last 2 lines (239-240) |
| 3 | Identity injection in mail intrinsic | `lingtai_kernel/intrinsics/mail.py` | 398 | ✅ | `"identity": agent._build_manifest(),` at 398 |
| 4 | Identity injection in email wrapper | `core/email/__init__.py` | 850 | ✅ | `"identity": self._agent._build_manifest(),` at 850 |
| 5 | `_inject_identity` (extraction) | same | 369-385 | ✅ | `def _inject_identity(` at 369, closing `}` at 385 |
| 6 | `_message_summary` sender display | `lingtai_kernel/intrinsics/mail.py` | 214-217 | ✅ | `identity = msg.get("identity")` at 214, `sender = f"..."` at 217 |
| 7 | `_email_summary` calls `_inject_identity` | `core/email/__init__.py` | 344, 351, 1011 | ✅ | All three lines confirmed: `self._inject_identity(summary, e)` at 344, 351; `self._inject_identity(entry, data)` at 1011 |

**Result: 5/7 ✅, 2/7 ⚠️ — `_build_manifest` refs are 2 lines off at boundaries.**

---

## 9. `mail/mailbox-core`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | Layout docstring | `core/email/__init__.py` | 1-8 | ✅ | Docstring with storage layout at lines 1-8 |
| 2 | `_mailbox_path` | same | 265-266 | ✅ | `def _mailbox_path` at 265, `return _mailbox_dir(self._agent)` at 266 |
| 3 | `_schedules_dir` | same | 269-270 | ✅ | `def _schedules_dir` at 269, `return ... "schedules"` at 270 |
| 4 | `_contacts_path` | same | 1215 | ⚠️ | `@property` at 1213, `def _contacts_path` at 1214, `return ... "contacts.json"` at 1215. Line 1215 is the return body, not the function def. Off by 1-2 lines |
| 5 | Root `mkdir` | same | 1228 | ✅ | `self._mailbox_path.mkdir(parents=True, exist_ok=True)` at 1228 |
| 6 | Sent dir creation | same | 878-879 | ✅ | `sent_dir = ... / "sent" / sent_id` at 878, `sent_dir.mkdir(...)` at 879 |
| 7 | Self-send direct write | `lingtai_kernel/intrinsics/mail.py` | 248-264 | ✅ | `def _persist_to_inbox(` at 248, `return msg_id` at 264 |
| 8 | `_list_inbox` / `_read_ids` | `lingtai_kernel/intrinsics/mail.py` | 26 | ❌ | **Line 26 is blank.** `_list_inbox` is at line **135**; `_read_ids` is at line **162**. Off by ~109-136 lines. These functions were likely moved or the reference was written against an older file layout |

**Result: 6/8 ✅, 1/8 ⚠️, 1/8 ❌ — One critical error: `_list_inbox`/`_read_ids` reference is completely wrong.**

**Update:** The `mailbox-core` leaf was corrected between reads. The `_list_inbox`/`_read_ids` reference was fixed from `line 26` to `135 / 162`, and `_contacts_path` was updated from `1214` to `1213-1215`. The corrected audit would be 7/8 ✅, 1/8 ⚠️, 0/8 ❌.

---

## 10. `mail/peer-send`

| # | What | File | Claimed Lines | Verdict | Notes |
|---|------|------|---------------|---------|-------|
| 1 | Wrapper send entry + outbox persist | `core/email/__init__.py` | 862-874 | ✅ | `for addr in all_recipients:` at 862, `t.start()` at 874 |
| 2 | Wrapper sent-record write | same | 876-892 | ✅ | Comment at 876, `write_text(...)` at 890-892 |
| 3 | Mailman thread (routing) | `lingtai_kernel/intrinsics/mail.py` | 317-358 | ✅ | `def _mailman(` at 317, `agent._log(...)` at 357-358 |
| 4 | Mailman non-self → transport | same | 338-340 | ✅ | `elif agent._mail_service is not None:` at 338, `send(address, payload, mode=mode)` at 339 |
| 5 | `FilesystemMailService.send()` | `lingtai_kernel/services/mail.py` | 131-209 | ✅ | `def send(` at 131, `return None` at 209 |
| 6 | `resolve_address` | `lingtai_kernel/handshake.py` | 13-22 | ⚠️ | `resolve_address` is at lines 15-24. Off by 2 lines |
| 7 | `is_agent` | same | 25-27 | ⚠️ | `is_agent` is at lines 27-29. Off by 2 lines |
| 8 | `is_alive` | same | 39-55 | ⚠️ | `is_alive` is at lines 41-57. Off by 2 lines |
| 9 | Attachment copy + rewrite | `lingtai_kernel/services/mail.py` | 180-194 | ✅ | `# Handle attachments` at 180, `message = {**message, "attachments": local_copies}` at 194 |
| 10 | Atomic write | same | 198-207 | ✅ | `# Atomic write: tmp → rename` at 198, `return f"Failed..."` at 207 |
| 11 | Bounce notification | `lingtai_kernel/intrinsics/mail.py` | 360-371 | ✅ | `# Bounce notification` at 360, notification string construction starts at 365 |
| 12 | Identity card injection | `core/email/__init__.py` | 850 | ✅ | `"identity": self._agent._build_manifest(),` at 850 |

**Result: 9/12 ✅, 3/12 ⚠️ — `handshake.py` refs share the same 2-line offset as handshake-files leaf.**

---

## Systematic Issues Found

### 1. `handshake.py` — Consistent 2-line offset (affects 3 leaves)

All references to `lingtai_kernel/handshake.py` across `avatar/handshake-files`, `mail/peer-send` (and tangentially `avatar/spawn` which references it indirectly) are off by exactly 2 lines. The functions were likely shifted down by 2 lines (perhaps due to added imports or blank lines) after the anatomy was written.

**Affected references:**
- `is_agent` claimed 25-27 → actual 27-29
- `is_alive` claimed 39-55 → actual 41-57
- `is_human` claimed 30-36 → actual 32-38
- `resolve_address` claimed 13-22 → actual 15-24

**Fix:** Shift all `handshake.py` line numbers up by 2.

### 2. `base_agent.py` heartbeat/schema — 2-line offset (affects 2 leaves)

References to `.agent.heartbeat` write and `_build_manifest` in `base_agent.py` are consistently 2 lines off, suggesting section comments were added after the anatomy was drafted.

### 3. `_list_inbox` / `_read_ids` — Completely wrong (affects 1 leaf)

`mail/mailbox-core` references these functions at `intrinsics/mail.py` line 26. The actual locations are lines 135 and 162 respectively. This is a stale reference — the functions may have been reorganized in the file since the leaf was written.

---

## Recommendations

1. **Priority 1 (❌):** Fix `mail/mailbox-core` §Source row for `_list_inbox`/`_read_ids`: change line 26 → two separate rows at 135 and 162
2. **Priority 2 (⚠️ batch):** Apply a uniform +2 offset to all `handshake.py` references in `avatar/handshake-files` and `mail/peer-send`
3. **Priority 3 (⚠️ batch):** Fix `base_agent.py` heartbeat write ref (718-719 → 718-721 or 720-721) and `_build_manifest` ref (1477-1501 → 1479-1503) in both `avatar/boot-verification` and `avatar/handshake-files`
4. **Priority 4 (⚠️ minor):** Fix `agent.py` `_build_manifest` ref in `mail/identity-card` (223-238 → 225-240)

---

## Semantic Audit Pilot: `avatar/spawn`

> Beyond line-number verification: are the **right things** referenced, and does the Contract
> section faithfully represent the code?

### Coverage analysis — Contract claims vs §Source rows

| Contract section | Covers lines | §Source rows | Semantic notes |
|---|---|---|---|
| Name validation | 138-152 | rows 3,4 (regex + validation block) | ✅ Good — two complementary views |
| Workdir creation | 179-203 | row 5 (workdir + scope check) | ✅ Good |
| init.json inheritance | 239-247, 359-430 | rows 8 (_make_avatar_init) | ⚠️ Contract says "lines 239-247" for the write, but §Source points only at `_make_avatar_init` (359-430). The actual write is at 245-247, which isn't a separate §Source row. Minor gap — the build + write are a single logical block |
| Process launch | 496-536 | row 9 (Popen) | ✅ Good |
| First prompt delivery | 223-251 | row 11 (.prompt signal file write, 249-251) | ⚠️ Contract covers 223-251 (prompt *construction* at 223-237, write at 249-251), but §Source only references 249-251. Missing: the prompt composition logic (223-237) — parent name extraction, language lookup via `t()`, reasoning concatenation. This is behavior the Contract describes but §Source doesn't pin |
| Ledger | 110-119, 267-278 | rows 10 (append, 114-119), 2 (covers 125-316 which includes 267-278) | ✅ Adequate — the 125-316 span covers ledger recording, but the Contract calls out 267-278 specifically. Could be a separate row for clarity |
| Duplicate detection | 155-167 | row 11 | ✅ Good |

### Unreferenced behavior — what the code does that the leaf doesn't document

I found **five significant code blocks** in `_spawn` (lines 125-316) that are present in the code but NOT mentioned in the Contract or §Source:

1. **Parent init.json validation (lines 169-177)**
   - Checks that parent has `init.json` (lines 170-172)
   - Handles `JSONDecodeError`/`OSError` (lines 174-177)
   - **Impact:** Contract doesn't mention this pre-condition. A reader might assume spawning works without init.json

2. **Relative path re-rooting (lines 205-211)**
   - Iterates `env_file`, `covenant_file`, `principle_file`, `procedures_file`, `comment_file`, `soul_file`
   - Resolves relative paths against parent's working dir
   - **Impact:** Contract §init.json inheritance mentions "Re-roots relative preset paths" but doesn't mention *file reference paths*. These are two distinct re-rooting operations (preset paths happen inside `_make_avatar_init` at 397-412; file refs happen at 205-211 in `_spawn` itself). The leaf conflates them

3. **venv_path inheritance (lines 213-215)**
   - `if hasattr(parent, "_venv_path") and parent._venv_path: parent_init["venv_path"] = parent._venv_path`
   - **Impact:** Not mentioned at all. This is how the avatar finds the Python runtime on first boot. Significant for understanding boot mechanics

4. **Stale signal cleanup (lines 217-221)**
   - Removes `.suspend`, `.sleep`, `.interrupt` files before launch
   - **Impact:** Defensive cleanup to prevent inheriting stale signals from a previous avatar at the same path. Not documented

5. **Rules auto-distribution (lines 292-300)**
   - After successful boot, reads `system/rules.md` and distributes to all descendants via `_distribute_rules_to_descendants()`
   - **Impact:** Significant behavior — spawns automatically propagate parent's rules network-wide. Completely absent from the leaf. This is a cross-cutting concern that belongs in the Contract (or at minimum in Related)

6. **`_rules` action (lines 559-593) + `_walk_avatar_tree` (596-643) + `_distribute_rules_to_descendants` (645-659)**
   - The `avatar` tool has a second action `rules` besides `spawn`
   - **Impact:** The leaf is titled "Avatar Spawn" and only describes spawn, but `AvatarManager.handle()` dispatches to both `_spawn` and `_rules`. The `rules` action is a full-featured mechanism for setting network-wide rules. Not mentioned anywhere

### §Source completeness — missing references

| Missing reference | Lines | Why it matters |
|---|---|---|
| Path re-rooting in `_spawn` | 205-211 | Distinct from preset re-rooting in `_make_avatar_init` |
| venv_path inheritance | 213-215 | Critical for boot mechanics |
| Signal cleanup | 217-221 | Explains why a respawn works cleanly |
| Rules distribution | 292-300, 559-659 | Major secondary behavior of the avatar capability |
| `_read_ledger` | 542-553 | Used by duplicate detection but not separately referenced |
| `handle()` dispatcher | 100-104 | Shows this is a two-action tool (spawn + rules) |

### Redundancy check

No truly redundant references found. The `_spawn` entry point (125-316) overlaps with most other rows, but that's intentional — it's the encompassing span, and the individual rows provide precision.

### Summary: semantic quality of `avatar/spawn`

**Coverage gap: ~40% of significant behavior is undocumented.** The leaf accurately describes the happy-path spawn flow but misses:
- Pre-conditions (parent init.json existence, venv_path)
- Defensive mechanisms (signal cleanup, stale file removal)
- The entire `rules` action and its descendant distribution network
- The distinction between two kinds of path re-rooting

**Recommendation:** The `avatar/spawn` leaf would benefit from:
1. A new §Contract subsection for the `rules` action (or a separate `avatar/rules` leaf)
2. A "Pre-conditions" or "Prerequisites" subsection documenting parent init.json and venv_path
3. Mentioning stale signal cleanup in the "Workdir creation" subsection
4. A note about rules auto-distribution on successful boot
5. Separating the file-reference path re-rooting (lines 205-211) from the preset path re-rooting (`_make_avatar_init` lines 397-412)

---

## Semantic Audit: `mail/scheduling`

> **Mechanical score: 11/11 ✅ — perfect line numbers.**
> **Semantic score: ~80% coverage — core mechanics well-documented, failure semantics and notification injection gaps.**

### Coverage analysis — Contract vs §Source

| Contract section | §Source rows | Verdict |
|---|---|---|
| Storage | `_schedules_dir` (269-270) | ✅ |
| Status state machine | conceptual diagram, no row needed | ✅ |
| Operations (4 actions) | `create` (443-484), `cancel` (486-525), `reactivate` (527-553), `list` (555-593), dispatch (430-441) | ✅ All 4 + dispatch |
| At-most-once guarantee | 720-722 | ✅ |
| Scheduler loop | `_scheduler_loop` (665-672), `_scheduler_tick` (674-781) | ✅ |
| Startup reconciliation | `_reconcile_schedules_on_startup` (634-663) | ✅ |

### Unreferenced behavior

1. **Failed-send counting (lines 742-746)** — After `self._send(send_args)`, if the result is an error or `"blocked"`, the schedule's `last_sent_at` is still updated BUT `sent` was already incremented at 720-722. This means **failed sends count toward the total** and the interval timer resets. A schedule can "complete" without successfully delivering all messages. The Contract says "at-most-once semantics" but doesn't clarify this failure-interaction semantic. A significant operational detail.

2. **System notification injection (lines 748-775)** — After each scheduled send, a `MSG_REQUEST` message is injected into the agent's inbox with a rich progress string like `[schedule 3/10] sent to X | subject: Y | sent at Z | next at W | ends ~T`. Different message format for the final send ("schedule complete"). The Contract mentions "Injects a system notification per send" in one sentence, but §Source doesn't pin the 748-775 block. This is 28 lines of notification logic.

3. **`create` supports richer params than documented (lines 456-465)** — The Contract's Operations table says `create` requires `address, message, interval, count`. But `_schedule_create` also accepts and stores `cc`, `bcc`, `type`, and `attachments` in the `send_payload`. Users scheduling complex messages with CC/BCC/attachments wouldn't know from the leaf that this is supported.

4. **Validation in `_schedule_create` (lines 446-449)** — Checks `interval is None or count is None`, and `interval <= 0 or count <= 0`. Contract doesn't mention these guardrails.

5. **`_read_schedule` + `_set_schedule_status` helpers (lines 615-632)** — Internal plumbing used by cancel/reactivate. Not independently meaningful, but `_set_schedule_status` handles the write-after-status-change that completes the state machine transitions. Not referenced.

### §Source completeness — missing references

| Missing reference | Lines | Why it matters |
|---|---|---|
| Failed-send + last_sent_at update | 742-746 | Operational failure semantics |
| System notification injection | 748-775 | Agent-visible side effect per send |
| `_read_schedule` helper | 615-622 | Read path for schedule state |

### Verdict

`mail/scheduling` is the strongest leaf of the three audited so far. The core loop, state machine, and all four operations are accurately documented. The main gap is **failure-mode semantics**: what happens when a scheduled send fails (counter still increments, timer still resets). This is the kind of detail that matters for debugging production behavior but is easy to omit from a Contract section that focuses on the happy path.

---

## Semantic Audit: `mail/peer-send`

> **Mechanical score: 9/12 ✅, 3/12 ⚠️ (handshake.py 2-line offset)**
> **Semantic score: ~75% coverage — happy path well-described, but private mode, delay, and outbox mechanics are gaps.**

### Coverage analysis — Contract vs §Source

| Contract section | §Source rows | Verdict |
|---|---|---|
| End-to-end flow diagram | `_send()` (862-874), Mailman (317-358), `FilesystemMailService.send()` (131-209) | ✅ All three layers |
| Address resolution | `resolve_address` handshake.py:13-22 | ✅ (⚠️ line offset) |
| Handshake | `is_agent` + `is_alive` handshake.py:25-55 | ✅ (⚠️ line offset) |
| Attachments | services/mail.py:180-194 | ✅ |
| Wrapper-level sent record | 876-892 | ✅ |

### Unreferenced behavior

1. **Private mode gate (lines 826-837)** — Before any send, `_send()` checks `self._private_mode`. If enabled, ALL recipients must be in `contacts.json` or the entire send is rejected. This is a significant capability-level pre-condition that affects every peer-send. Not mentioned in Contract or §Source. An agent debugging "why won't my send go through?" would not find the answer in this leaf.

2. **Delay parameter (lines 794, 859)** — `_send()` accepts a `delay` parameter (seconds). `deliver_at = datetime.now(timezone.utc) + timedelta(seconds=delay)` is computed at 859, passed to `_persist_to_outbox` and Mailman. Mailman sleeps until `deliver_at` (line 322-324). The Contract's flow diagram shows `sleep(delay)` in Mailman but doesn't mention where `delay` originates. The `_send()` row (862-874) also starts *after* the delay computation at 859.

3. **Sender address resolution (lines 839-841)** — The `from` field is resolved as `self._agent._mail_service.address` with fallback to `self._agent._working_dir.name`. This affects what the recipient sees. Not documented — a reader wouldn't know the sender display name comes from the mail service address first.

4. **`_dispatch_to` and `_mode` per-recipient injection (lines 863-865)** — Each recipient gets a shallow copy of `base_payload` with `_dispatch_to` (individual address) and `_mode` (peer/abs) added. This is how a single `_send()` call with multiple recipients routes each one correctly. The flow diagram shows the loop but doesn't explain these injected fields.

5. **`_persist_to_outbox` function (intrinsics/mail.py:277-292)** — Referenced in the flow diagram ("`_persist_to_outbox() → outbox/{uuid}/message.json`") but NOT in §Source. This is the write that places the message in the outbox for Mailman to pick up. A missing §Source row.

6. **`_is_self_send` check (intrinsics/mail.py:233-245)** — The Contract flow diagram says "address is NOT self → transport" but §Source doesn't reference the `_is_self_send` function. This is the divergence point where self-send takes a completely different path (`_persist_to_inbox` direct write vs `FilesystemMailService.send()`). The leaf title explicitly says "self-send diverges from this path" — the divergence code should be pinned.

7. **BCC asymmetry (lines 862-892)** — The Contract mentions "preserving BCC" in the sent record section. But the mechanics are subtle: BCC is added to the *sent record* (line 886-887) but NOT to `base_payload` (which is what each recipient receives). So BCC is invisible to all recipients but visible to the sender's sent folder. Worth documenting explicitly.

8. **`_schedule` metadata passthrough (lines 888-889)** — When triggered by scheduling, `_schedule` metadata is stored in the sent record. Cross-references `mail/scheduling` in Related.

### §Source completeness — missing references

| Missing reference | Lines | Why it matters |
|---|---|---|
| Private mode gate | `core/email/__init__.py` 826-837 | Pre-condition that silently blocks sends |
| Delay computation | `core/email/__init__.py` 794, 859 | Feature: deferred delivery |
| `_persist_to_outbox` | `intrinsics/mail.py` 277-292 | The outbox write step |
| `_is_self_send` | `intrinsics/mail.py` 233-245 | The self-send divergence point |
| Sender address resolution | `core/email/__init__.py` 839-841 | What appears in `from` |

### Verdict

`mail/peer-send` documents the happy path well — the three-layer flow diagram is excellent and all three transport layers are pinned in §Source. The main gaps are **pre-conditions** (private mode, sender resolution) and **divergence mechanics** (self-send check, outbox write). The delay feature is mentioned in the flow diagram but not properly sourced. The BCC asymmetry is a subtle detail that could save someone debugging a "BCC leaked" or "BCC missing" report.

---

## Cross-leaf pattern: semantic gaps are systematic

| Leaf | Mechanical score | Semantic coverage | Primary gap category |
|---|---|---|---|
| `avatar/spawn` | 12/12 ✅ | ~60% | Missing secondary actions, pre-conditions |
| `mail/scheduling` | 11/11 ✅ | ~80% | Failure-mode semantics, notification injection |
| `mail/peer-send` | 9/12 + 3 ⚠️ | ~75% | Pre-conditions (private mode), divergence points |

**Pattern confirmed: perfect line numbers ≠ complete documentation.** All three leaves prioritize the happy path over:
- **Pre-conditions** (parent init.json, private mode, venv_path)
- **Failure/edge semantics** (failed sends counting toward total, stale signal cleanup)
- **Secondary actions** (the `rules` action in avatar)
- **Divergence points** (self-send check, private mode gate)
- **Side effects** (notification injection, rules distribution)

This is a natural bias — the happy path is most visible when reading the code, and pre-conditions + failure modes require deliberate attention. The §Source tables are mechanically accurate because line-number verification is a mechanical task. But "are the right things referenced" requires semantic judgment about what a reader needs.

### Recommendations for the remaining 7 unaudited leaves

Based on the pattern, a full semantic audit of all 10 leaves would likely surface 15-25 additional gaps of similar character. Priority areas for the remaining leaves:
- `mail/identity-card` — check whether the full manifest field list matches `_build_manifest()` output, and whether the `location` field (only surfaced for humans) is documented
- `mail/dedup` — check whether the `_schedule` bypass is the *only* bypass, and whether error-message sends also bypass
- `mail/mailbox-core` — check whether `_persist_to_outbox`, `_persist_to_inbox`, and `_move_to_sent` are all referenced
