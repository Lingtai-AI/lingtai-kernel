---
timeout: 180
---

# Test: Spawn

## Setup

1. A running parent agent with `avatar` capability and valid `init.json`.
2. Clean `.lingtai/` network directory; no existing `test-avatar/` sibling.

## Steps

1. **Verify pre-state** — `test-avatar/` does not exist.
   ```bash
   test ! -d <root>/test-avatar && echo "PASS" || echo "FAIL"
   ```

2. **Spawn avatar** via tool call.
   ```python
   avatar(action="spawn", name="test-avatar", type="shallow", reasoning="Test spawn")
   ```

3. **Check workdir + init.json** — directory exists, init.json is valid with correct name/admin/prompt.
   ```bash
   python3 -c "
   import json, os
   assert os.path.isdir('<root>/test-avatar')
   d = json.load(open('<root>/test-avatar/init.json'))
   assert d['manifest']['agent_name'] == 'test-avatar'
   assert d['manifest']['admin'] == {}
   assert d['prompt'] == ''
   print('PASS')
   "
   ```

4. **Check .prompt signal file** — exists and non-empty.
   ```bash
   test -s <root>/test-avatar/.prompt && echo "PASS" || echo "FAIL"
   ```

5. **Wait for heartbeat** — `.agent.heartbeat` appears within 5s.
   ```bash
   for i in $(seq 1 50); do
     test -f <root>/test-avatar/.agent.heartbeat && echo "PASS" && break
     sleep 0.1
   done
   ```

6. **Check ledger** — one `"avatar"` event for `test-avatar` with `boot_status="ok"` and positive `pid`.
   ```bash
   python3 -c "
   import json
   for line in open('<parent>/delegates/ledger.jsonl'):
       r = json.loads(line)
       if r.get('name') == 'test-avatar' and r.get('event') == 'avatar':
           assert r['boot_status'] == 'ok'
           assert isinstance(r['pid'], int) and r['pid'] > 0
           print('PASS'); break
   "
   ```

7. **Check stderr capture + no stale signals** — `logs/spawn.stderr` exists; no `.suspend/.sleep/.interrupt`.
   ```bash
   test -e <root>/test-avatar/logs/spawn.stderr && \
   ! test -f <root>/test-avatar/.suspend && \
   ! test -f <root>/test-avatar/.sleep && \
   ! test -f <root>/test-avatar/.interrupt && \
   echo "PASS" || echo "FAIL"
   ```

8. **Verify init.json preset** — `active == default`, no materialized `llm`/`capabilities`.
   ```bash
   python3 -c "
   import json
   d = json.load(open('<root>/test-avatar/init.json'))
   p = d['manifest']['preset']
   assert p['active'] == p['default']
   assert 'llm' not in d['manifest']
   assert 'capabilities' not in d['manifest']
   print('PASS')
   "
   ```

## Pass criteria

- `test-avatar/` directory created; `init.json` valid JSON with `agent_name`, `admin={}`, `prompt=""`, preset `active==default`.
- `.prompt` file non-empty; `.agent.heartbeat` appears within 5s.
- Ledger has `"avatar"` event with `boot_status="ok"` and positive pid.
- `logs/spawn.stderr` exists; no stale signal files.

## Output template

```
## Spawn Test
| Step | Check | Result |
|------|-------|--------|
| 1 | Pre-state clean | |
| 2 | Spawn call | |
| 3 | Workdir + init.json | |
| 4 | .prompt written | |
| 5 | Heartbeat appears | |
| 6 | Ledger entry | |
| 7 | stderr + no stale | |
| 8 | Preset correct | |
```
