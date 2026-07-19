---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Stealth Browsing & Anti-Detection

> Part of the [web-browsing](../SKILL.md) skill, and the deep-dive for Tier 3's
> stealth story — see [tier-3-playwright.md](./tier-3-playwright.md) for the
> baseline `tier3()` function this reference extends.
> Browser fingerprinting, User-Agent rotation, proxy strategies, CAPTCHA handling.

---

## How Websites Detect Bots

Understanding detection methods helps you choose the right countermeasure.

| Vector | Signals |
|---|---|
| **Browser fingerprinting** | Canvas/WebGL hash, audio context, font enumeration, screen res/color depth, CPU cores/device memory, OS+browser consistency |
| **WebDriver detection** | `navigator.webdriver === true`, missing `window.chrome` in headless, CDP detection, `$cdc_` injection, missing plugins/mimeTypes |
| **TLS/JA3 fingerprinting** | Python `requests` has a distinctive TLS fingerprint; headless browsers may differ from real ones |
| **Behavioral analysis** | Perfect mouse movement, no scroll, instant clicks, regular request cadence |
| **IP/network level** | Datacenter IP detection, proxy headers, per-IP rate limiting, geo-inconsistency |

---

## playwright-stealth: Primary Defense

**When to use:** Default for Tier 3 (JS-rendered pages). Patches most detection vectors.
**Installed:** ✅ in LingTai environment (`playwright-stealth` 2.0.3)

> **NOTE:** `playwright-stealth` v2.0.3+ removed `stealth_sync()`. Use `Stealth().use_sync(page)` instead. All code blocks below use a compatibility shim (`_apply_stealth`) that works with both v1 and v2.

### Basic Usage

```python
from playwright.sync_api import sync_playwright

# playwright-stealth v2.0.3+ compatibility
try:
    from playwright_stealth import Stealth
    _apply_stealth = lambda page: Stealth().use_sync(page)
except ImportError:
    from playwright_stealth import stealth_sync
    _apply_stealth = lambda page: stealth_sync(page)

def stealth_fetch(url, wait_until="domcontentloaded", timeout=30000, wait_seconds=3):
    """Fetch a page with full stealth patches applied.

    _apply_stealth patches: navigator.webdriver, chrome object, permissions,
    plugins, languages, WebGL vendor, and more.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        _apply_stealth(page)  # Apply all stealth patches

        page.goto(url, wait_until=wait_until, timeout=timeout)
        page.wait_for_timeout(wait_seconds * 1000)

        content = page.inner_text("body")
        html = page.content()
        title = page.title()
        final_url = page.url

        browser.close()
        return {
            "url": final_url, "title": title,
            "body": content, "html": html,
        }
```

### ⚠️ Nature/Springer Gotcha

**Do NOT use `wait_until="networkidle"` for Nature or Springer pages.** They have long-running connections that cause timeouts. Use `"domcontentloaded"` instead.

---

## Manual Stealth Overrides

For sites that detect `playwright-stealth`, layer additional `page.add_init_script()`
overrides on top of it. Each targets one leak vector; combine as needed:

| Override | Patches |
|---|---|
| `navigator.webdriver` | `Object.defineProperty(navigator, 'webdriver', {get: () => undefined})` |
| `window.chrome` | `window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}}` |
| Permissions API | `navigator.permissions.query` returns real `Notification.permission` for `'notifications'` |
| Plugins | `Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]})` |
| Languages | `Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']})` |
| WebGL vendor/renderer | Patch `WebGLRenderingContext.prototype.getParameter` to return `'Intel Inc.'` / `'Intel Iris OpenGL Engine'` for params `37445`/`37446` |
| Hardware concurrency / device memory | `Object.defineProperty(navigator, 'hardwareConcurrency'/'deviceMemory', {get: () => 8})` |

