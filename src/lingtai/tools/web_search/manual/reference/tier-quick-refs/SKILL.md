---
name: web-manual-tier-quick-refs
description: >
  Nested web-manual reference: compact tier index (Tier 0 PDF, Tier 1 APIs,
  Tier 1.5 Trafilatura, Tier 2 BeautifulSoup, Tier 3 Playwright stealth, Tier 4
  Jina/Firecrawl, Tier 5 AI-native search) routing to each tier's deep-dive
  file, which is the single source of truth for its commands.
version: 1.1.0
last_changed_at: "2026-09-04T00:00:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: "If you find stale or incorrect information here, use the lingtai-issue-report skill to assemble evidence and obtain per-issue human consent before filing an issue. Never include secrets, credentials, tokens, or private paths."
---

# Web Browsing Tier Quick References

Nested web-manual reference. Open this after the top-level router to find
which tier's deep-dive file to load next — the runnable commands live in
those files (single source of truth; a copy here would drift from them, see
`maintenance-bundles/SKILL.md` Rule 3), not in this index.

| Tier | What it's for | Load |
|---|---|---|
| 0 | PDF direct download + `fitz` text extraction | [reference/tier-0-pdf.md](../tier-0-pdf.md) |
| 1 | Academic/metadata API queries (OpenAlex, CrossRef, Semantic Scholar, arXiv, Unpaywall, PubMed, CORE, Europe PMC, DBLP, Papers With Code, DOAJ, Zenodo, NASA ADS) | [reference/tier-1-apis.md](../tier-1-apis.md); full pipeline: [reference/academic-pipeline.md](../academic-pipeline.md) |
| 1.5 | Trafilatura fetch+extract, metadata, batch mode | [reference/tier-1-5-trafilatura.md](../tier-1-5-trafilatura.md) |
| 2 | BeautifulSoup CSS-selector extraction | [reference/tier-2-beautifulsoup.md](../tier-2-beautifulsoup.md) |
| 3 | Playwright stealth (JS-rendered/protected pages) | [reference/tier-3-playwright.md](../tier-3-playwright.md); anti-detection deep-dive: [reference/stealth.md](../stealth.md) |
| 4 | Jina Reader / Firecrawl — verify current vendor access and quota policy | [reference/tier-4-jina-firecrawl.md](../tier-4-jina-firecrawl.md) |
| 5 | AI-native search (DuckDuckGo, Tavily, Exa) | [reference/tier-5-ai-search.md](../tier-5-ai-search.md); strategy guide: [reference/search-strategies.md](../search-strategies.md) |

---
