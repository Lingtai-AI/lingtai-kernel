---
related_files:
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/tools/vision/ANATOMY.md
  - src/lingtai/services/vision/__init__.py
  - src/lingtai/services/vision/anthropic.py
  - src/lingtai/services/vision/codex.py
  - src/lingtai/services/vision/local.py
  - src/lingtai/services/vision/openai.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If code or citations drift, update this
  map with the code change and run the architecture/document checks.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# src/lingtai/services/vision/

Standalone image-understanding services. Each service owns its SDK client and
credentials; `mlx` is an explicit on-device pseudo-provider. The API services
are family services: `openai` and `anthropic` accept any compatible endpoint
through `base_url`, matching the four LLM provider families.

## Components

| File | Role |
|---|---|
| `__init__.py:20-93` | `VisionService`, MIME map, image readers, the shared OpenAI-compatible message builder, and the blank-key guard |
| `__init__.py:95-131` | `create_vision_service()` lazy factory for `openai`, `anthropic`, `codex`, and `mlx`; any other name raises `ValueError` |
| `anthropic.py:9-71` | Anthropic Messages image service; accepts model, endpoint, headers, and token limit |
| `openai.py:7-84` | OpenAI Chat Completions or Responses image service; preserves model, endpoint, headers, wire, and output limit |
| `codex.py:10-82` | Codex Responses service using OAuth token and current model/endpoint |
| `local.py:20-72` | On-device mlx-vlm pseudo-provider with lazy model loading |

## Connections

- `src/lingtai/tools/vision/__init__.py` imports the factory lazily during setup;
  its `local` route (a local OpenAI-compatible server) constructs
  `OpenAIVisionService` directly.
- API services read images through `_read_image()` and encode them as required by
  their wire; mlx passes the file path to mlx-vlm.
- OpenAI Responses uses `input_text`/`input_image` and `max_output_tokens`; Codex
  retains its separate streaming Responses request shape.

## Composition

The factory dispatches only to the four services named above. Vendors other
than OpenAI and Anthropic are reached by pointing the `openai` or `anthropic`
service at their compatible endpoint; no per-vendor or MCP vision service
remains here.

## State

Services keep their client/model configuration in memory. The mlx service keeps
its lazy model references after first load; no service writes agent
working-directory state.

## Notes

The capability layer supplies the active provider's effective endpoint, model,
credential, and headers only when the requested family is the active one, and
sends an inherited credential only to that same endpoint.
