---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Tier 3 — Playwright Stealth

> Part of the [web-browsing](../SKILL.md) skill.
> See also: [stealth.md](./stealth.md) for comprehensive anti-detection techniques.

**When it applies:** JS-rendered pages, login-gated content, sites blocking simple requests.
**Tools:** `playwright` + `playwright-stealth` (**already installed**).
**Speed:** ~3-5s per page.
**⚠️ CRITICAL:** For Nature / Springer, use `domcontentloaded`, NOT `networkidle` (it hangs forever).

```python
from playwright.sync_api import sync_playwright

# playwright-stealth v2.0.3+ compatibility
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
        browser.close()

        return {
            "url": page.url,
            "method": "tier3-playwright-stealth",
            "title": title,
            "body_preview": content[:5000],
            "html_len": len(html),
        }
```

### Advanced Stealth Techniques

**Custom init scripts** (override fingerprinting):
```python
# Override navigator.webdriver
page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

# Override chrome runtime
page.add_init_script("""
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
""")

# Override languages
page.add_init_script("""
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
""")
```

**Persistent sessions** (for login-gated sites):
```python
context = p.chromium.launch_persistent_context(
    user_data_dir="./browser_data",
    headless=True,
)
# ... work ...
context.storage_state(path="cookies.json")  # Save session
```

**Smart rate limiting:**
```python
import time, random
def smart_delay(base=2.0, jitter=1.0):
    """Random delay to avoid pattern detection."""
    time.sleep(base + random.random() * jitter)
```

**User-Agent rotation:**
```python
import random
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/17.2",
]
```

**Use when:** Tier 1 / 1.5 / 2 fail, or the page genuinely requires JS rendering or bypasses anti-bot.

**Going deeper:** fingerprinting/detection theory, additional manual overrides, proxy
strategies, session/cookie persistence, CAPTCHA handling, rate limiting, per-site
stealth notes, and `nodriver` all live in [stealth.md](./stealth.md) — read it when
`playwright-stealth` alone still gets detected.
