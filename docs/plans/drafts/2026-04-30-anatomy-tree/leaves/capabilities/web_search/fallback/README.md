# web_search/fallback

## What

The web_search capability provides web lookup via a `SearchService` backend.
When the agent's LLM provider doesn't support web search, the capability
falls back to DuckDuckGo — a zero-API-key provider that works without
credentials. This fallback is automatic and silent.

## Contract

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| `query` | string | **yes** | — | Search query. |

**Return:** `{status: "ok", results: "<formatted string>"}` where results are
markdown-formatted: `**Title**\nURL\nsnippet` joined by `\n\n`.

**Errors:**
- Missing query → `{"status": "error", "message": "Missing required parameter: query"}`
- No service configured → `{"status": "error", "message": "No SearchService configured..."}`
- Exception → `{"status": "error", "message": "Web search failed: <error>"}`

### Provider list and fallback

| Provider | Auth | Default? | Fallback target |
|----------|------|----------|-----------------|
| `duckduckgo` | none | **yes** | — (is the fallback) |
| `minimax` | api_key | no | — |
| `zhipu` | api_key | no | — |
| `gemini` | api_key | no | — |
| `anthropic` | api_key | no | — |
| `openai` | api_key | no | — |

**Fallback logic (`setup()`):**

1. If `search_service` provided directly → use it.
2. If `provider` is given AND in `PROVIDERS["providers"]` → create service.
3. If `provider` is given BUT NOT in list → log `capability_fallback` event,
   set `provider = "duckduckgo"`, clear `api_key`, create DDG service.
4. If neither `search_service` nor `provider` given → create DDG service.
5. DDG service requires no credentials (`create_search_service("duckduckgo")`).

**Key properties:**
- `PROVIDERS["default"]` = `"duckduckgo"` — the default when no provider specified.
- `PROVIDERS["fallback_on_inherit"]` = `"duckduckgo"` — the fallback when an
  unsupported provider is inherited from the agent's LLM config.
- DDG service is imported lazily from `services/websearch/duckduckgo.py`.
- Other providers inject extra kwargs: minimax→`api_host`, zhipu→`z_ai_mode`.

### Result format

`SearchResult` dataclass: `title: str`, `url: str`, `snippet: str`.
Formatted by the capability handler as markdown:
```
**<title>**
<url>
<snippet>
```

## Source

- Capability: `capabilities/web_search/__init__.py:41` — `WebSearchManager`
- Handler: `capabilities/web_search/__init__.py:52` — `handle()`
- Setup with fallback: `capabilities/web_search/__init__.py:77` — `setup()`
- Fallback logic: `capabilities/web_search/__init__.py:94-105` — provider mismatch handling
- Default creation: `capabilities/web_search/__init__.py:119-120` — no provider specified
- Service factory: `services/websearch/__init__.py:48` — `create_search_service()`
- Result dataclass: `services/websearch/__init__.py:19` — `SearchResult`
- Provider list: `capabilities/web_search/__init__.py:20` — `PROVIDERS`

## Related

- **web-browsing skill** — comprehensive URL fetching, scraping, and extraction.
- **web_search tool** — the runtime tool the agent calls (this leaf documents its backend).
- **services/websearch/duckduckgo.py** — the zero-config fallback provider.
