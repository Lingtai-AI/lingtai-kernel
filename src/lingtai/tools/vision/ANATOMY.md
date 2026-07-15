---
related_files:
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/services/vision/ANATOMY.md
  - src/lingtai/tools/vision/glossary-en.md
  - src/lingtai/tools/vision/glossary-zh.md
  - src/lingtai/tools/vision/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/tools/vision/

Vision capability — image understanding via pluggable VisionService backends.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 232 | `VisionManager`, `setup()`, provider registry, tool schema |

**Key symbols:**
- `PROVIDERS` (L28-35) — supported providers include `codex`, `codex-pool`, and `codex_pool`; no static default.
- `VisionManager` (L57-91) — handles tool calls; resolves relative image paths via `agent._working_dir` (L75-80).
- `setup()` (L94-232) — entry point called by `capabilities.setup_capability()`. Creates `VisionManager`, registers `"vision"` tool on agent (L230-231).

## Connections

- **→ `lingtai.services.vision.VisionService`** (L24-26) — abstract service interface, imported only for type checking.
- **→ `lingtai.services.vision.create_vision_service`** (L206-223) — lazy factory import for dedicated provider services.
- **→ `lingtai.auth.codex_pool.select_codex_pool_auth`** (L195-199) — lazy pool auth selection for Codex-family vision aliases.
- **→ `capabilities._media_host.resolve_media_host`** (L212-213) — injected for `minimax` provider.
- **→ `capabilities._zhipu_mode.resolve_z_ai_mode`** (L215-216) — injected for `zhipu` provider.
- **→ `lingtai.kernel.base_agent.BaseAgent`** — type-only (L25).
- **← `capabilities.__init__`** — registered as `".vision"` in `_BUILTIN`.

## Composition

Single file. No internal state — `VisionManager` instances hold agent + service refs.

## State

- `VisionManager._agent` / `_vision_service` (L65-66) — per-agent instance state. Stateless tool handler otherwise.
- `PROVIDERS` dict is module-level constant.

## Notes

- OpenAI-compat fallback: if the agent's provider isn't in `PROVIDERS` but the main LLM's `_provider_defaults["api_compat"] == "openai"`, vision routes through `OpenAIVisionService` using the LLM's own `base_url`/`model`/`api_key`. Lets `custom`/`openrouter`/`deepseek`/`kimi` users opt into vision via `vision: {"provider": "inherit"}` in their preset. Succeeds only if the relay+model actually support OpenAI-style `image_url` content blocks; otherwise the runtime call surfaces the relay's error.
- Graceful skip: if the agent's provider isn't in `PROVIDERS` AND the LLM is not OpenAI-compatible, setup returns `None` silently. Agent logs `capability_skipped`.
- Codex-family aliases flow to `create_vision_service("codex", api_key=None)` (L173-207); pool aliases select a non-secret auth path first, while direct Codex honors its provider bucket's `codex_auth_path`. Active Codex-family model/endpoint values are forwarded only for a Codex-family main service.
- Provider-specific kwarg injection is opt-in per provider — prevents `TypeError` from passing unsupported kwargs to heterogeneous service constructors.
- Local mlx-vlm provider exists in `services/vision/local.py` but is intentionally hidden from `PROVIDERS` (see docstring L10-14).
