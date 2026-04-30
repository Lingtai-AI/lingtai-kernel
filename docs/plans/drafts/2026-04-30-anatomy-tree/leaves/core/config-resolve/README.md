# Config Resolution

## What

The config resolution chain determines the final values for every configurable field in `init.json`. It handles environment variable indirection, file-based content loading, path normalization, and capability-level env field resolution — with a clear precedence order.

## Contract

### Precedence Order (highest to lowest)

1. **Environment variables** — if `api_key_env` is set and the env var exists, it wins over the inline `api_key` value.
2. **Inline values** — fields like `api_key`, `covenant`, `principle` directly in `init.json`.
3. **File-based values** — `*_file` fields (`covenant_file`, `principle_file`, etc.) load content from disk. The inline value takes precedence; the file is a fallback (or the sole source if inline is absent).
4. **Defaults** — `AgentConfig` provides defaults for missing fields (`stamina=86400`, `soul_delay=120`, `language="en"`, `max_turns=50`, `molt_pressure=0.8`, `aed_timeout=360`, `max_rpm=60`, etc.).

### Env Variable Resolution (`resolve_env`)

For a field like `api_key`:
```
resolve_env(value=init_json["api_key"], env_name=init_json["api_key_env"])
```
- If `env_name` is set and `os.environ[env_name]` exists → return env value.
- Otherwise → return the inline `value` (may be `None`).

This applies to `manifest.llm.api_key` (via `api_key_env`) and to all `*_env` suffixed keys in capability kwargs (via `_resolve_env_fields`).

### File-Based Resolution (`resolve_file`)

For text content fields (`covenant`, `principle`, `procedures`, `brief`, `pad`, `prompt`, `comment`, `soul`):
```
resolve_file(value=init_json.get(key), file_path=init_json.get(key + "_file"))
```
- If `file_path` is set and the file exists → return file contents.
- Otherwise → return the inline `value`.

The `_file` path is resolved to absolute against `working_dir` by `resolve_paths()` before use.

### Path Resolution (`resolve_paths`)

All path-type fields are resolved against `working_dir`:

- **Top-level**: `env_file`, `venv_path`
- **Text content**: `covenant_file`, `principle_file`, `procedures_file`, `brief_file`, `pad_file`, `prompt_file`, `comment_file`, `soul_file`

Resolution: `~` is expanded. Relative paths become `<working_dir>/<path>`. Absolute paths pass through unchanged.

### Capability Env Resolution (`_resolve_capabilities`)

Each capability's kwargs dict is scanned for `*_env` keys. For each, the corresponding base key is resolved via `resolve_env`. Example:

```json
"web_search": {"api_key_env": "DUCKDUCKGO_API_KEY"}
```
→ resolves `api_key` from `os.environ["DUCKDUCKGO_API_KEY"]`.

### Env File Loading (`load_env_file`)

If `env_file` is specified, it's loaded BEFORE other resolution. Format: `KEY=VALUE` lines, `#` comments. **Existing env vars are NOT overwritten** — the env file is a fallback, not an override.

### JSONC Support (`load_jsonc`)

`init.json` and preset files support JSONC (JSON with `//` comments and trailing commas). Comment stripping is string-aware: `//` inside quoted strings is preserved (protecting URLs like `"https://host/..."`).

## Source

| Component | File | Lines |
|-----------|------|-------|
| `resolve_env()` | `src/lingtai/config_resolve.py` | 42-48 |
| `load_env_file()` | `src/lingtai/config_resolve.py` | 51-66 |
| `resolve_file()` | `src/lingtai/config_resolve.py` | 69-75 |
| `_resolve_env_fields()` | `src/lingtai/config_resolve.py` | 78-85 |
| `resolve_paths()` | `src/lingtai/config_resolve.py` | 98-118 |
| `_resolve_capabilities()` | `src/lingtai/config_resolve.py` | 121-129 |
| `load_jsonc()` | `src/lingtai/config_resolve.py` | 16-39 |
| `validate_init()` | `src/lingtai/init_schema.py` | 59-227 |
| `_setup_from_init()` (consumer) | `src/lingtai/agent.py` | 699-882 |

## Why

The `_file` indirection (e.g. `covenant_file`) exists because large text blocks in JSON are unreadable and create merge conflicts — pulling them into separate `.md` files keeps `init.json` short and diffable. Env var resolution (`api_key_env`) lets secrets stay out of version control entirely. If you remove the `_file` path, inline values become the only option, and init.json files balloon to hundreds of lines; if you remove `resolve_env`, API keys must be stored in plain JSON.

## Related

- **preset-materialization**: Preset values are injected into `init.json` data BEFORE config resolution runs, so the resolution chain operates on the fully-resolved manifest.
- **venv-resolve**: `venv_path` in init.json is one path that goes through `resolve_paths`.
- **`init_schema.py`**: Validates the structure before resolution; `validate_init()` enforces types and required fields.
