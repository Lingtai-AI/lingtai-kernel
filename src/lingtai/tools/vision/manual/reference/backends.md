---
name: vision-backends-reference
tool: vision
related_files:
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/settings.py
  - src/lingtai/tools/vision/CONTRACT.md
maintenance: |
  Keep backend setup and troubleshooting provider-neutral where possible. Any
  installation, model, endpoint, or configuration change requires explicit
  operator consent and its existing owner procedure.
---
# Vision backend reference

## Local OpenAI-compatible server

`provider="local"` targets a local server that accepts OpenAI Chat Completions
image input (Ollama, LM Studio, vLLM, llama.cpp, or equivalent). It needs no API
key, uses `http://localhost:11434/v1` when no endpoint is supplied, and **requires
an explicit vision `model`**. The tool supplies no hidden model. The endpoint and
model are operator-owned; capability values override `settings/vision.json`.

Obtain human authorization before installation, downloads, or configuration.

1. Install/start an image-capable server and obtain its model. For Ollama,
   `ollama pull moondream` is one example; a text-only model rejects images.
2. **Select the route first** under `manifest.capabilities` in the active
   init/preset configuration:

   ```json
   {"vision": {"provider": "local"}}
   ```

   An already-active `local` provider also selects it. The settings file alone does not select
   a local provider: without that selection Vision keeps the active route.
3. Supply the model/endpoint in `settings/vision.json` (agent workdir):

   ```json
   {"schema_version": 1, "base_url": "http://localhost:11434/v1",
    "model": "moondream", "max_tokens": 1024}
   ```

   Or put `model`, `base_url`, and `max_tokens` beside `provider` in the
   capability above. Capability values win, but an invalid present local file
   still blocks setup; kwargs do not bypass file validation.

   The file accepts only `schema_version`, `base_url`, `model`, `api_key`, and
   `max_tokens`, within 64 KiB. Duplicate/unknown fields, invalid values,
   non-regular files, or unstable reads fail setup rather than falling back.
4. Refresh/relaunch through the existing authorized procedure, then `check` and
   `analyze`. Check confirms route construction, not server/model readiness.
   Use the server's expected `/v1` endpoint. `api_key` may be omitted when the
   server needs no authentication; Vision then supplies an SDK placeholder.

### Local failures

- **No direct vision provider** — use the default active compatible route, borrow
  an allowed preset explicitly, or configure `provider="local"`; then refresh.
- **Explicit model required** — set the exact model the server serves.
- **Settings invalid** — fix the owner file's schema, fields, types, or size;
  refresh rather than relying on guessed defaults.
- **Connection refused** — start the local server and retry explicitly.
- **Model not found / images rejected** — list models and select a vision model.
- **Wrong response or missing `/v1`** — use the server's OpenAI Chat Completions
  `/v1` endpoint, then check again.

## Apple MLX

`provider="mlx"` is an explicit Apple-Silicon, macOS-only on-device route. It
requires `mlx-vlm` and no API key. Loading is lazy on the first analysis and may
fetch model assets; `check` does not verify installation, cache, or offline readiness. Its route-owned default is
`mlx-community/paligemma2-3b-ft-docci-448-8bit` with `max_tokens=512`; pass an
explicit model to choose another. MLX is not silently selected, advertised as a
fallback, or installed by this manual. Ask the human before installing the
package or downloading a model.
