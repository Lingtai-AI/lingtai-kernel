---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/stealth.md
maintenance: |
  Keep this public JS-rendering fallback aligned with the tier index and stealth
  reference. Verify the installed Playwright API; do not imply login or
  access-control bypass authority.
---
# Tier 3 — Playwright for public JS pages

Use only for an authorized public page that genuinely needs JavaScript after
static/API extraction fails. Verify the selected environment's installed
Playwright and stealth APIs; this reference does not install dependencies.

```python
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    text = page.inner_text("body")
    browser.close()
```

For Nature or Springer use `domcontentloaded`, prefer it over `networkidle` (long-lived
connections can hang). Keep a complete result when the caller needs one; do
not silently return a preview as if it were the page. Forms, login, uploads,
SSO, and human verification belong to
[agent-native-browser.md](agent-native-browser.md). Never solve CAPTCHA,
impersonate a user, or bypass a paywall/robots/access control.

For fingerprinting, rate limiting, and detection-specific hazards load
[stealth.md](stealth.md), which owns those details.

The retained helper checks stealth v2 `Stealth().use_sync(page)` then legacy
`stealth_sync(page)`; verify your installed version instead of assuming either.
That helper blocks visual resources and truncates its result; for screenshots
or complete-text validation use an explicitly suitable harness, not its preview.
