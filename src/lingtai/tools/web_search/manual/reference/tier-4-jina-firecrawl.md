---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
maintenance: |
  Keep this external-service note aligned with the tier index. Vendor access,
  price, quota, API shape, and availability are not LingTai constants.
---
# Tier 4 — explicitly selected page-to-markdown service

Use only when an authorized external service is suitable for the public target
and local procedures are insufficient. Verify the current vendor docs, account,
quota, price, dependencies, and access policy first; no install, credential,
paid-use, or access-control authority is implied.

Jina Reader's illustrative public route is `GET https://r.jina.ai/<url>` and
usually returns Markdown. Firecrawl has its own current SDK/API contract. Do
not assume either is free, available, or a safe substitute for a blocked page.

This is one explicit recovery route, not a hidden `web` fallback chain. Keep
requests public and read-only; never impersonate a person, bypass login,
paywall, robots, CAPTCHA, or other access control. Preserve provenance and
label the external service in the result.
