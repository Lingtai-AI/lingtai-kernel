---
related_files:
  - src/lingtai/tools/web_search/manual/reference/search-strategies.md
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Tier 5 — AI-Native Search (Tavily / Exa)

> External legacy recipe, not a built-in `web` engine or installed-capability
> promise. Start with public `web(search/browse)`; select a separate fallback
> explicitly through [web-manual](../SKILL.md) only when needed. Check the
> selected vendor's current API, dependencies, account access and quotas before
> use. These examples grant no install, credential/config change, paid use or
> access-control bypass authority. No live vendor validation is claimed here.

> Part of the [web-manual](../SKILL.md) skill.
> See also: [search-strategies.md](./search-strategies.md) for comprehensive search strategy guidance.

**When it applies:** discover content rather than extract a known URL.
For the one maintained example set, read
[Search Strategies — Engine Reference](search-strategies.md#engine-reference):

| Need | Recipe / route |
|---|---|
| Search + optional AI answer + raw page content | `search_tavily`; `include_answer` and `include_raw_content` |
| Semantic, keyword or automatic matching; domain filters | `search_exa`; `contents.text` |
| No-key local-library example, including news/images/videos | `search_ddg` and the installed DDGS library's help |
| Google-style search | Serper example; vendor docs for Google Custom Search |
| Academic discovery | [Academic pipeline](academic-pipeline.md): OpenAlex / Semantic Scholar, DBLP for CS |
| News | [News and RSS](news-and-rss.md): RSS or the library's news search |

The strategy page owns selection order, query reformulation, extraction,
pagination and failure handling. Its Exa example returns `results`; retain the
raw response JSON too if you need provider metadata. Vendor availability,
latency, price and free tiers are not LingTai constants.
