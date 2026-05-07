# PR #11 Review — lingtai-kernel (Notification Fix + Proactive Handling)

**Reviewer:** pr-reviewer  
**Date:** 2026-05-06  
**Branch:** `fix/aed-retry-and-notification-leak` (2 commits)  
**Files changed:** 5 files, +113 −18

---

## Verdict: APPROVE (with minor notes)

The core fix is correct and well-tested. The proactive notification handling (Issue #47) is clean and low-risk. A few minor observations below.

---

## Commit 1: `e0715ca` — Issue #8 fix (Notification pairs leak)

### `src/lingtai_kernel/llm/interface.py` — Shape guard relaxation

**What changed:** Both `remove_pair_by_call_id` and `remove_pair_by_notif_id` previously required `len(a.content) == 1` on the assistant entry, which rejected notification pairs shaped as `[TextBlock, ToolCallBlock]` (2 blocks). The fix iterates through blocks, accepts exactly one `ToolCallBlock` plus any number of `TextBlock`s, and rejects anything else.

**Assessment:** ✅ Correct. The logic is:
```python
cblock = None
for blk in a.content:
    if isinstance(blk, ToolCallBlock):
        if cblock is not None:  # multiple tool calls — skip
            cblock = None; break
        cblock = blk
    elif not isinstance(blk, TextBlock):
        cblock = None; break
if cblock is None: continue
```
This correctly handles:
- `[ToolCallBlock]` — single tool call (original shape)
- `[TextBlock, ToolCallBlock]` — synthesized notification pair (new shape)
- `[TextBlock, TextBlock, ToolCallBlock]` — multiple text blocks (allowed)
- `[ToolCallBlock, ToolCallBlock]` — multiple tool calls (rejected)
- `[TextBlock, SomeOtherBlock]` — unexpected block types (rejected)

The guard remains strict enough to protect regular tool-call history from accidental corruption.

**Minor note:** The identical guard logic is duplicated in both methods (~15 lines each). This is acceptable since the methods have different matching criteria after the guard, and extracting a shared helper would add indirection without much clarity gain.

### `tests/test_notification_sync.py` — Test update

**Assessment:** ✅ Correct. The test now verifies the new pair structure:
```python
assert len(entries[0].content) == 2
assert isinstance(entries[0].content[0], TextBlock)
call_block = entries[0].content[1]
assert isinstance(call_block, ToolCallBlock)
assert call_block.args["action"] == "notification"
```
The assertion change from `call_block.args == {"action": "notification"}` to `call_block.args["action"] == "notification"` is appropriate — the args dict may carry additional fields in the future, and checking only the action is more resilient.

---

## Commit 2: `11bb23b` — Issue #47 (Proactive notification handling)

### `src/lingtai/core/mcp/inbox.py` — Human message detection

**What changed:** Added `has_human_messages` tracking to `_scan_once`. Events whose `from` field doesn't start with `"system"` or `"soul"` are flagged as human. The flag is passed to `_dispatch_summary` → `_format_notification_summary`, which appends `[HUMAN]` to the notification body.

**Assessment:** ✅ Acceptable. The heuristic is simple and the consequence of mis-detection is purely cosmetic (an extra `[HUMAN]` tag in the notification summary). It doesn't affect routing, wake behavior, or message delivery.

**Minor note:** The denylist (`startswith("system")`, `startswith("soul")`) is fragile — MCP server names like `"soul"` or custom system senders could trigger false positives. A more robust approach would be an allowlist of known MCP names (telegram, email, feishu, wechat) or a metadata flag from the event. However, since this is cosmetic only, the current approach is acceptable for v1.

### `src/lingtai_kernel/base_agent/turn.py` — Pre-idle notification check

**What changed:** Before transitioning to IDLE, checks if the notification fingerprint has changed. If so, calls `_sync_notifications()` to surface pending messages.

**Assessment:** ✅ Correct. This catches messages that arrived during active work (while the agent was processing a turn and not checking the inbox). The `_sync_notifications()` method already handles all the state-dependent injection logic (IDLE → splice pair, ACTIVE → stash pending, ASLEEP → wake + splice).

**Note:** The PR description says "The turn.py AED changes were reverted" but the diff still shows 17 lines of new code in `turn.py`. If this was truly reverted, the diff is wrong. If the PR description is stale, it should be updated.

### `src/lingtai_kernel/intrinsics/soul/flow.py` — Pre-consultation notification check

**What changed:** Before running `_run_consultation_fire()`, checks notification fingerprint and syncs if changed.

**Assessment:** ✅ Correct. This ensures messages are seen within one soul delay cycle rather than waiting indefinitely. The check is wrapped in a try/except so it never blocks the consultation.

**Minor note:** The notification check in `turn.py` (pre-idle) and `flow.py` (pre-consultation) are near-identical code blocks. Since `_sync_notifications()` already checks the fingerprint internally (`if fp == self._notification_fp: return`), the outer fingerprint comparison in both locations is a fast-path optimization — it avoids the overhead of `collect_notifications()` when nothing changed. This is fine, but worth noting that the `collect_notifications()` call in both blocks is redundant if `_sync_notifications()` is going to do it again internally.

---

## PR Description Accuracy

The PR description says "2 files, +35 −10" but the actual diff is 5 files, +113 −18. The Issue #47 changes (inbox.py, turn.py, flow.py) are included in the branch but not mentioned in the description. The description should be updated to cover the full scope of the PR.

---

## Summary

| Area | Assessment |
|------|------------|
| Issue #8 fix (shape guard) | ✅ Correct, well-tested |
| Issue #47 (proactive notifications) | ✅ Clean, low-risk |
| Human message detection | ✅ Acceptable (cosmetic only) |
| Test coverage | ✅ Updated for new shape |
| PR description accuracy | ⚠️ Stale — needs update |

The core notification leak fix is solid. The proactive notification handling is a sensible addition. Approved for merge.
