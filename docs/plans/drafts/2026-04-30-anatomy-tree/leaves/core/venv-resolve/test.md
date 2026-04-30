---
timeout: 180
---

# Test: Virtual Environment Resolution

## Setup

1. Identify the kernel source path: `<kernel_src>` = `src/lingtai/`.
2. Locate a working venv (e.g. the one currently running, or `~/.lingtai-tui/runtime/venv/`).

## Steps

1. **Verify `venv_python()` returns correct path**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.venv_resolve import venv_python
   from pathlib import Path
   p = venv_python(Path('/tmp/test-venv'))
   print(f'venv_python: {p}')
   assert 'python' in p.lower(), f'Unexpected: {p}'
   print('PASS: path contains python')
   "
   ```

2. **Verify `_test_venv()` detects a working venv**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.venv_resolve import _test_venv
   from pathlib import Path
   result = _test_venv(Path('<working_venv_path>'))
   print(f'test_venv: {result}')
   assert result is True, 'Expected True for working venv'
   print('PASS: working venv detected')
   "
   ```

3. **Verify `_test_venv()` rejects non-existent venv**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.venv_resolve import _test_venv
   from pathlib import Path
   result = _test_venv(Path('/nonexistent/venv'))
   print(f'test_venv: {result}')
   assert result is False, 'Expected False for nonexistent venv'
   print('PASS: nonexistent venv rejected')
   "
   ```

4. **Verify `resolve_venv()` search order — init.json venv_path wins**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.venv_resolve import resolve_venv
   from pathlib import Path
   # If init.json has venv_path, it should be tried first
   result = resolve_venv({'venv_path': '<working_venv_path>'})
   print(f'resolve_venv with init_data: {result}')
   print('PASS: resolved from init_data')
   "
   ```

5. **Verify `resolve_venv()` falls back to global runtime**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.venv_resolve import resolve_venv
   # No init_data → should try global runtime
   result = resolve_venv(None)
   print(f'resolve_venv without init_data: {result}')
   print('PASS: resolved to global or created new')
   "
   ```

6. **Verify `_DEFAULT_RUNTIME_DIR` is correct**.
   ```bash
   python3 -c "
   import sys; sys.path.insert(0, '<kernel_src>/src')
   from lingtai.venv_resolve import _DEFAULT_RUNTIME_DIR
   print(f'Default runtime: {_DEFAULT_RUNTIME_DIR}')
   assert '.lingtai-tui' in str(_DEFAULT_RUNTIME_DIR), 'Unexpected path'
   assert 'runtime' in str(_DEFAULT_RUNTIME_DIR), 'Missing runtime'
   print('PASS: default path correct')
   "
   ```

## Pass Criteria

- `venv_python()` returns platform-appropriate path (`bin/python` or `Scripts/python.exe`).
- `_test_venv()` returns `True` for a venv with lingtai installed, `False` for nonexistent paths.
- `resolve_venv()` tries `init_data.venv_path` first, then global runtime, then auto-creates.
- `_DEFAULT_RUNTIME_DIR` points to `~/.lingtai-tui/runtime/venv/`.
- Auto-creation uses Python ≥ 3.11 and installs lingtai via pip.

## Output Template

```
## Venv Resolution Test Results

| Check | Result | Evidence |
|-------|--------|----------|
| venv_python path | PASS/FAIL | <path> |
| test_venv (working) | PASS/FAIL | True |
| test_venv (nonexistent) | PASS/FAIL | False |
| resolve_venv (init_data) | PASS/FAIL | <resolved path> |
| resolve_venv (fallback) | PASS/FAIL | <resolved path> |
| default runtime dir | PASS/FAIL | <path> |
```
