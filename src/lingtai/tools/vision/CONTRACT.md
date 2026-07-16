---
name: vision-contract
tool: vision
contract_version: 1
related_files:
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Vision capability contract

`vision` analyzes a single image via a `VisionService`. It is a thin tool over a
provider service; all model/transport heterogeneity is resolved at setup time.
The implementation lives in `src/lingtai/tools/vision/`; the code is the source of
truth.

## Routing Card

**Use this when:**
- You are editing the vision tool surface, its `VisionService` wiring, or the
  provider-resolution / api_compat fallback in `setup`.
- You are reviewing image-path handling (absolute vs working-dir-relative) or
  the provider list advertised to first-run wizards.

**Do not use this for:**
- Web/text search: use `web_search` (see `src/lingtai/tools/web_search/CONTRACT.md`).
- Code navigation only: read `src/lingtai/tools/vision/ANATOMY.md`.
- Adding a provider service implementation: that lives under
  `src/lingtai/services/vision/`, imported lazily from here.

**Fast paths:** tool schema -> §Tool surface; provider list -> §Scope;
lazy-import DAG rule -> §Cross-platform invariants.

## Scope

- Canonical tool name: `vision`.
- One tool, one call — no actions, no persistent state.
- Advertised providers (`PROVIDERS["providers"]`): `minimax`, `zhipu`, `glm`,
  `mimo`, `gemini`, `anthropic`, `openai`, `codex`, `codex-pool`,
  `codex_pool`. Default provider is `None` and there is no agnostic inherit
  fallback (`fallback_on_inherit: None`).
- A local `mlx-vlm` provider (`provider="local"`) exists but is intentionally
  **not** advertised in `PROVIDERS`; users opt in explicitly.

- `codex`, `codex-pool`, and `codex_pool` all construct the native standalone
  Codex Responses vision service. Pool aliases select their initial auth path
  with `select_codex_pool_auth(defaults, model=<exact configured model>)` and
  never route through another provider's vision service.
- When the active main provider is Codex-family, its configured model and
  endpoint are forwarded to standalone vision. A non-Codex main provider does
  not supply those values to an explicitly configured Codex vision provider.
  Direct Codex uses the selected provider bucket's `codex_auth_path`.
- When the active main provider is the same direct-native provider, `openai`,
  `anthropic`, and `gemini` standalone vision use the active model unless the
  capability explicitly set `model`. `openai` and `anthropic` also use the
  active base URL unless the capability explicitly set `base_url`; Gemini has
  no base-url constructor and does not receive one here.
- MiMo is a deliberate exception: explicit capability `model` wins, but without
  one setup leaves the vision service on its known vision-capable default
  (`mimo-v2.5`) instead of forwarding text-only active MiMo chat models. When
  the active main provider is MiMo, setup may still forward the active base URL
  unless the capability explicitly set `base_url`.
- `glm` is a first-class vision alias for the existing Zhipu MCP vision service.
  It is not a new direct-native provider.

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
  `lingtai.tools → lingtai` import edge is only crossed inside setup/handlers, never at
  module import.
- Provider-specific kwargs are injected per branch (e.g. MiniMax `api_host`,
  Zhipu `z_ai_mode`) because vision services have heterogeneous constructor
  signatures. Dedicated services never receive `api_compat`. Direct-native
  `openai`, `anthropic`, `gemini`, and Codex-family services may receive the
  active model only from the same active provider family unless capability
  `model` was explicit. `openai`, `anthropic`, MiMo, and Codex-family services
  may receive `base_url` when it came from an explicit capability override or
  from the same active provider family; Gemini, MiniMax, and Zhipu/`glm` do not
  receive inherited `base_url` through the service constructor.
- `CodexVisionService` refreshes the selected OAuth token and account id for
  each image call. It sends `ChatGPT-Account-ID` only when a non-secret account
  id is available, and uses Responses `input_text` plus `input_image` blocks.
