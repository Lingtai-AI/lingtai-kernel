---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-3-playwright.md
  - src/lingtai/tools/web_search/manual/reference/agent-native-browser.md
maintenance: |
  Keep this bounded anti-bot safety note aligned with Tier 3 and the separate
  interactive-browser route. Preserve stop conditions; do not turn it into a
  bypass recipe or a duplicate browser implementation.
---
# Anti-bot and access boundaries

Open this for a public JS-page recovery that is being rate-limited or detected.
It does not change `web browse`, grant access, or authorize a proxy, login,
payment, or credential. Use the cheapest explicit route first and stop when a
site requires protected access.

- Use `domcontentloaded` for Nature/Springer; `networkidle` can hang.
- Pace requests per the site's current policy, honor `Retry-After`, and cache
  only where authorized. Do not rotate identities or User-Agents per request.
- A CAPTCHA or challenge is a stop signal: use an authorized API/RSS/different
  public source or ask the human. Never solve reCAPTCHA/hCaptcha, impersonate a
  user, bypass a paywall/login/robots directive, or use leaked credentials.
- Proxies and persistent cookies are sensitive external mechanisms. Use only an
  explicitly authorized dedicated profile/session; never the user's real profile
  or an unapproved residential proxy.

For ordinary public JS rendering load [Tier 3](tier-3-playwright.md). For forms,
SSO, uploads, or human verification load
[agent-native-browser](agent-native-browser.md). For a single documented public
HTTP retry, follow the operation contract; do not build a fallback chain.
