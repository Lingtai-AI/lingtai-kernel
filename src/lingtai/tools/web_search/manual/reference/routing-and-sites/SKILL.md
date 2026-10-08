---
name: web-manual-routing-and-sites
description: >
  Site-class and recovery routing for the external web procedures, including
  endpoint pointers and access limitations.
version: 2.0.0
last_changed_at: "2026-09-09T10:53:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/news-and-rss.md
  - src/lingtai/tools/web_search/manual/reference/social-media.md
  - src/lingtai/tools/web_search/manual/reference/realtime-data.md
  - src/lingtai/tools/web_search/manual/scripts/extract_page.py
maintenance: |
  Keep this as a compact site-routing aid. The executable `extract_page.py`
  owns auto-tier behavior; this file must not promise an automatic chain or
  duplicate its implementation. Keep endpoint and access caveats current.
---
# Site routing

Use this only after the [tier index](../tier-quick-refs/SKILL.md) identifies an
external recovery procedure. `web browse` itself remains static public HTTP(S)
GET and never runs this table automatically.

| Target | First explicit route | Important limit |
|---|---|---|
| PDF, DOI, arXiv ID | Tier 0 / [academic pipeline](../academic-pipeline.md) | Use a public or authorized OA copy; do not bypass a paywall. |
| Academic metadata or structured identifier | Tier 1 / [academic APIs](../tier-1-apis.md) | Confirm current API policy and key requirements. |
| Static article, blog, docs | Tier 1.5 / [Trafilatura](../tier-1-5-trafilatura.md) | It cannot execute JavaScript or authenticate. |
| Lists, tables, metadata, Reddit/GitHub HTML | Tier 2 / [BeautifulSoup](../tier-2-beautifulsoup.md) | Prefer the platform's public API where documented. |
| JS-rendered/protected public page | Tier 3 / [Playwright](../tier-3-playwright.md) | Nature/Springer use `domcontentloaded`, not `networkidle`. |
| Forms, login, SPA, upload, human proof | [Agent-native browser](../agent-native-browser.md) | Separate Chrome DevTools MCP procedure; never `web browse`. |
| News/RSS, social, finance, weather, facts | The matching domain reference below | Follow its public endpoint and rate/access rules. |

`extract_page.py::auto_tier()` is the source of truth for that retained helper's
selection. Its `--fallback` option is a standalone script feature and is not a
promise of automatic fallback in the public `web` action. For one documented
public HTTP retry, follow the operation contract's crawler User-Agent rule;
never impersonate a user, chain identities, solve CAPTCHA, or bypass login,
paywall, robots, or other access control.

Domain routes: [news/RSS](../news-and-rss.md), [social media](../social-media.md),
[real-time data](../realtime-data.md), and [academic pipeline](../academic-pipeline.md).

Before executing a retained helper or trusting an asset, read its
[known limitations](../maintenance-bundles/SKILL.md#known-legacy-limitations).
