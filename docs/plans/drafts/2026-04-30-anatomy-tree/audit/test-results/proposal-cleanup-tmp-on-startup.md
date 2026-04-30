# Proposal: Auto-cleanup `.tmp` orphans on agent startup

**Author:** test-mail (audit agent)  
**Date:** 2026-04-30  
**Status:** Proposed (not implemented)

---

## Problem

If the agent process crashes **after** writing `message.json.tmp` but **before** `os.replace()`, the `.tmp` file is orphaned. No reconciliation exists on restart — the orphan sits silently forever.

See: `atomic-write` leaf §"What it does NOT protect against".

## Proposed change

Add `_cleanup_tmp_orphans()` to `EmailManager`, called from `_reconcile_schedules_on_startup()`'s neighbor — i.e., at the same startup phase.

### Hook point

`lingtai/core/email/__init__.py`, inside `setup()` (line 1294), at line 1304 after `_reconcile_schedules_on_startup()`:

```python
mgr._reconcile_schedules_on_startup()  # L1304
mgr._cleanup_tmp_orphans()             # INSERT HERE
mgr.start_scheduler()                  # L1305
```

### Implementation

```python
def _cleanup_tmp_orphans(self, max_age_s: int = 300) -> int:
    """Remove .tmp files older than max_age_s from inbox.
    
    Called once at startup. Safe: a .tmp being actively written
    by a concurrent Mailman thread will have fresh mtime and
    won't meet the age threshold.
    
    Returns number of orphans removed.
    """
    inbox = self._agent._working_dir / "mailbox" / "inbox"
    if not inbox.is_dir():
        return 0
    
    now = time.time()
    removed = 0
    for tmp in inbox.glob("**/message.json.tmp"):
        try:
            age = now - tmp.stat().st_mtime
        except OSError:
            continue
        if age > max_age_s:
            try:
                tmp.unlink()
                removed += 1
            except OSError:
                continue
    return removed
```

### Why 300s (5 min)?

- Mailman delivery takes < 1s normally
- A `.tmp` older than 5 min is almost certainly orphaned
- Conservative: even a very slow network share wouldn't take 5 min for a file rename

### Is age threshold alone sufficient?

Yes, for three reasons:

1. **At startup, no Mailman threads are running.** `_cleanup_tmp_orphans()` runs before `start_scheduler()` (L1305). No new `.tmp` can be created by this agent between the reconciliation and the cleanup.
2. **Peer-send race is protected by mtime.** A *sender's* Mailman thread could be writing a `.tmp` to our inbox concurrently (peer-send delivers to recipient's inbox). But that `.tmp` has `mtime ≈ now`, so it won't meet the 300s threshold.
3. **Dead Mailman + stale outbox is still safe to delete.** If a Mailman thread died silently (GC'd daemon thread), both the `.tmp` and the outbox entry are orphans. Deleting the `.tmp` is correct — the outbox entry is also stale and will be orphaned on next restart (no outbox reconciliation exists).

**Edge case NOT covered:** A `.tmp` that is *actively being written* (not yet flushed) by a peer sender AND has `mtime > 300s`. This requires: (a) sender's Mailman thread is alive but disk write is stalled for 5+ minutes, AND (b) the write hasn't flushed yet. This is extremely unlikely — `write_text()` is synchronous; a 5-minute stall implies a dead disk or NFS hang. Acceptable risk.

### Why NOT integrate into the standalone script?

The standalone `cleanup_tmp_orphans.py` (in `audit/test-results/scripts/`) is a manual diagnostic tool. The real fix is startup reconciliation — agents shouldn't need manual intervention.

## Risk

**Near zero.** The check is read-stat + unlink on files with a specific name pattern (`message.json.tmp`) in a directory the agent owns. A `.tmp` being actively written by a concurrent Mailman thread will have `mtime ≈ now` and won't meet the 300s threshold.

## Test plan

1. Create a fake orphan: `mkdir -p mailbox/inbox/fake-uuid && echo '{}' > mailbox/inbox/fake-uuid/message.json.tmp && touch -t 202601010000 mailbox/inbox/fake-uuid/message.json.tmp`
2. Call `_cleanup_tmp_orphans()`
3. Verify `fake-uuid/` is gone or empty

## Related leaves

- `atomic-write` — this is the "does NOT protect against" gap
- `mailbox-core` — directory layout where orphans appear
