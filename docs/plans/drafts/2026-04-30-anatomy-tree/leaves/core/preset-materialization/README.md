# Preset Materialization

## What

Preset materialization substitutes a named preset's `llm` and `capabilities` into an agent's `init.json` data, producing a fully-resolved manifest before validation. Agents swap their entire runtime (LLM provider/model, capabilities) by changing a single string.

## Contract

### Preset as Path

A preset's **name is its path** — no separate stem identity. Three forms: absolute (`/Users/me/...`), home-relative (`~/...`), working-dir-relative (`./presets/...`). Resolved via `expanduser` at read time only — **no canonicalization on write**.

### Preset File Shape

Required: `{name, description: {summary: str, tier?: "1"-"5"}, manifest: {llm: {provider, model, ...}, capabilities?: {...}}}`. `context_limit` must live inside `manifest.llm`, not at `manifest` root.

### `manifest.preset` in init.json

```json
"preset": {
  "default": "~/.../cheap.json",
  "active": "~/.../premium.json",
  "allowed": ["~/.../cheap.json", "~/.../premium.json"]
}
```

- **`default`**: Agent's "home" preset; AED auto-fallback target; avatar spawn source.
- **`active`**: Currently materialized. Changed by `system(refresh, preset=...)`.
- **`allowed`**: Whitelist — registration IS authorization. No implicit directory scan.
- Both `default` and `active` **MUST** be in `allowed`.

### Materialization Flow (`materialize_active_preset`)

Called by `_read_init()` BEFORE `validate_init()`:

1. Read `manifest.preset.active`. 2. `load_preset(active)` — load + validate file. 3. Copy preset's `manifest.llm` → init data's `manifest.llm`. 4. Copy preset's `manifest.capabilities` → init data. 5. Move `context_limit` from `llm` to manifest root. 6. **Mutates in place**.

### Activation Flow (`_activate_preset`)

Called by `system(refresh, preset=...)`:

1. Read current `init.json` from disk. 2. Load target preset. 3. Substitute `llm` + `capabilities`. 4. Set `preset.active = name`. 5. Initialize `default` if unset. 6. Ensure `name` in `allowed` (safety belt). 7. Atomic write (`.tmp` + `os.replace`).

### `expand_inherit` — Capability Provider Inheritance

Capabilities with `"provider": "inherit"` copy the main LLM's provider, credentials, and `base_url`. **`model` is NOT inherited** — capabilities pick their own. Resolved by `expand_inherit()` after materialization, before validation.

### Preset Validation (`load_preset`)

Validates: file exists + valid JSONC; `manifest.llm` has `provider`/`model`; `description.summary` non-empty; `tier` in `("1"-"5")`; `context_limit` inside `llm` only. Kernel migrations run on directory before validation (idempotent).

## Source

| Component | File | Lines |
|-----------|------|-------|
| `load_preset()` | `src/lingtai/presets.py` | 177-291 |
| `materialize_active_preset()` | `src/lingtai/presets.py` | 292-338 |
| `expand_inherit()` | `src/lingtai/presets.py` | 373-392 |
| `resolve_preset_name()` | `src/lingtai/presets.py` | 77-96 |
| `resolve_allowed_presets()` | `src/lingtai/presets.py` | 99-120 |
| `_activate_preset()` | `src/lingtai/agent.py` | 632-695 |
| `_read_init()` (calls materialize) | `src/lingtai/agent.py` | 582-631 |
| `validate_init()` (preset block) | `src/lingtai/init_schema.py` | 104-175 |

## Why

Materialization exists so that `init.json` can reference a preset by path rather than embedding the full LLM config — one string change swaps the entire runtime. The "name is path" design avoids a global registry of preset names (which would need its own lifecycle). `context_limit` lives at manifest root (not inside llm) in init.json because it governs the agent's conversation window, which is a runtime concern separate from the LLM's capability — moving it into the preset's llm block was a later normalization that the init.json schema declined to follow.

## Related

- **preset-allowed-gate**: `allowed` enforcement and connectivity checks.
- **config-resolve**: Materialization runs BEFORE config resolution.
- **preset-connectivity**: `system(action='presets')` endpoint reachability probing.
- **agent-state-machine**: AED exhaustion auto-fallback to `manifest.preset.default`.