```python
def create_stealth_page(browser):
    """Fully stealthed page: _apply_stealth() plus every override above."""
    page = browser.new_page()
    _apply_stealth(page)
    page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (p) =>
            p.name === 'notifications' ? Promise.resolve({state: Notification.permission}) : originalQuery(p);
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(param) {
            if (param === 37445) return 'Intel Inc.';
            if (param === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, param);
        };
        Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
        Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
    """)
    return page
```

---

## Resource Interception (Speed + Stealth)

Block unnecessary resources to load faster AND look less like a data-heavy bot:

```python
def block_resources(route):
    """Block images, CSS, fonts, media — only allow documents and scripts."""
    if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
        route.abort()
    else:
        route.continue_()

# Apply to page
page.route("**/*", block_resources)
```

**When to use:** Always unless you need images or visual rendering.
**Speed improvement:** ~2-5× faster page loads.

---

## User-Agent Rotation

```python
import random

USER_AGENTS = [  # Chrome/Firefox/Safari/Edge across Windows/Mac/Linux
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
]

def random_ua():
    return random.choice(USER_AGENTS)

r = requests.get(url, headers={"User-Agent": random_ua()})      # requests
page = browser.new_page(user_agent=random_ua())                  # Playwright
```

**When to rotate:** Every few requests to the same domain. Don't rotate per-request (suspicious).

---

## Proxy Strategies

```python
# HTTP/HTTPS — requests
proxies = {"http": "http://user:pass@proxy:8080", "https": "http://user:pass@proxy:8080"}
r = requests.get(url, proxies=proxies)

# HTTP/HTTPS — Playwright
context = browser.new_context(proxy={"server": "http://proxy:8080", "username": "user", "password": "pass"})

# SOCKS5 (requests needs `pip install requests[socks]`)
proxies = {"http": "socks5://user:pass@proxy:1080", "https": "socks5://user:pass@proxy:1080"}
context = browser.new_context(proxy={"server": "socks5://proxy:1080"})

# Pool rotation
from itertools import cycle
proxy_pool = cycle(["http://user:pass@proxy1:8080", "http://user:pass@proxy2:8080"])
def request_with_rotation(url):
    return requests.get(url, proxies={"http": (p := next(proxy_pool)), "https": p},
                       headers={"User-Agent": random_ua()})
```

**When to use proxies:** Getting IP-blocked, need geo-diversity, heavy scraping.
**When NOT to:** Simple one-off requests, free API queries.

---

## Session Management (Cookie Persistence)

For sites that require login or maintain session state, use a persistent browser
context so cookies/localStorage survive across runs:

```python
def persistent_session(url, user_data_dir="./browser_session", save_cookies=True):
    """Persistent context keeps cookies/localStorage in user_data_dir."""
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir, headless=True, user_agent=random_ua())
        page = context.new_page()
        _apply_stealth(page)
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
        content = page.inner_text("body")
        if save_cookies:
            context.storage_state(path=f"{user_data_dir}/storage_state.json")
        context.close()
        return content

def load_session(url, storage_state_path="./browser_session/storage_state.json"):
    """Resume a session saved by persistent_session()'s storage_state()."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_context(storage_state=storage_state_path).new_page()
        _apply_stealth(page)
        page.goto(url, wait_until="domcontentloaded")
        content = page.inner_text("body")
        browser.close()
        return content
```

**When to use:** Sites requiring login (LinkedIn, some forums), sites with CSRF tokens.

---

## CAPTCHA Decision Tree

```
CAPTCHA encountered ─────────────┐
                                 │
    ┌────────────────────────────┼────────────────────────────┐
    │                    │                    │
  reCAPTCHA/          Simple/              Cloudflare
  hCaptcha            Image CAPTCHA        Challenge
    │                    │                    │
    ▼                    ▼                    ▼
  STOP.              Try refreshing       Try nodriver
  Do NOT attempt     the page. If         (if installed).
  to solve.          persistent,          If still blocked,
  Switch to          lower request        STOP.
  another            frequency.           Switch to API
  data source.                            or different
  (API, RSS,                              data source.
  different site)
```

