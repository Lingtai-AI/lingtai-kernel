# Preset Allowed Gate

## What

The allowed gate controls which presets an agent may switch to at runtime. It is a whitelist declared explicitly in `manifest.preset.allowed` — there is no implicit "everything in the library directory" fallback. Registration in `allowed` IS authorization.

## Contract

### The `allowed` List

```json
{
  "manifest": {
    "preset": {
      "default": "~/.lingtai-tui/presets/cheap.json",
      "active": "~/.lingtai-tui/presets/premium.json",
      "allowed": [
        "~/.lingtai-tui/presets/cheap.json",
        "~/.lingtai-tui/presets/premium.json",
        "~/.lingtai-tui/presets/experimental.json"
      ]
    }
  }
}
```

- **List of path strings** — same format as `default`/`active` (absolute, `~/`, or working-dir-relative).
- **Non-empty** — must contain at least the `default` preset.
- **Both `default` and `active` must be members** — validated by `init_schema.validate_init()`.

### What Prevents Switching

1. **Not in `allowed`** — `_activate_preset()` checks (belt-and-braces) and auto-adds the name if missing. But `validate_init()` strictly enforces that `active` and `default` are in `allowed` at boot time. A preset not in `allowed` that's passed to `system(refresh, preset=...)` will be caught by the `system` intrinsic's gate check before `_activate_preset` is called.

2. **Connectivity check** — `preset_connectivity.check_connectivity()` performs a two-tier probe:
   - **Credential check** (free, no network): Is `api_key_env` set in the environment? If not → `no_credentials`.
   - **Endpoint reachability** (network): TCP connect to the LLM's `base_url` host (default 443/80). Timeout 2s. → `ok` (with latency), `unreachable`, or `no_credentials`.
   - Result is reported per-preset in `system(action='presets')` listing. Agents are instructed to avoid presets with `unreachable` or `no_credentials` status, but this is advisory, not a hard block.

3. **Preset file missing** — `load_preset()` raises `KeyError` if the file doesn't exist. The `system` intrinsic catches this and reports the error.

4. **Preset malformed** — `load_preset()` validates schema and raises `ValueError` for missing `manifest.llm`, bad `description`, wrong `context_limit` location, etc.

### Connectivity: No Caching

Every `system(action='presets')` call probes fresh. Caching would let an agent confidently swap into a preset that went down between the cache write and the swap. The agent calls `presets` deliberately as a planning step; a 0.2-2s round-trip is acceptable.

### Gate Enforcement Points

| Check | Where | Type |
|-------|-------|------|
| `allowed` membership | `system` intrinsic (pre-activate) + `_activate_preset` (belt-and-braces) | Hard block |
| `active`/`default` in `allowed` | `validate_init()` at boot | Hard error |
| Connectivity status | `system(action='presets')` listing | Advisory |
| File exists | `load_preset()` | Hard error |
| Schema valid | `load_preset()` | Hard error |

### AED Auto-Fallback

When AED (Automatic Error Detection) retries are exhausted and the agent is on a non-default preset, the kernel attempts to swap to `manifest.preset.default` before going ASLEEP. This is a one-shot per process — if the default preset also fails, the agent sleeps. This bypasses the `allowed` gate because `_activate_preset` auto-adds `default` to `allowed` if it's missing.

## Source

| Component | File | Lines |
|-----------|------|-------|
| `validate_init()` (allowed checks) | `src/lingtai/init_schema.py` | 114-170 |
| `_activate_preset()` (gate + auto-add) | `src/lingtai/agent.py` | 625-687 |
| `check_connectivity()` | `src/lingtai/preset_connectivity.py` | 63-115 |
| `check_many()` (parallel checks) | `src/lingtai/preset_connectivity.py` | 118-133 |
| `_PROVIDER_DEFAULT_URLS` | `src/lingtai/preset_connectivity.py` | 27-37 |
| AED auto-fallback | `src/lingtai_kernel/base_agent.py` | 982-999 |
| `_activate_default_preset()` | `src/lingtai/agent.py` | 689-697 |

## Why

An explicit whitelist (`allowed`) was chosen over a directory scan because agents share preset libraries on disk — without gating, an avatar could swap itself to a parent's expensive tier-5 preset, silently burning budget. The connectivity check is advisory (not a hard block) because an unreachable endpoint might recover in seconds; hard-blocking would prevent an agent from optimistically trying a preset that came back online between the check and the swap.

## Related

- **preset-materialization**: The gate operates before materialization — you must pass the gate to change `active`.
- **agent-state-machine**: AED exhaustion triggers auto-fallback through the gate.
- **`system` tool**: `action='presets'` lists presets with connectivity; `action='refresh', preset=...'` triggers the gate.