- Unknown providers route through the OpenAI- or Anthropic-compatible service
  based on `api_compat`; if neither matches, the capability logs
  `capability_skipped` and returns `CAPABILITY_UNAVAILABLE`.

There are no subprocess/shell/PTY/binary-spawn assumptions in this tool.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `setup` registers the `vision` tool with a provider or a service | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_added_by_setup`, `::test_vision_setup_with_provider_and_key`, `::test_vision_with_dedicated_service` |
| Setup raises when neither service nor provider is given | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_setup_requires_provider_or_service`, `::test_vision_setup_no_provider_raises` |
| Missing / relative `image_path` handling is correct | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_empty_image_path`, `::test_vision_missing_image`, `::test_vision_relative_path` |
| Empty model response is an error; service exceptions are caught | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_empty_response_is_error`, `::test_vision_service_error_handled` |
| Unsupported provider with an api_compat routes to the compat service | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_fallback_anthropic_compat_routes_to_anthropic_service`, `::test_vision_fallback_reads_api_compat_from_provider_bucket` |
| Unknown api_compat skips the capability with a diagnostic | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_fallback_unknown_api_compat_skips_with_diagnostic`, `::test_vision_setup_unsupported_provider_skips` |
| `api_key_env` overrides the raw key at setup | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_vision_setup_resolves_api_key_env` |
| Direct-native vision preserves same-provider model/endpoint identity without cross-provider fallback | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_direct_native_vision_inherits_same_provider_model_and_endpoint`, `::test_direct_native_vision_honors_explicit_model_and_endpoint_over_active_provider`, `::test_direct_native_vision_does_not_inherit_from_mismatched_provider` |
| MiMo preserves explicit/default vision-capable model behavior while preserving same-provider endpoint | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_mimo_vision_keeps_default_model_but_preserves_same_provider_endpoint`, `::test_mimo_vision_honors_explicit_model_and_endpoint` |
| MiniMax/Zhipu MCP vision do not forward active chat models; `glm` aliases Zhipu MCP | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_minimax_vision_does_not_forward_active_chat_model`, `::test_zhipu_vision_does_not_forward_active_chat_model_or_base_url`, `::test_glm_vision_alias_uses_zhipu_mcp_service` |
| Text-only/provider-relay aliases remain unavailable by default | `src/lingtai/tools/vision/__init__.py` | `tests/test_vision_capability.py::test_unsupported_text_providers_remain_unavailable_by_default` |
| Dedicated provider services parse valid/invalid responses correctly | `src/lingtai/services/vision/` | `tests/test_vision_services.py::test_mimo_vision_returns_content_on_valid_response`, `::test_openai_vision_returns_content_on_valid_response`, `::test_create_vision_service_unknown_provider` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Tool registers only with a usable service/provider | `tests/test_vision_capability.py::test_vision_added_by_setup` | Boot with `capabilities={"vision": {"provider": "gemini", "api_key": "..."}}` | Agent silently lacks vision, or crashes at setup |
| Image path resolution + existence check | `tests/test_vision_capability.py::test_vision_relative_path` | Call with a relative path under the agent dir | False "not found" or reads outside intent |
| Provider-resolution fallback (api_compat) | `tests/test_vision_capability.py::test_vision_fallback_reads_api_compat_from_provider_bucket` | Configure an OpenRouter-style relay, confirm routing | Unknown providers hard-fail instead of routing |
| Lazy import preserves the lingtai.tools → lingtai DAG rule | import-time absence of `lingtai.services` at module load | `grep` for top-level `lingtai.services` imports (none) | Import cycle / layering violation |
| Errors are structured, never raised to the agent loop | `tests/test_vision_capability.py::test_vision_service_error_handled` | Point at an unreachable endpoint, confirm `status: error` | Tool-call crash instead of a recoverable error |

Run before merging vision changes:

```bash
python -m pytest tests/test_vision_capability.py tests/test_vision_services.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
