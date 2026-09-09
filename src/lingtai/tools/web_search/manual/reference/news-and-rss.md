---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/social-media.md
  - src/lingtai/tools/web_search/manual/reference/realtime-data.md
  - src/lingtai/tools/web_search/manual/scripts/cached_get.py
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
maintenance: |
  Keep this public news/feed route aligned with the parent manual and endpoint
  asset. Do not duplicate social or real-time recipes; preserve public-access,
  paywall, rate-limit, and no-silent-chain boundaries.
---
# News and RSS

Use for a news snapshot, a public RSS/Atom feed, or an explicitly authorized
archive lookup. For ordinary discovery start with `web` search; this is a
separate procedure, not a hidden Web fallback.

## Direct routes

- Topic snapshot → Google News RSS (`news.google.com/rss/search?q=...`), then
  parse the XML and normalize the redirected article URL.
- A known feed → fetch and parse its XML; discover a feed from an HTML
  `link[rel=alternate][type*=rss|atom]` before probing common `/feed`/`/rss`
  paths. Inspect parser diagnostics (`bozo`) and content encoding.
- Reddit or Hacker News → [social media](social-media.md), which owns those
  APIs. Current structured endpoints are also listed in the endpoint asset.
- Archive availability → Wayback's public availability API; archival saves are
  asynchronous and are an external side effect requiring authorization.

Always send a descriptive User-Agent, respect current rate limits and
`Retry-After`, and do not use the broken `cached_get` helper as a cache; see
[bundle limitations](maintenance-bundles/SKILL.md#known-legacy-limitations). Keep source URL, publication time, and feed/parser warnings.

## Paywall boundary

Public RSS/metadata, an authorized archive, or text already present in public
HTML may be read. If the article body requires login, a subscription, leaked
cookie, CAPTCHA solution, or server-side paywall bypass, stop and report the
limitation. Do not simulate a paid identity or distribute a protected copy.
A public static page may use the operation contract's single explicitly
authorized HTTP retry; do not silently chain trafilatura, Jina, Wayback, and
browser services.

Encode RSS query parameters; keep Google News `hl`, `gl` and `ceid` consistent
with the requested locale. `bozo` can coexist with usable feed entries: report
warnings rather than discarding the whole feed blindly. An empty Wayback availability
result proves only that this lookup found no snapshot, not that none ever existed.
