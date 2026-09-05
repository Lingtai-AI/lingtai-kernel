---
related_files:
  - src/lingtai/tools/web_search/manual/reference/stealth.md
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Tier 3 — Playwright Stealth

> External legacy recipe, not a built-in `web` engine or installed-capability
> promise. Start with public `web(search/browse)`; select a separate fallback
> explicitly through [web-manual](../SKILL.md) only when needed. Check the
> selected vendor's current API, dependencies, account access and quotas before
> use. These examples grant no install, credential/config change, paid use or
> access-control bypass authority. No live vendor validation is claimed here.

> Part of the [web-manual](../SKILL.md) skill.
> See also: [stealth.md](./stealth.md) for comprehensive anti-detection techniques.

**When it applies:** JS-rendered pages, login-gated content, sites blocking simple requests.
**Tools:** `playwright` + `playwright-stealth` (verify the selected environment).
**Speed:** ~3-5s per page.
**⚠️ CRITICAL:** For Nature / Springer, use `domcontentloaded`, NOT `networkidle` (it hangs forever).

```python
from playwright.sync_api import sync_playwright

# Select the API exposed by the installed playwright-stealth package
try:
    from playwright_stealth import Stealth
    _apply_stealth = lambda page: Stealth().use_sync(page)
except ImportError:
    from playwright_stealth import stealth_sync
    _apply_stealth = lambda page: stealth_sync(page)

def tier3(url, wait_time=3):
    """Playwright stealth — JS-rendered or protected pages."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        _apply_stealth(page)

        # Block images/styles/fonts for speed
        def block_resources(route):
            if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
                route.abort()
            else:
                route.continue_()
        page.route("**/*", block_resources)

        # CRITICAL: do NOT use networkidle (Nature / Springer hang forever)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_time * 1000)

        content = page.inner_text("body")
        html = page.content()
        title = page.title()
        final_url = page.url
        browser.close()

        return {
            "url": final_url,
            "method": "tier3-playwright-stealth",
            "title": title,
            "body_preview": content[:5000],
            "html_len": len(html),
        }
```

### Advanced Stealth Techniques

Read the corresponding owner sections in [stealth.md](stealth.md):
[manual overrides](stealth.md#manual-stealth-overrides),
[persistent sessions](stealth.md#session-management-cookie-persistence),
[rate limiting](stealth.md#rate-limiting), and
[User-Agent rotation](stealth.md#user-agent-rotation). They own the detailed
recipes; do not maintain a second set of browser-version strings here.
A simple delay is `base + random.random() * jitter`; the deep reference adds
per-domain tracking and exponential backoff. Persist storage only in the
explicitly authorized dedicated session directory.

**Use when:** Tier 1 / 1.5 / 2 fail, or the page genuinely requires JS rendering within authorized access.

**Going deeper:** fingerprinting/detection theory, additional manual overrides, proxy
strategies, session/cookie persistence, CAPTCHA handling, rate limiting, per-site
stealth notes, and `nodriver` all live in [stealth.md](./stealth.md) — read it when
`playwright-stealth` alone still gets detected.
