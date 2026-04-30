---
timeout: 180
---

# Test: Preset Allowed Gate

## Setup

1. Locate an agent with `manifest.preset` configured in `init.json`.
2. Identify the allowed preset paths and the active preset path.

## Steps

1. **Verify `allowed` is a non-empty list of strings**.
   ```bash
   python3 -c "
   import json
   data = json.load(open('<workdir>/init.json'))
   allowed = data['manifest']['preset']['allowed']
   assert isinstance(allowed, list), f'allowed is {type(allowed)}'
   assert len(allowed) > 0, 'allowed is empty'
   assert all(isinstance(p, str) for p in allowed), 'non-string entries'
   print(f'PASS: {len(allowed)} presets in allowed')
   for p in allowed: print(f'  - {p}')
   "
   ```

2. **Verify `active` and `default` are both in `allowed`**.
   ```bash
   python3 -c "
   import json
   data = json.load(open('<workdir>/init.json'))
   p = data['manifest']['preset']
   assert p['active'] in p['allowed'], f'active {p[\"active\"]} not in allowed'
   assert p['default'] in p['allowed'], f'default {p[\"default\"]} not in allowed'
   print('PASS: active and default both in allowed')
   "
   ```

3. **Verify each allowed preset file exists and loads**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.presets import load_preset
   from pathlib import Path
   import json
   data = json.load(open('<workdir>/init.json'))
   allowed = data['manifest']['preset']['allowed']
   wd = Path('<workdir>')
   for p in allowed:
       try:
           preset = load_preset(p, working_dir=wd)
           print(f'OK: {p} -> {preset[\"manifest\"][\"llm\"][\"provider\"]}/{preset[\"manifest\"][\"llm\"][\"model\"]}')
       except Exception as e:
           print(f'FAIL: {p} -> {e}')
   "
   ```

4. **Check connectivity for each allowed preset**.
   ```bash
   python3 -c "
   import sys, json; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.preset_connectivity import check_connectivity
   from lingtai.presets import load_preset
   from pathlib import Path
   data = json.load(open('<workdir>/init.json'))
   wd = Path('<workdir>')
   for p in data['manifest']['preset']['allowed']:
       preset = load_preset(p, working_dir=wd)
       llm = preset['manifest']['llm']
       result = check_connectivity(
           provider=llm.get('provider'),
           base_url=llm.get('base_url'),
           api_key_env=llm.get('api_key_env'),
       )
       print(f'{p}: {result[\"status\"]} (latency: {result.get(\"latency_ms\")}ms)')
   "
   ```

5. **Verify `_activate_preset` auto-adds to `allowed`** — inspect the safety belt behavior in source.
   ```bash
   grep -n "preset_allowed_widened\|direct_activate_bypassed" <agent.py_path>
   # Should show the two auto-add paths in _activate_preset()
   ```

6. **Verify init_schema rejects missing `allowed`** — confirm the validator catches it.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.init_schema import validate_init
   # Missing allowed
   data = {
       'manifest': {
           'llm': {'provider': 'x', 'model': 'y'},
           'preset': {'active': 'a.json', 'default': 'd.json'}
       },
       'covenant': '', 'principle': '', 'pad': '', 'prompt': '', 'soul': ''
   }
   try:
       validate_init(data)
       print('FAIL: should have raised')
   except ValueError as e:
       assert 'allowed' in str(e), f'Wrong error: {e}'
       print(f'PASS: rejected with: {e}')
   "
   ```

## Pass Criteria

- `allowed` is a non-empty list of path strings.
- `active` and `default` both appear in `allowed`.
- Each allowed preset file exists and passes `load_preset` validation.
- Connectivity check runs for each preset (results are informational, not pass/fail).
- `validate_init()` rejects init.json when `allowed` is missing or empty.
- `_activate_preset` has belt-and-braces auto-add to `allowed`.

## Output Template

```
## Preset Allowed Gate Test Results

| Check | Result | Evidence |
|-------|--------|----------|
| allowed non-empty list | PASS/FAIL | <count> entries |
| active in allowed | PASS/FAIL | <active path> |
| default in allowed | PASS/FAIL | <default path> |
| All presets loadable | PASS/FAIL | <per-preset status> |
| Connectivity checked | INFO | <per-preset status/latency> |
| init_schema rejects missing | PASS/FAIL | <error message> |
| Auto-add safety belt | PASS/FAIL | <source verification> |
```
