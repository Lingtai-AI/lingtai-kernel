# src/lingtai/capabilities/

Root capabilities package — registry, capability normalization, and setup dispatcher for composable agent capabilities.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | Role |
|---|---|
| `__init__.py` | **Shim (SDK-01).** Re-exports the registry surface (`_BUILTIN`, `_GROUPS`, `CORE_DEFAULTS`, `normalize_capabilities`, `apply_core_defaults`, `expand_groups`, `setup_capability`, `get_all_providers`) from `lingtai_sdk.capabilities`. Kept as a package (not a plain module) because `vision/`, `web_search/`, `_media_host.py`, `_zhipu_mode.py` still live here |
| `_media_host.py` | `resolve_media_host()` — extracts origin from the agent LLM `base_url` |
| `_zhipu_mode.py` | `resolve_z_ai_mode()` — returns `"ZHIPU"` (bigmodel.cn) or `"ZAI"` (international) |

**SDK-owned (moved):** the registry seam itself (`_BUILTIN`, `_GROUPS`, `CORE_DEFAULTS`, `setup_capability`, `expand_groups`, `normalize_capabilities`, `apply_core_defaults`, `get_all_providers`) now lives in `src/lingtai_sdk/capabilities/__init__.py`. See `../../lingtai_sdk/ANATOMY.md`. Registry entries are absolute module paths: file tools resolve to `lingtai_sdk.capabilities.file.*`; the rest still resolve to `lingtai.core.*` / `lingtai.capabilities.*` until later SDK slices.

**Sub-packages:** `vision/`, `web_search/` — optional individual capability modules.

## Connections

- **→ `lingtai.core.*`** — always-on capabilities registered by absolute path in the SDK `_BUILTIN`: `knowledge` (private durable memory), `skills` (skill catalog), `bash`, `avatar`, `daemon`, `mcp`.
- **→ `lingtai_sdk.capabilities.file.*`** — the file group (`read`, `write`, `edit`, `glob`, `grep`) now resolves into the SDK (SDK-02).
- **→ `lingtai.capabilities.vision`, `lingtai.capabilities.web_search`** — optional multimodal/search capabilities (registered by absolute path in the SDK registry).
- **← `lingtai.agent.Agent`** — expands groups and calls `normalize_capabilities()` before setup in both construction and refresh (`src/lingtai/agent.py:57-73`, `src/lingtai/agent.py:1116-1129`).
- **← `.vision.setup()`, `.web_search.setup()`** — import `_media_host` and `_zhipu_mode` lazily inside their setup functions for provider-specific kwarg injection.

## Composition

`__init__.py` is a re-export shim over `lingtai_sdk.capabilities`; the registry behavior lives in the SDK. `_media_host.py` and `_zhipu_mode.py` are private helpers used by the sub-packages, not by the registry itself.

## State

- `_BUILTIN` (defined in `lingtai_sdk.capabilities`) is static capability name → absolute module path. `knowledge` resolves to `lingtai.core.knowledge`; the file group resolves to `lingtai_sdk.capabilities.file.*`; former durable-memory names `library` and `codex` are not registered.
- `_GROUPS` is static group name → list of capabilities; currently only `"file"` expands to `[read, write, edit, glob, grep]`.
- `CORE_DEFAULTS` is the static set of capability-name → default-kwargs pairs that boot automatically on every `Agent`: `knowledge`, `skills`, `bash` (`{yolo: true}`), `avatar`, `daemon`, `mcp`, and the file group (`read`/`write`/`edit`/`glob`/`grep`). `vision` and `web_search` are NOT in this set — they require provider config / API keys and stay opt-in.
- `normalize_capabilities()` is intentionally small after the breaking rename: it does not map former `library`/`codex` names, and only preserves deterministic merges such as duplicate `skills.paths`.
- No mutable runtime state is held by this package.

## Notes

- `setup_capability()` imports the target module and calls its `setup()`. Unknown names raise `ValueError` with available capabilities and groups.
- `apply_core_defaults(capabilities, disable)` overlays `CORE_DEFAULTS` with user-supplied kwargs (init.json wins on merge), then strips names listed in `disable`. A `"name": None` entry in `capabilities` is an inline opt-out equivalent to including the name in `disable`. The function is the single seam where init.json's `manifest.capabilities` becomes the effective set; called from `Agent.__init__` and `Agent._setup_from_init`.
- `get_all_providers()` returns user-facing capability/provider metadata for `lingtai-agent check-caps`; it intentionally lists canonical `knowledge` and `skills`, not former `library`/`codex` durable-memory names.
- `knowledge`/`skills` is a flat tool namespace split, not a nested taxonomy: private durable memory is `knowledge`; portable procedures are `skills`.
