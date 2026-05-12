# src/lingtai/capabilities/

Root capabilities package — registry, capability normalization, and setup dispatcher for composable agent capabilities.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | Role |
|---|---|
| `__init__.py` | Static registry (`_BUILTIN`, `_GROUPS`), normalization helper (`normalize_capabilities`), `setup_capability()`, and `get_all_providers()` |
| `_media_host.py` | `resolve_media_host()` — extracts origin from the agent LLM `base_url` |
| `_zhipu_mode.py` | `resolve_z_ai_mode()` — returns `"ZHIPU"` (bigmodel.cn) or `"ZAI"` (international) |

**Sub-packages:** `vision/`, `web_search/` — optional individual capability modules.

## Connections

- **→ `lingtai.core.*`** — always-on capabilities registered by absolute path in `_BUILTIN`: `knowledge` (private durable memory), `skills` (skill catalog), `bash`, `avatar`, `daemon`, `mcp`, `read`, `write`, `edit`, `glob`, `grep` (`__init__.py:15-30`).
- **→ `.vision`, `.web_search`** — optional multimodal/search capabilities registered by relative path (`__init__.py:31-33`).
- **← `lingtai.agent.Agent`** — expands groups and calls `normalize_capabilities()` before setup in both construction and refresh (`src/lingtai/agent.py:57-73`, `src/lingtai/agent.py:1116-1129`).
- **← `.vision.setup()`, `.web_search.setup()`** — import `_media_host` and `_zhipu_mode` lazily inside their setup functions for provider-specific kwarg injection.

## Composition

`__init__.py` is the entry point. `_media_host.py` and `_zhipu_mode.py` are private helpers used by the sub-packages, not by the registry itself.

## State

- `_BUILTIN` is static capability name → module path (`__init__.py:15-34`). `knowledge` resolves to `lingtai.core.knowledge`; former durable-memory names `library` and `codex` are not registered.
- `_GROUPS` is static group name → list of capabilities; currently only `"file"` expands to `[read, write, edit, glob, grep]` (`__init__.py:36-39`).
- `normalize_capabilities()` is intentionally small after the breaking rename: it does not map former `library`/`codex` names, and only preserves deterministic merges such as duplicate `skills.paths`.
- No mutable runtime state is held by this package.

## Notes

- `setup_capability()` imports the target module and calls its `setup()` (`__init__.py:122-143`). Unknown names raise `ValueError` with available capabilities and groups.
- `get_all_providers()` returns user-facing capability/provider metadata for `lingtai-agent check-caps`; it intentionally lists canonical `knowledge` and `skills`, not former `library`/`codex` durable-memory names.
- `knowledge`/`skills` is a flat tool namespace split, not a nested taxonomy: private durable memory is `knowledge`; portable procedures are `skills`.
