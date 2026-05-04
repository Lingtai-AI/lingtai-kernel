# src/lingtai/capabilities/vision/

Vision capability — image understanding via pluggable VisionService backends.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 135 | `VisionManager`, `setup()`, provider registry, tool schema |

**Key symbols:**
- `PROVIDERS` (L27-31) — supported providers: `minimax`, `zhipu`, `mimo`, `gemini`, `anthropic`, `openai`. No default, no fallback.
- `VisionManager` (L53) — handles tool calls; resolves relative image paths via `agent._working_dir` (L73).
- `setup()` (L90) — entry point called by `capabilities.setup_capability()`. Creates `VisionManager`, registers `"vision"` tool on agent (L134).

## Connections

- **→ `lingtai.i18n.t`** (L21) — i18n for tool description and schema strings.
- **→ `lingtai.services.vision.VisionService`** (L22) — abstract service interface + `create_vision_service()` factory.
- **→ `capabilities._media_host.resolve_media_host`** (L120) — injected for `minimax` provider.
- **→ `capabilities._zhipu_mode.resolve_z_ai_mode`** (L123) — injected for `zhipu` provider.
- **→ `lingtai_kernel.base_agent.BaseAgent`** — type-only (L25).
- **← `capabilities.__init__`** — registered as `".vision"` in `_BUILTIN`.

## Composition

Single file. No internal state — `VisionManager` instances hold agent + service refs.

## State

- `VisionManager._agent` / `_vision_service` (L61-62) — per-agent instance state. Stateless tool handler otherwise.
- `PROVIDERS` dict is module-level constant.

## Notes

- Graceful skip (L105-112): if the agent's provider isn't in `PROVIDERS`, setup returns `None` silently (no error). Agent logs `capability_skipped`.
- Provider-specific kwarg injection (L119-124) is opt-in per provider — prevents `TypeError` from passing unsupported kwargs to heterogeneous service constructors.
- Local mlx-vlm provider exists in `services/vision/local.py` but is intentionally hidden from `PROVIDERS` (see docstring L11-14).
