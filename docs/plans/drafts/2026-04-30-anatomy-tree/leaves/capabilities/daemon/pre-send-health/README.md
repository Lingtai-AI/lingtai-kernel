# pre-send-health — Pre-Dispatch Validation

## What

Before any emanation thread is scheduled, the daemon manager performs a battery of pre-flight checks: capacity, preset loadability, LLM connectivity, capability instantiation, tool surface validation, and filesystem construction. A failure at any stage refuses the entire batch atomically.

## Contract

### 1. Capacity gate (see `max-rpm-gating` leaf)

Completed emanations are pruned from the registry, then `running + len(tasks) > max_emanations` is checked. No I/O is spent if the gate fails.

### 2. Preset validation (per-task)

For each task specifying a `preset`:

- **Loadability**: `load_preset(preset_name, working_dir=...)` must succeed. `KeyError`/`ValueError` → refuse whole batch.
- **Connectivity**: `check_connectivity(provider, base_url, api_key_env)` must return `status: "ok"`. Live network probe to LLM endpoint. Failure → refuse whole batch.
- **Capability instantiation**: `_instantiate_preset_capabilities(preset_caps, preset_llm)` sets up capabilities in a `_ToolCollector` sandbox. If any `setup()` raises → refuse whole batch.

All three happen **before** any `ThreadPoolExecutor.submit()`.

### 3. Tool surface validation (per-task)

`_build_tool_surface(spec["tools"], preset_surface)` expands group names (e.g. `"file"` → `read/write/edit/glob/grep`), filters the `EMANATION_BLACKLIST` (`{"daemon", "avatar", "psyche", "library"}`), and verifies every requested tool exists. Unknown tools → refuse whole batch.

### 4. Filesystem construction (per-task)

`DaemonRunDir.__init__()` creates the run directory with `mkdir(parents=True, exist_ok=False)`. The `exist_ok=False` guarantees uniqueness. The `run_id` format `<handle>-<YYYYMMDD-HHMMSS>-<hex6>` makes collisions astronomically unlikely.

On construction: `daemon.json` written (atomic), `.prompt` written, `.heartbeat` touched, `daemon_start` event appended to `logs/events.jsonl`.

### 5. Heartbeat

`.heartbeat` is touched on construction, every `set_current_tool()`, and every `bump_turn()`. External monitors can check mtime to detect stale emanations.

### 6. What is NOT checked

- **Process-level health**: emanations are threads (not processes), so the thread-pool + cancel-event + watchdog pattern replaces PID monitoring.
- **LLM response quality**: pre-flight only checks endpoint reachability.

## Source

Anchored by function name; re-locate with `grep -n 'def <func>' <file>` if line numbers drift.

- `__init__.py::_handle_emanate()` — orchestrates the entire validation cascade
- `__init__.py::DaemonManager.__init__()` — stores `_max_emanations`, `_max_turns`, `_timeout`
- `__init__.py::_instantiate_preset_capabilities()` — sandboxed capability setup via `_ToolCollector`
- `__init__.py::_build_tool_surface()` — group expansion, blacklist filter, existence check
- `__init__.py::_ToolCollector` — proxy that intercepts `add_tool()` into local dicts
- `__init__.py::EMANATION_BLACKLIST` — `{"daemon", "avatar", "psyche", "library"}`
- `run_dir.py::DaemonRunDir.__init__()` — mkdir, daemon.json, .prompt, .heartbeat, events
- `lingtai/preset_connectivity.py::check_connectivity()` — live LLM endpoint probe

## Related

- `../verify_daemon_leaves.py` — 12 static checks for this leaf (run against source)
- `max-rpm-gating` leaf — the capacity gate that precedes these checks
- `daemon-manual` skill — how to inspect `daemon.json` and `.heartbeat` for health
