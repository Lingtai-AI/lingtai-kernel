---
name: vision-contract
tool: vision
contract_version: 1
related_files:
  - src/tools/vision/__init__.py
  - src/tools/vision/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Vision capability contract

`vision` analyzes a single image via a `VisionService`. It is a thin tool over a
provider service; all model/transport heterogeneity is resolved at setup time.
The implementation lives in `src/tools/vision/`; the code is the source of
truth.

## Routing Card

**Use this when:**
- You are editing the vision tool surface, its `VisionService` wiring, or the
  provider-resolution / api_compat fallback in `setup`.
- You are reviewing image-path handling (absolute vs working-dir-relative) or
  the provider list advertised to first-run wizards.

**Do not use this for:**
- Web/text search: use `web_search` (see `src/tools/web_search/CONTRACT.md`).
- Code navigation only: read `src/tools/vision/ANATOMY.md`.
- Adding a provider service implementation: that lives under
  `src/lingtai/services/vision/`, imported lazily from here.

**Fast paths:** tool schema -> §Tool surface; provider list -> §Scope;
lazy-import DAG rule -> §Cross-platform invariants.

## Scope

- Canonical tool name: `vision`.
- One tool, one call — no actions, no persistent state.
- Advertised providers (`PROVIDERS["providers"]`): `minimax`, `zhipu`, `mimo`,
  `gemini`, `anthropic`, `openai`, `codex`. Default provider is `None` and there
  is no agnostic inherit fallback (`fallback_on_inherit: None`).
- A local `mlx-vlm` provider (`provider="local"`) exists but is intentionally
  **not** advertised in `PROVIDERS`; users opt in explicitly.

**Non-goals:** the capability does not pre-validate that the resolved
model/relay can actually do vision — an incapable relay fails at runtime, not at
registration. It does not persist analyses or manage image storage.

## Tool surface

Schema requires `image_path`.

| Inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|
| `image_path` (required) | `question` (default `"Describe this image."`; handler default `"Describe what you see in this image."`) | `{status: "ok", analysis: <text>}` | `{status: "error", message}` — missing `image_path` (`Provide image_path`), file not found (`Image file not found: <path>`), empty model response (`Vision analysis returned no response.`), or `Vision analysis failed: <exc>` |

`image_path` may be absolute or relative; relative paths are resolved against
the agent working directory before the file-existence check.

## State & storage

None. `vision` reads the given image file and returns the model's analysis. It
writes no files and keeps no per-call state under the agent working directory.

## Cross-platform invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- The provider service is imported **lazily inside `setup`**
  (`from lingtai.services.vision import create_vision_service`, and the
  api_compat-routed branches importing `OpenAIVisionService` /
  `AnthropicVisionService`). This preserves the architectural DAG rule that the
  `tools → lingtai` import edge is only crossed inside setup/handlers, never at
  module import.
- Provider-specific kwargs are injected per branch (e.g. MiniMax `api_host`,
  Zhipu `z_ai_mode`) because vision services have heterogeneous constructor
  signatures; `api_compat` / `base_url` are popped before forwarding to
  dedicated services.
- Unknown providers route through the OpenAI- or Anthropic-compatible service
  based on `api_compat`; if neither matches, the capability logs
  `capability_skipped` and returns `CAPABILITY_UNAVAILABLE`.

There are no subprocess/shell/PTY/binary-spawn assumptions in this tool.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `setup` registers the `vision` tool with a provider or a service | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_added_by_setup`, `::test_vision_setup_with_provider_and_key`, `::test_vision_with_dedicated_service` |
| Setup raises when neither service nor provider is given | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_setup_requires_provider_or_service`, `::test_vision_setup_no_provider_raises` |
| Missing / relative `image_path` handling is correct | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_empty_image_path`, `::test_vision_missing_image`, `::test_vision_relative_path` |
| Empty model response is an error; service exceptions are caught | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_empty_response_is_error`, `::test_vision_service_error_handled` |
| Unsupported provider with an api_compat routes to the compat service | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_fallback_anthropic_compat_routes_to_anthropic_service`, `::test_vision_fallback_reads_api_compat_from_provider_bucket` |
| Unknown api_compat skips the capability with a diagnostic | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_fallback_unknown_api_compat_skips_with_diagnostic`, `::test_vision_setup_unsupported_provider_skips` |
| `api_key_env` overrides the raw key at setup | `src/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_setup_resolves_api_key_env` |
| Dedicated provider services parse valid/invalid responses correctly | `src/lingtai/services/vision/` | `tests/test_vision_services.py::test_mimo_vision_returns_content_on_valid_response`, `::test_openai_vision_returns_content_on_valid_response`, `::test_create_vision_service_unknown_provider` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Tool registers only with a usable service/provider | `tests/test_vision_capability.py::test_vision_added_by_setup` | Boot with `capabilities={"vision": {"provider": "gemini", "api_key": "..."}}` | Agent silently lacks vision, or crashes at setup |
| Image path resolution + existence check | `tests/test_vision_capability.py::test_vision_relative_path` | Call with a relative path under the agent dir | False "not found" or reads outside intent |
| Provider-resolution fallback (api_compat) | `tests/test_vision_capability.py::test_vision_fallback_reads_api_compat_from_provider_bucket` | Configure an OpenRouter-style relay, confirm routing | Unknown providers hard-fail instead of routing |
| Lazy import preserves the tools→lingtai DAG rule | import-time absence of `lingtai.services` at module load | `grep` for top-level `lingtai.services` imports (none) | Import cycle / layering violation |
| Errors are structured, never raised to the agent loop | `tests/test_vision_capability.py::test_vision_service_error_handled` | Point at an unreachable endpoint, confirm `status: error` | Tool-call crash instead of a recoverable error |

Run before merging vision changes:

```bash
python -m pytest tests/test_vision_capability.py tests/test_vision_services.py -q
```
