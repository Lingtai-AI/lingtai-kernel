---
name: web-manual-tier-quick-refs
description: >
  Compact recovery index for static-browse failures: choose one explicit PDF,
  API, extraction, browser, or external-search procedure.
version: 2.0.0
last_changed_at: "2026-09-09T10:53:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/operation-contract.md
  - src/lingtai/tools/web_search/manual/reference/tier-0-pdf.md
  - src/lingtai/tools/web_search/manual/reference/tier-1-apis.md
  - src/lingtai/tools/web_search/manual/reference/tier-1-5-trafilatura.md
  - src/lingtai/tools/web_search/manual/reference/tier-2-beautifulsoup.md
  - src/lingtai/tools/web_search/manual/reference/tier-3-playwright.md
  - src/lingtai/tools/web_search/manual/reference/tier-4-jina-firecrawl.md
  - src/lingtai/tools/web_search/manual/reference/tier-5-ai-search.md
maintenance: |
  Keep this index as routing only; each linked tier owns its own procedure.
  It is an external recovery catalog, not a second Web action or an automatic
  fallback chain. Preserve the public-access and authorization boundary.
---
# Recovery tier index

Open this after `web` browse returns typed unsupported content or `NO_TEXT_BLOCKS`.
Choose one route; do not silently escalate through several services.

| Need | Load | Boundary |
|---|---|---|
| Direct PDF or known paper identifier | [Tier 0](../tier-0-pdf.md) | Download only an authorized public URL; extract locally. |
| DOI, arXiv, PMID, or free structured API | [Tier 1](../tier-1-apis.md) | Verify the current vendor API and access policy. |
| Ordinary static article/blog/documentation | [Tier 1.5](../tier-1-5-trafilatura.md) | No JavaScript or login. |
| Tables, lists, metadata, known selectors | [Tier 2](../tier-2-beautifulsoup.md) | Requests + BeautifulSoup; no browser interaction. |
| JS-rendered public page | [Tier 3](../tier-3-playwright.md) | Verify installed API; use the documented Nature/Springer wait mode. |
| Authorized external page-to-markdown service | [Tier 4](../tier-4-jina-firecrawl.md) | Check current account, quota, price, and access policy first. |
| Discovery through an external search vendor | [Tier 5](../tier-5-ai-search.md) | Explicit selection only; not a built-in `web` engine. |

For forms, login, uploads, SPA interaction, or human verification use
[agent-native-browser](../agent-native-browser.md), not a tier or `web browse`.

Before executing a retained helper or trusting an asset, read its
[known limitations](../maintenance-bundles/SKILL.md#known-legacy-limitations).
