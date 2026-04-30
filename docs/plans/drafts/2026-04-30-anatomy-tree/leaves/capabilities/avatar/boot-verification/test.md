---
timeout: 180
---

# Test: Boot Verification

## Setup

1. A running parent agent with `avatar` capability.
2. Network root directory with write permissions.

## Steps

### Scenario A: Successful boot

1. **Spawn a valid avatar** and capture result.
   ```python
   result = avatar(action="spawn", name="boot-ok", reasoning="boot test")
   ```

2. **Check result fields** — `status=="ok"`, no `warning` key, positive `pid`.
   ```bash
   # Inspect tool result: result['status']=='ok', no 'warning', result['pid']>0
   ```

3. **Verify heartbeat file on disk** — exists with fresh timestamp.
   ```bash
   python3 -c "
   import time
   ts = float(open('<root>/boot-ok/.agent.heartbeat').read().strip())
   assert time.time() - ts < 5.0, f'stale by {time.time()-ts:.1f}s'
   print('PASS')
   "
   ```

4. **Verify ledger has boot_status=ok** with no boot_error.
   ```bash
   python3 -c "
   import json
   for line in open('<parent>/delegates/ledger.jsonl'):
       r = json.loads(line)
       if r.get('name') == 'boot-ok' and r.get('event') == 'avatar':
           assert r['boot_status'] == 'ok'
           assert 'boot_error' not in r
           print('PASS'); break
   "
   ```

### Scenario B: Slow boot (timeout without exit)

5. **Understand slow detection** — if the child process survives 5s without writing
   `.agent.heartbeat`, `_wait_for_boot` returns `("slow", None)`. The caller
   returns `{"status":"ok", ..., "warning":"avatar still booting after 5s — check
   .agent.heartbeat"}`. This is observable via the result dict's `warning` field.
   The ledger records `boot_status:"slow"`.

### Scenario C: Failed boot (early exit)

6. **Understand failure detection** — if the child exits before writing `.agent.heartbeat`,
   `_wait_for_boot` reads `logs/spawn.stderr` (last 2000 bytes) and returns
   `("failed", msg)`. The caller returns `{"error": "avatar 'X' failed to boot: ..."}`
   and the ledger records `boot_status:"failed"` with `boot_error` field.

7. **Verify stderr path exists** — even failed spawns leave a `logs/spawn.stderr` file.
   ```bash
   # After a failed spawn, check that logs/spawn.stderr exists (written by _launch)
   ```

8. **Verify constants** — `_BOOT_WAIT_SECS=5.0`, `_BOOT_POLL_INTERVAL=0.1`.
   ```bash
   python3 -c "
   from lingtai.core.avatar import AvatarManager
   assert AvatarManager._BOOT_WAIT_SECS == 5.0
   assert AvatarManager._BOOT_POLL_INTERVAL == 0.1
   print('PASS')
   "
   ```

## Pass criteria

- Scenario A: result `status=="ok"`, heartbeat fresh, ledger `boot_status="ok"`.
- Scenario B: described behavior when child is slow (warning field in result, `"slow"` in ledger).
- Scenario C: described behavior when child exits early (error field, stderr read, `"failed"` in ledger).
- Constants: `_BOOT_WAIT_SECS==5.0`, `_BOOT_POLL_INTERVAL==0.1`.

## Output template

```
## Boot Verification Test
| Scenario | Step | Check | Result |
|----------|------|-------|--------|
| A | 1 | Spawn succeeds | |
| A | 2 | Result fields | |
| A | 3 | Heartbeat fresh | |
| A | 4 | Ledger ok | |
| B | 5 | Slow behavior | |
| C | 6 | Failed behavior | |
| C | 7 | stderr path | |
| — | 8 | Constants | |
```
