---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/news-and-rss.md
  - src/lingtai/tools/web_search/manual/reference/social-media.md
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
maintenance: |
  Keep this compact endpoint route aligned with the bundled data and parent
  manual. Current quotas, schemas, prices, and availability belong to each
  vendor; do not preserve stale numbers as guarantees.
---
# Real-time and structured public data

Use the matching public endpoint when a question needs current facts rather
than a prose page. Verify response status, timestamp/timezone, content type,
current vendor policy, and rate headers before relying on a value.

| Need | Route |
|---|---|
| Stocks/market history | yfinance or the vendor's current public API; treat fields as optional. |
| Weather/forecast | Open-Meteo geocoding then forecast endpoint. |
| Technical Q&A | Stack Exchange API with an explicit site. |
| Encyclopedia/facts | Wikipedia REST/MediaWiki API; search canonical titles first. |
| Service status | Statuspage JSON when the service publishes it; otherwise report unavailable. |
| News/social | [news/RSS](news-and-rss.md) or [social media](social-media.md). |

Endpoint examples and patterns live in
[`assets/api-endpoints.json`](../assets/api-endpoints.json). Keep API keys in
the authorized secret mechanism, never in URLs, prompts, logs, or reports.
Respect public terms, rate limits, and `Retry-After`; do not automatically
substitute stale cached data for a current answer without labeling it. A 404
or missing field is evidence to report or resolve, not permission to invent a
value. Wikipedia's retired `/page/related` endpoint must not be used.

For related Wikipedia pages, check current MediaWiki Action API search support
for `morelike:<title>` instead of reviving the retired REST endpoint. Endpoint
assets are historical examples, not a fresh vendor verification.
