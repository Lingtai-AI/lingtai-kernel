# Virtual Environment Resolution

## What

When the system needs to launch a LingTai agent process (via `lingtai run`, CPR, avatar spawn, or deferred refresh), it must find a Python virtual environment that has the `lingtai` package installed. The `venv_resolve` module provides a deterministic search order with auto-creation fallback.

## Contract

### Search Order

```
1. init.json → venv_path    → test (import lingtai) → use if working
2. ~/.lingtai-tui/runtime/venv/ → test (import lingtai) → use if working
3. Neither works → auto-create ~/.lingtai-tui/runtime/venv/
```

### Step 1: init.json `venv_path`

If `init.json` contains a `venv_path` field, that path is resolved (via `config_resolve.resolve_paths` → expanduser + relative-to-working-dir) and tested.

Test: `<venv_path>/bin/python` (or `Scripts/python.exe` on Windows) must:
- Exist as a file.
- Successfully `import lingtai` when executed (subprocess, 10s timeout).

If the test passes, use this venv. If it fails (missing binary, import error, timeout), fall through to step 2.

### Step 2: Global Runtime Venv

Default location: `~/.lingtai-tui/runtime/venv/`

Same test as step 1: binary exists and `import lingtai` succeeds.

### Step 3: Auto-Create

If neither venv works:
1. Find Python ≥ 3.11 on the system (`python3` then `python`, via `shutil.which`).
2. Run `python -m venv ~/.lingtai-tui/runtime/venv/`.
3. Run `pip install lingtai` inside the new venv.
4. Print progress to stderr.
5. Return the newly created venv path.

If Python ≥ 3.11 cannot be found, raise `RuntimeError`.

### `venv_python(venv_dir)` → Python Path

Returns the Python executable path inside a venv:
- Unix: `<venv_dir>/bin/python`
- Windows: `<venv_dir>/Scripts/python.exe`

### `ensure_package(pip_name, import_name)` — Lazy Package Install

For optional dependencies (e.g. `web_search` needs `duckduckgo_search`):
1. Try `__import__(import_name)` — if it works, return immediately.
2. If `uv` is on PATH → `uv pip install <pip_name> -p <sys.executable>` (fast).
3. Otherwise → `<sys.executable> -m pip install <pip_name>`.
4. Verify by importing again; raise `ImportError` if still missing.

### `CONDA_PREFIX` Handling

The current implementation does **not** check `CONDA_PREFIX`. Conda environments are treated like any other venv — if `venv_path` in init.json points to a conda env that has lingtai installed, it will work. But there is no automatic conda detection.

### Callers

| Caller | Context |
|--------|---------|
| `_cpr_agent()` | Resuscitating a suspended agent — resolves target's venv from its init.json. |
| `_build_launch_cmd()` | Building the relaunch command for refresh — resolves own venv. |
| CLI `lingtai run` | Entry point — resolves venv before launching agent process. |

## Source

| Component | File | Lines |
|-----------|------|-------|
| `resolve_venv()` | `src/lingtai/venv_resolve.py` | 19-37 |
| `venv_python()` | `src/lingtai/venv_resolve.py` | 40-44 |
| `_test_venv()` | `src/lingtai/venv_resolve.py` | 47-59 |
| `_create_venv()` | `src/lingtai/venv_resolve.py` | 62-91 |
| `ensure_package()` | `src/lingtai/venv_resolve.py` | 94-128 |
| `_find_python()` | `src/lingtai/venv_resolve.py` | 130-146 |
| `_cpr_agent()` (caller) | `src/lingtai/agent.py` | 393-437 |
| `_build_launch_cmd()` (caller) | `src/lingtai/agent.py` | 976-982 |

## Why

The auto-creation fallback exists because first-time users should not need to manually create a venv — `lingtai run` should just work. The `import lingtai` test (rather than just checking for `bin/python`) catches venvs that exist but have a corrupted or missing install. Without this test, agents would launch with a broken Python and fail in opaque ways downstream. The search order (init.json → global) lets projects pin a specific venv while keeping a shared fallback for agents that don't care.

## Related

- **config-resolve**: `venv_path` in init.json goes through `resolve_paths` for path normalization.
- **agent-state-machine**: CPR uses venv resolution to find the right Python for relaunching suspended agents.
- **preset-materialization**: Avatar spawn records carry no venv info; each agent resolves its own venv from its init.json.
