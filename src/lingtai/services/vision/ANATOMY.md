---
related_files:
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/services/vision/__init__.py
  - src/lingtai/services/vision/anthropic.py
  - src/lingtai/services/vision/codex.py
  - src/lingtai/services/vision/gemini.py
  - src/lingtai/services/vision/local.py
  - src/lingtai/services/vision/mimo.py
  - src/lingtai/services/vision/openai.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If code or citations drift, update this
  map with the code change and run the architecture/document checks.
---
# src/lingtai/services/vision/

Standalone image-understanding services. Each service owns its SDK client and
credentials; `local` is an explicit on-device pseudo-provider.

## Components

| File | Role |
|---|---|
| `__init__.py:18-79` | `VisionService`, MIME map, image readers, and shared OpenAI-compatible message builder |
| `__init__.py:82-125` | `create_vision_service()` lazy factory for `anthropic`, `openai`, `gemini`, `mimo`, `codex`, and `local` |
| `anthropic.py:9-70` | Anthropic Messages image service; accepts active model, endpoint, headers, and token limit |
| `openai.py:7-72` | OpenAI Chat Completions or Responses image service; preserves model, endpoint, headers, wire, and output limit |
| `mimo.py:26-59` | MiMo Chat Completions service; constructor accepts only API key, model, endpoint, and token limit |
| `gemini.py:7-53` | Gemini SDK image service |
| `codex.py:11-79` | Codex Responses service using OAuth token and current model/endpoint |
| `local.py:20-72` | Local mlx-vlm pseudo-provider with lazy model loading |

## Connections

- `src/lingtai/tools/vision/__init__.py` imports the factory lazily during setup.
- API services read images through `_read_image()` and encode them as required by
  their wire; local passes the file path to mlx-vlm.
- OpenAI Responses uses `input_text`/`input_image` and `max_output_tokens`; Codex
  retains its separate streaming Responses request shape.

## Composition

The factory dispatches only to the six services named above. MiniMax is routed
by the capability layer to Anthropic, and compatible aliases are routed to the
OpenAI or Anthropic service; no MCP vision service remains here.

## State

Services keep their client/model configuration in memory. Local keeps its lazy
model references after first load; no service writes agent working-directory
state.

## Notes

The capability layer supplies the active preset model and endpoint when the
route supports them. It supplies active headers to Anthropic/OpenAI where their
constructors accept them, and never forwards headers or wire metadata to MiMo.
