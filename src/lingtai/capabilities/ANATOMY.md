# src/lingtai/capabilities/

Root capabilities package — registry and setup dispatcher for composable agent capabilities.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 99 | Registry (`_BUILTIN`, `_GROUPS`), `setup_capability()`, `get_all_providers()` |
| `_media_host.py` | 25 | `resolve_media_host()` — extracts origin from agent's LLM `base_url` |
| `_zhipu_mode.py` | 19 | `resolve_z_ai_mode()` — returns `"ZHIPU"` (bigmodel.cn) or `"ZAI"` (international) |

**Sub-packages:** `vision/`, `web_search/` — individual capability modules.

## Connections

- **→ `lingtai_kernel.base_agent.BaseAgent`** — type-only import (`TYPE_CHECKING`) in all 3 files.
- **→ `lingtai.core.*`** — always-on capabilities registered by absolute path (`_BUILTIN`, L15-27): codex, bash, avatar, daemon, library, mcp, read, write, edit, glob, grep.
- **→ `.vision`, `.web_search`** — optional multimodal capabilities registered by relative path (L29-30).
- **← `lingtai_kernel` agent** — calls `setup_capability(agent, name)` during agent init.
- **← `.vision.setup()`, `.web_search.setup()`** — import `_media_host` and `_zhipu_mode` lazily inside their `setup()` functions for provider-specific kwarg injection.

## Composition

`__init__.py` is the entry point. `_media_host.py` and `_zhipu_mode.py` are private helpers used by the sub-packages (not the registry itself).

## State

- `_BUILTIN` dict (L15-31) — static, maps capability name → module path. Two forms: absolute (`lingtai.core.X`) for always-on floor, relative (`.X`) for this package.
- `_GROUPS` dict (L34-36) — maps group name to list of capability names. Only `"file"` → `[read, write, edit, glob, grep]`.
- No mutable runtime state.

## Notes

- `expand_groups()` (L39) is a pure function; groups expand before `setup_capability()` is called.
- `get_all_providers()` (L74) returns a subset of `_BUILTIN` (the user-facing ones) plus their `PROVIDERS` metadata; used by `lingtai check-caps` CLI.
- Both `_media_host` and `_zhipu_mode` are purely derived from `agent.service._base_url` — no external calls.
