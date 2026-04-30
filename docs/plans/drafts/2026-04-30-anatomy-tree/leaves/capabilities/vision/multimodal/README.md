# vision/multimodal

## What

The vision capability provides image understanding by routing through a
provider-specific `VisionService`. Given an image path and optional question,
it returns a text analysis. The capability supports 7 providers via a factory
pattern, with automatic MIME detection and provider-specific kwarg injection.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `image_path` | string | **yes** | — | Absolute or relative path to image file. |
| `question` | string | no | `"Describe what you see in this image."` | Prompt guiding the analysis. |

**Return:** `{status: "ok", analysis: "<text>"}` on success.

**Errors:**
- Missing `image_path` → `{"status": "error", "message": "Provide image_path"}`
- File not found → `{"status": "error", "message": "Image file not found: <path>"}`
- Empty analysis → `{"status": "error", "message": "Vision analysis returned no response."}`
- Exception → `{"status": "error", "message": "Vision analysis failed: <error>"}`

### Provider routing

| Provider | Module | Auth | Extra kwargs |
|----------|--------|------|-------------|
| `anthropic` | `services/vision/anthropic.py` | api_key | — |
| `openai` | `services/vision/openai.py` | api_key | — |
| `gemini` | `services/vision/gemini.py` | api_key | — |
| `minimax` | `services/vision/minimax.py` | api_key | `api_host` (auto-resolved) |
| `zhipu` | `services/vision/zhipu.py` | api_key | `z_ai_mode` (auto-resolved) |
| `mimo` | `services/vision/mimo.py` | api_key | — |
| `local` | `services/vision/local.py` | none | `mlx-vlm` on Apple Silicon |

**Provider mismatch:** If the agent's LLM provider is not in the vision
providers list and no explicit vision provider is set, the capability silently
skips registration (`capability_skipped` log event). No error raised.

### Image preprocessing

`_read_image()` in `services/vision/__init__.py`:
- Reads raw bytes from `image_path`
- MIME detection by extension: `.png`→`image/png`, `.jpg`/`.jpeg`→`image/jpeg`,
  `.webp`→`image/webp`, `.gif`→`image/gif`
- Fallback MIME: `image/png`
- Raises `FileNotFoundError` if path doesn't exist

### Setup flow

1. If `vision_service` provided directly → use it.
2. If `provider` given → check against `PROVIDERS["providers"]` list.
3. If provider not in list → silently skip (return `None`).
4. Inject provider-specific kwargs (minimax→`api_host`, zhipu→`z_ai_mode`).
5. Call `create_vision_service(provider, api_key, **kwargs)`.
6. Register tool via `agent.add_tool("vision", ...)`.

## Source

- Capability: `capabilities/vision/__init__.py:53` — `VisionManager`
- Handler: `capabilities/vision/__init__.py:64` — `handle()`
- Setup: `capabilities/vision/__init__.py:90` — `setup()`
- Service factory: `services/vision/__init__.py:63` — `create_vision_service()`
- MIME detection: `services/vision/__init__.py:38` — `_MIME_BY_EXT`
- Image reader: `services/vision/__init__.py:47` — `_read_image()`
- Provider list: `capabilities/vision/__init__.py:27` — `PROVIDERS`

## Related

- **vision skill** — decision tree for which path to use (built-in / minimax-cli / local VLM).
- **minimax-cli skill** — shell-based vision via `mmx vision` (alternative to MCP).
- **services/vision/local.py** — on-device vision via `mlx-vlm` (opt-in, not advertised).