### Key Rules

1. **Never try to solve reCAPTCHA/hCaptcha programmatically** — it's unethical and usually futile
2. **Cloudflare challenge** → try `nodriver` library or switch to API
3. **Simple image CAPTCHA** → refresh the page, try again with different fingerprint
4. **Rate-triggered CAPTCHA** → slow down! You're going too fast

---

## Rate Limiting

```python
import time, random
from collections import defaultdict

class RateLimiter:
    """Per-domain rate limiter with jitter: limiter.wait("example.com") before each request."""
    def __init__(self, min_interval=2.0):
        self.last_request = defaultdict(float)
        self.min_interval = min_interval

    def wait(self, domain):
        elapsed = time.time() - self.last_request[domain]
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed + random.random())
        self.last_request[domain] = time.time()

rate_limiter = RateLimiter(min_interval=2.0)

def exponential_backoff(attempt, base=1.0, max_delay=60.0):
    """Use on 429/5xx: delay = min(base * 2**attempt + jitter, max_delay)."""
    delay = min(base * (2 ** attempt) + random.random(), max_delay)
    time.sleep(delay)
    return delay
```

### Site-Specific Notes

| Site | Rate limit | Stealth notes |
|------|----------|-------|
| Google Scholar | ~10 req/IP then CAPTCHA | Playwright stealth unreliable here — prefer SerpAPI/Serper; if scraping, residential proxy + 10s+ delays |
| Reddit | 60 req/min, needs User-Agent | Use JSON API (`.json` suffix), not HTML; `old.reddit.com` is simpler markup |
| GitHub | 60/hr (5000/hr with token) | Use a token for heavy use |
| Wikipedia | Reasonable use | Cache results, be nice |
| arXiv | No explicit limit | Be reasonable |
| Nature/Springer | Aggressive blocking | Prefer the API; avoid `networkidle` |
| Twitter/X | Very aggressive, requires login | Nitter instances if available; never bypass login |
| LinkedIn | Most aggressive of any major site | Prefer LinkedIn API; do not scrape profiles at scale |
| Medium | Client-side paywall | `trafilatura` → Jina Reader → give up; `?source=friends_link` sometimes helps |
| News sites | Client-side paywalls (JS) | RSS first → trafilatura → Jina Reader → Wayback Machine |

---

## nodriver: Maximum Stealth (Alternative to Playwright)

For sites that detect Playwright specifically, `nodriver` (formerly
undetected-chromedriver) patches Chrome at a lower level — slower startup,
harder to detect:

```python
# pip install nodriver
import nodriver as uc

async def nodriver_fetch(url):
    browser = await uc.start()
    page = await browser.get(url)
    await page.sleep(3)  # Wait for JS
    return await page.get_content()
```

**When to use:** Playwright stealth is detected (Cloudflare, some banking sites).
**When NOT to:** Standard scraping — Playwright is faster and more reliable.

---

## Complete Stealth Workflow

Escalate through the cheapest methods first, same fallback shape as the auto-tier
extractor: plain `requests` → `trafilatura` → Playwright stealth (`stealth_fetch`,
defined above) → Jina Reader. Each step is wrapped in `try/except` and only
advances on failure or a too-short result (see
[tier-4-jina-firecrawl.md](./tier-4-jina-firecrawl.md) for the Jina call and
[tier-1-5-trafilatura.md](./tier-1-5-trafilatura.md) for the trafilatura call).
Call `rate_limiter.wait(domain)` before the first attempt.

---

## Dependencies

```bash
# Core (already installed in LingTai)
pip install playwright playwright-stealth
playwright install chromium

# Optional
pip install nodriver          # Maximum stealth alternative
pip install requests[socks]   # SOCKS5 proxy support
```

---

This reference is part of the [web-browsing](../SKILL.md) manual. For the main
extraction pipeline, see the parent skill; for academic-specific browsing, see
[academic-pipeline.md](./academic-pipeline.md).
