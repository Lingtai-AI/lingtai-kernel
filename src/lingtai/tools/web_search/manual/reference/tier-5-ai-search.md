---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/search-strategies.md
  - src/lingtai/tools/web_search/manual/assets/search-providers.json
maintenance: |
  Keep this external-search route aligned with search strategies and provider
  asset data. It is never a built-in Web engine or implicit fallback.
---
# Tier 5 — explicit external discovery

Use when the task needs discovery rather than extraction of a known URL. The
maintained examples and query rules live in
[search-strategies.md](search-strategies.md); provider metadata is in
[`assets/search-providers.json`](../assets/search-providers.json).

| Need | Explicit owner |
|---|---|
| No-key illustrative search | DuckDuckGo library; inspect installed help. |
| Search plus answer/content | Tavily; verify current account and response fields. |
| Meaning-based search | Exa; verify current API and quota. |
| Google-style or independent index | Serper, Google Custom Search, Brave, or SearXNG, each selected explicitly. |

External vendors may need credentials, payment, installation, or have changed
limits. Confirm authorization and current policy before use. Never put keys in
prompts or reports, and never present these procedures as `web(action="search")`
provider guarantees.
