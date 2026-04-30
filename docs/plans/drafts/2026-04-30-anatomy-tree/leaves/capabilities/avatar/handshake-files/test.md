---
timeout: 180
---

# Test: Handshake Files

## Setup

1. A running agent with working directory at `<wd>`.
2. Python on PATH; `lingtai_kernel.handshake` importable.

## Steps

1. **Check `.agent.json` exists and has all required fields**.
   ```bash
   python3 -c "
   import json
   d = json.load(open('<wd>/.agent.json'))
   required = ['agent_id','agent_name','address','created_at','started_at',
               'admin','language','stamina','state','soul_delay','molt_count']
   missing = [k for k in required if k not in d]
   assert not missing, f'missing: {missing}'
   print('PASS')
   "
   ```

2. **Verify agent_id format** — `YYYYMMDD-HHSS-hex(4)`.
   ```bash
   python3 -c "
   import json, re
   d = json.load(open('<wd>/.agent.json'))
   assert re.match(r'^\d{8}-\d{6}-[0-9a-f]{4}$', d['agent_id']), d['agent_id']
   print('PASS')
   "
   ```

3. **Verify avatar admin field** — `{}` for spawned avatar.
   ```bash
   python3 -c "
   import json
   d = json.load(open('<wd>/.agent.json'))
   assert d['admin'] == {}, f'admin={d[\"admin\"]}'
   print('PASS')
   "
   ```

4. **Verify no stale `.agent.json.tmp`** (atomic write completed).
   ```bash
   test ! -f <wd>/.agent.json.tmp && echo "PASS" || echo "FAIL"
   ```

5. **Check `.agent.heartbeat`** — exists, parseable float, fresh (< 2s).
   ```bash
   python3 -c "
   import time
   ts = float(open('<wd>/.agent.heartbeat').read().strip())
   age = time.time() - ts
   assert age < 2.0, f'heartbeat is {age:.1f}s old'
   print('PASS')
   "
   ```

6. **Verify `is_agent()` and `is_alive()` return True**.
   ```bash
   python3 -c "
   from lingtai_kernel.handshake import is_agent, is_alive
   assert is_agent('<wd>'), 'is_agent failed'
   assert is_alive('<wd>'), 'is_alive failed'
   print('PASS')
   "
   ```

7. **Check `.agent.lock` exists while running**.
   ```bash
   test -f <wd>/.agent.lock && echo "PASS" || echo "FAIL"
   ```

8. **Check `.status.json` if exists** — valid JSON with `tokens` and `runtime` keys.
   ```bash
   if [ -f <wd>/.status.json ]; then
     python3 -c "
     import json
     d = json.load(open('<wd>/.status.json'))
     assert 'tokens' in d and 'runtime' in d
     print('PASS')
     "
   else
     echo "SKIP (no turn completed)"
   fi
   ```

9. **Verify `is_human()` returns False for avatar** (admin is `{}`, not `null`).
   ```bash
   python3 -c "
   from lingtai_kernel.handshake import is_human
   assert not is_human('<wd>'), 'avatar should not be human'
   print('PASS')
   "
   ```

10. **Verify mail delivery liveness** — both `.agent.json` exists AND heartbeat fresh
    (this is what `FilesystemMailService` checks at mail.py:141–142).
    ```bash
    python3 -c "
    from lingtai_kernel.handshake import is_agent, is_alive
    assert is_agent('<wd>') and is_alive('<wd>'), 'mail would reject this agent'
    print('PASS')
    "
    ```

## Pass criteria

- `.agent.json`: exists, valid JSON, all required fields, correct `agent_id` format, `admin={}`.
- `.agent.heartbeat`: exists, parseable float, within 2s of now.
- `is_agent()`, `is_alive()` both true; `is_human()` false.
- `.agent.lock` exists while process runs.
- `.status.json` valid if exists (optional — may not exist before first turn).

## Output template

```
## Handshake Files Test
| Step | Check | Result |
|------|-------|--------|
| 1 | .agent.json fields | |
| 2 | agent_id format | |
| 3 | admin field | |
| 4 | No stale .tmp | |
| 5 | Heartbeat fresh | |
| 6 | is_agent + is_alive | |
| 7 | .agent.lock exists | |
| 8 | .status.json (opt.) | |
| 9 | is_human false | |
| 10 | Mail liveness | |
```
