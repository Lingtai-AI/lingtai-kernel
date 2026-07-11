---
name: web_search-contract
tool: web_search
contract_version: 1
related_files:
  - src/lingtai/tools/web_search/__init__.py
  - src/lingtai/tools/web_search/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Web search capability contract

`web_search` performs a single web lookup via a `SearchService` and returns
formatted results. It is a thin tool over a provider service; provider selection
and fallback happen at setup time. The implementation lives in
`src/lingtai/tools/web_search/`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the web-search tool surface, its `SearchService` wiring, or
  the provider-resolution / graceful-fallback logic in `setup`.
- You are reviewing result formatting or the default/duckduckgo behavior.

**Do not use this for:**
- Image understanding: use `vision` (see `src/lingtai/tools/vision/CONTRACT.md`).
- Code navigation only: read `src/lingtai/tools/web_search/ANATOMY.md`.
- Adding a provider service implementation: that lives under
  `src/lingtai/services/websearch/`, imported lazily from here.

**Fast paths:** tool schema -> §Tool surface; provider list / default ->
§Scope; lazy-import DAG rule -> §Cross-platform invariants.

## Scope

- Canonical tool name: `web_search`.
- One tool, one call — no actions, no persistent state.
- Advertised providers (`PROVIDERS["providers"]`): `duckduckgo`, `minimax`,
  `zhipu`, `gemini`, `anthropic`, `openai`. Default provider is `duckduckgo`,
  and `fallback_on_inherit` is `duckduckgo`.
- If an inherited/resolved provider is unsupported, setup logs
  `capability_fallback` and falls back to `duckduckgo` (with no credentials) —
  it never raises.

**Non-goals:** the capability does not fetch or crawl page bodies, deduplicate,
or rank beyond what the underlying `SearchService` returns. It keeps no state.

## Tool surface

Schema requires `query`.

| Inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|
| `query` (required) | — | `{status: "ok", results: <formatted string>}` (each result rendered as `**title**\n<url>\n<snippet>`, joined by blank lines; `"No results found."` when empty) | `{status: "error", message}` — missing `query` (`Missing required parameter: query`), no configured service (`No SearchService configured. ...`), or `Web search failed: <exc>` |

## State & storage

None. `web_search` issues one query through the configured `SearchService` and
returns the formatted results inline. It writes no files and keeps no per-call
state under the agent working directory.

## Cross-platform invariants

DOCUMENT ONLY — do not change these assumptions and do not propose Windows work.

- The provider service factory is imported **lazily inside `setup`**
  (`from lingtai.services.websearch import create_search_service`), preserving
  the architectural DAG rule that the `lingtai.tools → lingtai` import edge is only
  crossed inside setup/handlers, never at module import.
- Provider-specific kwargs are injected per branch (MiniMax `api_host`, Zhipu
  `z_ai_mode`); `duckduckgo` is created with no credentials.
- Provider resolution is graceful: an unsupported provider degrades to the
  `duckduckgo` fallback rather than raising, so an agent always ends up with a
  working search service.

There are no subprocess/shell/PTY/binary-spawn assumptions in this tool.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `setup` registers the `web_search` tool | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_web_search_added_by_capability`, `::test_web_search_with_provider_kwarg` |
| The manager delegates the query to the `SearchService` | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_web_search_manager_uses_search_service`, `::test_web_search_with_dedicated_service` |
| Missing `query` is a structured error | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_web_search_missing_query` |
| Service exceptions are caught and returned as errors | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_web_search_service_exception` |
| `api_key_env` overrides the raw key at setup | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_web_search_setup_api_key_env_overrides_raw_key`, `::test_web_search_setup_resolves_api_key_env` |
| MiniMax `api_host` / Zhipu mode are injected; Gemini omits `api_host` | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_web_search_setup_passes_api_host_for_minimax`, `::test_web_search_setup_passes_zhipu_mode_without_api_host`, `::test_web_search_setup_omits_api_host_for_gemini` |
| An inherited env key registers a provider | `src/lingtai/tools/web_search/__init__.py` | `tests/test_web_search_capability.py::test_inherited_web_search_env_key_registers` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Tool registers with a service or resolvable provider | `tests/test_web_search_capability.py::test_web_search_added_by_capability` | Boot with `capabilities={"web_search": {}}` and inspect tools | Agent lacks search or crashes at setup |
| Unsupported providers fall back to duckduckgo (never raise) | fallback path in `tests/test_web_search_capability.py` | Configure an unknown provider, confirm `capability_fallback` log + duckduckgo | Setup crashes; agent loses search entirely |
| Query delegation + result formatting | `tests/test_web_search_capability.py::test_web_search_manager_uses_search_service` | Run a query, confirm `**title**` formatting | Malformed/empty results surfaced to the model |
| Errors are structured, never raised to the agent loop | `tests/test_web_search_capability.py::test_web_search_service_exception` | Point at an unreachable endpoint, confirm `status: error` | Tool-call crash instead of a recoverable error |
| Lazy import preserves the lingtai.tools → lingtai DAG rule | import-time absence of `lingtai.services` at module load | `grep` for top-level `lingtai.services` imports (none) | Import cycle / layering violation |

Run before merging web_search changes:

```bash
python -m pytest tests/test_web_search_capability.py -q
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
