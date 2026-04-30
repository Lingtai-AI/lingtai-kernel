---
timeout: 180
---

# Test: Config Resolution

## Setup

1. Locate an agent's `init.json` file (e.g. `~/.lingtai/<project>/<agent>/init.json`).
2. Optionally prepare a test `init.json` with known `_file` and `*_env` fields.

## Steps

1. **Verify init.json is valid JSONC** — confirm it parses without error.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.config_resolve import load_jsonc
   data = load_jsonc('<workdir>/init.json')
   print(f'Parsed OK: {len(data)} top-level keys')
   "
   ```

2. **Verify `resolve_env` precedence** — set a test env var and confirm it overrides inline value.
   ```bash
   TEST_KEY=from_env python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.config_resolve import resolve_env
   result = resolve_env('inline_value', 'TEST_KEY')
   assert result == 'from_env', f'Expected from_env, got {result}'
   print('PASS: env var takes precedence')
   "
   ```

3. **Verify `resolve_env` fallback** — when env var is not set, inline value is returned.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.config_resolve import resolve_env
   result = resolve_env('inline_value', 'NONEXISTENT_VAR_XYZ')
   assert result == 'inline_value', f'Expected inline_value, got {result}'
   print('PASS: inline fallback works')
   "
   ```

4. **Verify `resolve_paths` makes relative paths absolute**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.config_resolve import resolve_paths
   from pathlib import Path
   data = {'env_file': '.env', 'venv_path': './venv'}
   resolve_paths(data, Path('/Users/test/.lingtai/proj/agent'))
   assert data['env_file'].startswith('/'), f'Not absolute: {data[\"env_file\"]}'
   assert data['venv_path'].startswith('/'), f'Not absolute: {data[\"venv_path\"]}'
   print(f'PASS: env_file={data[\"env_file\"]}')
   print(f'PASS: venv_path={data[\"venv_path\"]}')
   "
   ```

5. **Verify `resolve_file` loads content from disk** — create a temp file and confirm it's read.
   ```bash
   echo "test content from file" > /tmp/test_resolve.md
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.config_resolve import resolve_file
   result = resolve_file(None, '/tmp/test_resolve.md')
   assert result == 'test content from file\n', f'Got: {result!r}'
   print('PASS: file content loaded')
   "
   rm /tmp/test_resolve.md
   ```

6. **Verify env_file is loaded and doesn't overwrite existing vars**.
   ```bash
   echo "EXISTING_VAR=from_file" > /tmp/test.env
   echo "NEW_VAR=from_file" >> /tmp/test.env
   EXISTING_VAR=original python3 -c "
   import sys, os; sys.path.insert(0, '<kernel_src>/src')
   os.environ['EXISTING_VAR'] = 'original'
   from lingtai.config_resolve import load_env_file
   load_env_file('/tmp/test.env')
   assert os.environ['EXISTING_VAR'] == 'original', 'overwritten!'
   assert os.environ['NEW_VAR'] == 'from_file', 'not loaded!'
   print('PASS: existing vars preserved, new vars loaded')
   "
   rm /tmp/test.env
   ```

## Pass Criteria

- JSONC parsing succeeds with comments and trailing commas.
- `resolve_env` returns env var value when the var is set, inline value otherwise.
- `resolve_paths` converts all path fields to absolute paths.
- `resolve_file` reads file contents when the file exists, falls back to inline otherwise.
- `load_env_file` loads new vars without overwriting existing ones.

## Output Template

```
## Config Resolution Test Results

| Check | Result | Evidence |
|-------|--------|----------|
| JSONC parsing | PASS/FAIL | <keys parsed> |
| resolve_env (env wins) | PASS/FAIL | <result> |
| resolve_env (inline fallback) | PASS/FAIL | <result> |
| resolve_paths absolute | PASS/FAIL | <resolved paths> |
| resolve_file from disk | PASS/FAIL | <content preview> |
| env_file no-overwrite | PASS/FAIL | <verification> |
```
