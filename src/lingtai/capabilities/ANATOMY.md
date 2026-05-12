# src/lingtai/capabilities/

Root capabilities package — registry, rename compatibility, and setup dispatcher for composable agent capabilities.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | Role |
|---|---|
| `__init__.py` | Static registry (`_BUILTIN`, `_GROUPS`), rename helpers (`canonical_capability_name`, `normalize_capabilities`), `setup_capability()`, and `get_all_providers()` |
| `_media_host.py` | `resolve_media_host()` — extracts origin from the agent LLM `base_url` |
| `_zhipu_mode.py` | `resolve_z_ai_mode()` — returns `"ZHIPU"` (bigmodel.cn) or `"ZAI"` (international) |

**Sub-packages:** `vision/`, `web_search/` — optional individual capability modules.

## Connections

- **→ `lingtai.core.*`** — always-on capabilities registered by absolute path in `_BUILTIN`: `library` (knowledge), `skills` (skill catalog), deprecated `codex` alias, `bash`, `avatar`, `daemon`, `mcp`, `read`, `write`, `edit`, `glob`, `grep` (`__init__.py:15-30`).
- **→ `.vision`, `.web_search`** — optional multimodal/search capabilities registered by relative path (`__init__.py:31-33`).
- **← `lingtai.agent.Agent`** — expands groups and calls `normalize_capabilities()` before setup in both construction and refresh (`src/lingtai/agent.py:57-73`, `src/lingtai/agent.py:1116-1129`).
- **← `.vision.setup()`, `.web_search.setup()`** — import `_media_host` and `_zhipu_mode` lazily inside their setup functions for provider-specific kwarg injection.

## Composition

`__init__.py` is the entry point. `_media_host.py` and `_zhipu_mode.py` are private helpers used by the sub-packages, not by the registry itself.

## State

- `_BUILTIN` is static capability name → module path (`__init__.py:15-34`). The deprecated `codex` name resolves to `lingtai.core.library` for one migration window (`__init__.py:19-21`).
- `_GROUPS` is static group name → list of capabilities; currently only `"file"` expands to `[read, write, edit, glob, grep]` (`__init__.py:36-39`).
- `normalize_capabilities()` is pure: old `codex` becomes new `library`; old skill-catalog `library` configs (bare `library`, `library: {}`, or `library.paths`) become `skills` unless an explicit `skills` key disambiguates, while knowledge-library-only kwargs such as `library_limit`/`codex_limit` keep `library` on the new durable-knowledge path; `paths` lists merge/dedupe for `skills` (`__init__.py:54-117`).
- No mutable runtime state is held by this package.

## Notes

- `setup_capability()` imports the target module and calls its `setup()` (`__init__.py:122-143`). Unknown names raise `ValueError` with available capabilities and groups.
- `get_all_providers()` returns user-facing capability/provider metadata for `lingtai-agent check-caps`; it intentionally lists canonical `library` and `skills` plus deprecated `codex` compatibility (`__init__.py:146-172`).
- `library`/`skills` is a flat tool namespace rename, not a nested taxonomy: old `library` tool calls cannot remain as a skill-catalog alias because `library` now names durable knowledge.
