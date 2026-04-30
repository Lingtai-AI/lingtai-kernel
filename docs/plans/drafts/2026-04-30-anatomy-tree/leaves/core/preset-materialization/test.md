---
timeout: 180
---

# Test: Preset Materialization

## Setup

1. Locate an agent with a preset configuration: `init.json` should contain `manifest.preset`.
2. Identify the preset file path from `manifest.preset.active`.
3. Optionally create a minimal test preset at `/tmp/test-preset.json`.

## Steps

1. **Verify preset block in init.json** — confirm `active`, `default`, and `allowed` are present and consistent.
   ```bash
   python3 -c "
   import json
   data = json.load(open('<workdir>/init.json'))
   preset = data.get('manifest', {}).get('preset', {})
   print(f'active: {preset.get(\"active\")}')
   print(f'default: {preset.get(\"default\")}')
   allowed = preset.get('allowed', [])
   print(f'allowed: {allowed}')
   assert preset['active'] in allowed, 'active not in allowed!'
   assert preset['default'] in allowed, 'default not in allowed!'
   print('PASS: preset block consistent')
   "
   ```

2. **Verify preset file exists and is valid** — load it and check required fields.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.presets import load_preset
   from pathlib import Path
   preset = load_preset('<preset_path>', working_dir=Path('<workdir>'))
   m = preset['manifest']
   print(f'provider: {m[\"llm\"][\"provider\"]}')
   print(f'model: {m[\"llm\"][\"model\"]}')
   print(f'description.summary: {preset[\"description\"][\"summary\"][:60]}')
   print('PASS: preset loaded and validated')
   "
   ```

3. **Verify materialization injects llm and capabilities** — run `materialize_active_preset` on a copy of init.json data.
   ```bash
   python3 -c "
   import json, copy, sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.presets import materialize_active_preset
   from pathlib import Path
   data = json.load(open('<workdir>/init.json'))
   original_llm = copy.deepcopy(data['manifest'].get('llm', {}))
   materialize_active_preset(data, Path('<workdir>'))
   new_llm = data['manifest']['llm']
   print(f'provider: {new_llm.get(\"provider\")}')
   print(f'model: {new_llm.get(\"model\")}')
   assert 'provider' in new_llm, 'no provider after materialization'
   assert 'model' in new_llm, 'no model after materialization'
   print('PASS: llm materialized')
   "
   ```

4. **Verify `context_limit` moves from llm to manifest root**.
   ```bash
   python3 -c "
   import json, sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.presets import materialize_active_preset
   from pathlib import Path
   data = json.load(open('<workdir>/init.json'))
   materialize_active_preset(data, Path('<workdir>'))
   ctx = data['manifest'].get('context_limit')
   ctx_in_llm = data['manifest']['llm'].get('context_limit')
   print(f'manifest.context_limit: {ctx}')
   print(f'manifest.llm.context_limit: {ctx_in_llm}')
   assert ctx_in_llm is None, 'context_limit leaked into llm'
   if ctx is not None:
       print('PASS: context_limit at manifest root')
   else:
       print('INFO: no context_limit set (acceptable)')
   "
   ```

5. **Verify `expand_inherit` resolves `"provider": "inherit"` in capabilities**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.presets import expand_inherit
   caps = {'web_search': {'provider': 'inherit'}}
   main_llm = {'provider': 'openai', 'api_key': 'sk-test', 'base_url': None}
   expand_inherit(caps, main_llm)
   assert caps['web_search']['provider'] == 'openai'
   assert caps['web_search']['api_key'] == 'sk-test'
   print('PASS: inherit resolved correctly')
   "
   ```

6. **Verify no-op when no preset block** — materialization should be a no-op.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.presets import materialize_active_preset
   from pathlib import Path
   data = {'manifest': {'llm': {'provider': 'x', 'model': 'y'}}}
   materialize_active_preset(data, Path('/tmp'))
   assert data['manifest']['llm']['provider'] == 'x'
   print('PASS: no-op without preset block')
   "
   ```

## Pass Criteria

- Preset block has `active`, `default`, and `allowed` with both in allowed.
- Preset file loads and validates (has `manifest.llm` with `provider`+`model`, `description.summary`).
- Materialization injects preset's `llm` and `capabilities` into the data dict.
- `context_limit` moves from `manifest.llm` to `manifest` root.
- `expand_inherit` correctly resolves `"provider": "inherit"` sentinels.
- Materialization is a no-op when `manifest.preset` is absent.

## Output Template

```
## Preset Materialization Test Results

| Check | Result | Evidence |
|-------|--------|----------|
| Preset block consistent | PASS/FAIL | active=default=..., allowed=[...] |
| Preset file valid | PASS/FAIL | provider=..., model=... |
| llm materialized | PASS/FAIL | <provider>/<model> |
| context_limit relocated | PASS/FAIL | manifest root: <N>, llm: null |
| expand_inherit | PASS/FAIL | <resolved provider> |
| No-op without preset | PASS/FAIL | <verification> |
```
