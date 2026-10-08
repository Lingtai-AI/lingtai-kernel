---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-5-ai-search.md
  - src/lingtai/tools/web_search/manual/reference/academic-pipeline.md
  - src/lingtai/tools/web_search/manual/reference/news-and-rss.md
  - src/lingtai/tools/web_search/manual/assets/search-providers.json
maintenance: |
  Keep this external search procedure aligned with the parent route, tier-5
  reference, and provider asset. Vendor APIs, quotas, prices, and dependencies
  must be checked at use time; avoid maintaining duplicate endpoint recipes.
---
# External search strategies

This is a legacy/external procedure, not an installed `web` engine promise.
Start with `web(action="search")`; use this only when an explicitly authorized
separate vendor or domain route is needed. It grants no installation,
credential/configuration change, paid use, or access-control bypass authority.

## Choose a route

- Academic identifier or paper → [academic pipeline](academic-pipeline.md).
- News, RSS, or social → [news/RSS](news-and-rss.md) or
  [social media](social-media.md).
- General public discovery → `web` search first; an external vendor may be
  selected explicitly after checking current access, key, quota, and policy.
- Semantic search → Exa; answer-plus-content → Tavily; no-key illustration →
  DuckDuckGo. These names do not alter Web's admitted provider set.

Use precise terms, quoted phrases, `site:`, `filetype:`, date bounds, or
exclusions when the selected service supports them. Reformulate once when
results are empty; preserve source URLs and distinguish provider output from
inferred claims. Fetch a chosen result through `web browse` when it is a public
static URL, retaining the same-Agent `link_ref` where available.

Before using an external vendor, read its current documentation/account policy
and inspect installed help rather than remembered quotas or API fields. Never
leak API keys into calls, logs, prompts, or reports. There is no automatic
search→crawler→browser fallback here; choose one next owner explicitly.
