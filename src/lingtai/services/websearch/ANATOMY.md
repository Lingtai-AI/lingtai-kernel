---
related_files:
  - src/lingtai/tools/web_search/ANATOMY.md
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/services/websearch/__init__.py
  - src/lingtai/services/websearch/anthropic.py
  - src/lingtai/services/websearch/duckduckgo.py
  - src/lingtai/services/websearch/openai.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# src/lingtai/services/websearch/

Provider-specific web search — standalone services behind a common `SearchService` ABC.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|------|-----|------|
| `__init__.py` | 126 | `SearchResult` dataclass, `SearchService` ABC, `SearchProviderError(provider, failure_class)`, `create_search_service()` factory |
| `anthropic.py` | 62 | `AnthropicSearchService` — Claude `web_search_20250305` native tool |
| `duckduckgo.py` | 25 | `DuckDuckGoSearchService` — `ddgs` package, zero API key |
| `openai.py` | ~65 | `OpenAISearchService` — canonical Responses API `tools:[{"type":"web_search"}]` |

`minimax.py` and `zhipu.py` were deleted 2026-07-28 (Jason authorized the exact
two-path deletion, issue 11114). Neither provider is a built-in `web` search
engine anymore; wire either through a third-party MCP server instead — see
`src/lingtai/tools/mcp/skills/mcp-manual/reference/third-party-and-legacy.md`. MiniMax/Zhipu
web search is no longer implemented anywhere in this repository. `gemini.py`
(`GeminiSearchService`, Gemini `GoogleSearch` grounding) was deleted with the
Gemini LLM provider when LingTai collapsed to four LLM provider families
(`openai`, `anthropic`, `codex`, `claude-code`); the `google-genai` SDK is no
longer used here.

## Connections

- **ABC contract** — all providers inherit `SearchService` (`__init__.py:51`); single abstract method `search(query, max_results) -> list[SearchResult]`.
- **Factory** — `create_search_service(provider, api_key=...)` dispatches by name. Supported: `duckduckgo`, `anthropic`, `openai`. An unrecognized name — including the retired `minimax`/`zhipu` and the removed `gemini` — raises `ValueError: Unknown web search provider`. `anthropic` named through a forbidden direct-composition route inside the `web` capability raises the distinct `SettingsOnlyProviderError` one layer up, at `src/lingtai/tools/web_search/__init__.py`, and an unrecognized composed name takes the `web` capability's DuckDuckGo legacy fallback — both before ever reaching this factory; that policy is `web`'s own admission concern, not this package's.
- **External SDKs** — `anthropic`, `openai`, `ddgs`.
- **Logging** — all providers except DuckDuckGo use `lingtai.kernel.logging.get_logger`.
- **Backend eligibility** — `openai.py`/`anthropic.py` construction is gated, one layer up, by the module-private `_same_provider_identity()` predicate in `src/lingtai/tools/web_search/__init__.py` (each engine only for an Agent whose own LLM provider is that family); this package has no knowledge of the Agent's own LLM backend identity.

## Composition

- **LLM-grounded providers** (anthropic, openai) — create a fresh SDK client inside each `search()` call, send a one-shot prompt with a search tool enabled, extract each provider's own official per-source citation URLs from the structured response (`_extract_results()` in each module). Fully stateless.
- **DuckDuckGo** — simplest provider; direct `DDGS().text()` call, no client state.
- **Result normalization** — LLM-grounded providers prefer real per-source
  citation URLs: OpenAI's Responses `output[]` message
  `annotations[].url_citation` (`openai.py:_extract_results`); Anthropic's
  text-block `citations[]` of `web_search_result_location`, falling back to
  raw `web_search_tool_result` content items (`anthropic.py:_extract_results`).
  When no structured citation is
  present but the provider still returned a nonempty narrative, each falls
  back to exactly one bounded `SearchResult(url="")` rather than discarding
  real output.

## State

- **LLM-grounded** — stateless per-call. Fresh client created each invocation.
- **DuckDuckGo** — stateless per-call.

## Notes

- **Provider search mechanisms** — Anthropic: `web_search_20250305` tool type (`anthropic.py:47`); OpenAI: canonical Responses API `tools:[{"type":"web_search"}]` (`openai.py`).
- **Default models** — Anthropic: `claude-sonnet-4-20250514`; OpenAI: `gpt-5.6` (`OpenAISearchService.DEFAULT_MODEL`; see the module docstring for the exact evidence-count citation this default follows — the fetched official doc's own worked code samples, not its comparison-table prose cell, which names a different model once).
- **Provider error hierarchy** — `SearchProviderError(provider, failure_class)` (`__init__.py`) is the shared, narrow base both canonical adapters raise on a runtime failure — bounded to a provider name and failure class, never raw SDK exception text, request bodies, or credentials. `OpenAISearchError`, `AnthropicSearchError` are provider-specific subclasses (`openai.py`, `anthropic.py`); only `OpenAISearchError` triggers the `web` capability's DuckDuckGo fallback (`src/lingtai/tools/web_search/__init__.py` `_search`/`_openai_duckduckgo_fallback` checks the exact subclass, not the shared base). `AnthropicSearchService` additionally detects Anthropic's official HTTP-200 in-body `web_search_tool_result_error` (`anthropic.py:_extract_results`) and raises `AnthropicSearchError` carrying only the bounded `error_code`.
- **Error handling** — DuckDuckGo catches exceptions in `search()` and returns `[]` on failure with a `logger.warning` (a bounded exception-class message, never raw exception text). Anthropic and OpenAI instead each raise their own `SearchProviderError` subclass on any SDK/HTTP error or (Anthropic only) an official in-body tool error — `[]` from these two is reserved for a genuine successful response with no content/result, never a swallowed failure.
- **Git history** — 6 commits before the 2026-07-28 MiniMax/Zhipu retirement. Key: zhipu provider addition (`3100311`), HTTPMCPClient switch (`092bff6`), region-aware mode (`bed1c1e`), canonical-provider-routing repair deleting minimax.py/zhipu.py (2026-07-28, issue 11114).
