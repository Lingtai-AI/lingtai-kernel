---
related_files:
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/web_search/__init__.py
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/services/websearch/ANATOMY.md
  - src/lingtai/tools/web_search/glossary-en.md
  - src/lingtai/tools/web_search/glossary-zh.md
  - src/lingtai/tools/web_search/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/tools/web_search/

Web search capability — web lookup via pluggable SearchService backends.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 146 | `WebSearchManager`, `setup()`, provider registry, search/manual tool schema |
| `manual/` | 26 files | TUI-derived `web-search-manual` router, references, assets, and extraction scripts |

**Key symbols:**
- `PROVIDERS` (L20-24) — supported: `duckduckgo`, `minimax`, `zhipu`, `gemini`, `anthropic`, `openai`. Default: `duckduckgo`. Fallback on inherit: `duckduckgo`.
- `WebSearchManager` — returns the installed manual before touching query/service state, otherwise delegates to `SearchService.search()`.
- `setup()` — entry point. Creates manager and registers the `"web_search"` tool.
- `manual/SKILL.md` — collision-safe `web-search-manual` root synchronized from the TUI web-browsing bundle; relative scripts/references ship together.

## Connections

- **→ `lingtai.i18n.t`** (L14) — i18n for tool description and schema strings.
- **→ `lingtai.services.websearch.SearchService`** (L15) — abstract service interface + `create_search_service()` factory.
- **→ `capabilities._media_host.resolve_media_host`** (L110) — injected for non-duckduckgo providers.
- **→ `capabilities._zhipu_mode.resolve_z_ai_mode`** (L113) — injected for `zhipu` provider.
- **→ `lingtai.kernel.base_agent.BaseAgent`** — type-only.
- **→ `tools._manual.load_installed_manual`** — read-only `action="manual"` loader for `.library/intrinsic/capabilities/web_search/SKILL.md`.
- **→ `Agent._install_intrinsic_manuals()`** — copies `manual/` wholesale into each agent’s intrinsic skill catalog.
- **← `capabilities.__init__`** — registered as `".web_search"` in `_BUILTIN`.

## Composition

One code module plus a self-contained manual bundle. `WebSearchManager` instances hold agent + service refs; manual files are immutable package data copied into the agent library.

## State

- `WebSearchManager._agent` / `_search_service` (L49-50) — per-agent instance. Service can be `None` (returns error on call, L57-64).
- `PROVIDERS` dict is module-level constant.
- `action="manual"` reads installed package data only; it neither constructs nor calls a search service.

## Notes

- Graceful fallback (L97-105): unsupported providers fall back to `duckduckgo` (with `api_key=None`). Unlike vision, this never skips — always provides search.
- No-provider default (L119-120): if neither `search_service` nor `provider` is given, defaults to `duckduckgo`.
- Results are formatted as markdown `**title**\nurl\nsnippet`.
- The manual bundle keeps `<skill-path>` references portable and uses the distinct `web-search-manual` frontmatter name so a TUI `web-browsing` utility can coexist.
