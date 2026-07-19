---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Real-Time Data

> Part of the [web-browsing](../SKILL.md) skill.
> Financial data, weather, system status, Stack Exchange, Wikipedia.

Real-time data from free, no-key APIs. Every source below is a `requests.get` (or a
thin library) → JSON → flat dict; only the endpoints and per-source gotchas differ.

---

## Decision Tree

```
Real-time data need
  ├─ Finance / stock   → yfinance (§1)
  ├─ Weather / climate → Open-Meteo (§2)
  ├─ Tech Q&A          → Stack Exchange API (§3)
  ├─ Facts / encyclopedia → Wikipedia REST API (§4)
  ├─ Service up/down   → Statuspage.io JSON (§5)
  └─ News / social     → news-and-rss.md, social-media.md (§6)
```

---

## 1. Financial Data (yfinance)

Stocks, company info, history, news. Free, no key, ~1-2s. Crypto via `yf.Ticker("BTC-USD")`.
**Gotcha:** yfinance scrapes Yahoo Finance — may break if Yahoo changes page structure;
not all `.info` fields exist for all tickers, so always use `.get()` with defaults.

```python
import yfinance as yf

def stock_info(symbol):
    """Company + quote snapshot. symbol e.g. 'AAPL', 'TSLA', '^GSPC'."""
    info = yf.Ticker(symbol).info
    return {k: info.get(v) for k, v in {
        "name": "longName", "price": "currentPrice", "previous_close": "previousClose",
        "change_pct": "regularMarketChangePercent", "market_cap": "marketCap",
        "pe_ratio": "trailingPE", "52week_high": "fiftyTwoWeekHigh",
        "52week_low": "fiftyTwoWeekLow", "volume": "volume", "sector": "sector",
        "industry": "industry", "description": "longBusinessSummary",
    }.items()} | {"symbol": symbol}

def stock_history(symbol, period="1mo", interval="1d"):
    """OHLCV DataFrame. period=1d/5d/1mo/…/max; interval=1m/…/1d/1wk/1mo
    (1m data only for the last 7 days). `.to_dict("records")` for plain rows."""
    return yf.Ticker(symbol).history(period=period, interval=interval)
```

Also available on a `yf.Ticker`: `.news[:n]`, `.recommendations`, `.earnings_dates`
(each a list/DataFrame — map through `.to_dict("records")`). For a multi-stock
comparison, loop `stock_history(sym)` and compare `Close.iloc[0]` vs `Close.iloc[-1]`.

---

## 2. Weather (Open-Meteo)

Forecast, historical, climate. Free, no key, no registration, ~0.5s.

```python
import requests

def geocode(city):
    """City name → lat/lon via Open-Meteo geocoding."""
    res = requests.get("https://geocoding-api.open-meteo.com/v1/search",
                       params={"name": city, "count": 1, "language": "en",
                               "format": "json"}, timeout=10).json().get("results")
    if not res:
        return None
    r = res[0]
    return {"lat": r["latitude"], "lon": r["longitude"], "name": r["name"],
            "country": r.get("country"), "admin1": r.get("admin1")}

def get_weather(lat, lon, days=7):
    """Current weather + daily forecast. Pass geocode()'s lat/lon."""
    r = requests.get("https://api.open-meteo.com/v1/forecast",
                     params={"latitude": lat, "longitude": lon, "current_weather": True,
                             "daily": "temperature_2m_max,temperature_2m_min,"
                                      "precipitation_sum,windspeed_10m_max,weathercode",
                             "timezone": "auto", "forecast_days": days}, timeout=15)
    r.raise_for_status()
    d = r.json()
    return {"current": d.get("current_weather"), "daily": d.get("daily"),
            "timezone": d.get("timezone")}
```

Historical: `https://archive-api.open-meteo.com/v1/archive` with `start_date`/`end_date`
(`YYYY-MM-DD`, up to ~80 years back) and the same `daily=` fields.

---

## 3. Stack Exchange API

Technical Q&A. Free, no key (300/day; 10000/day with a free key), ~0.5s.
`site` = stackoverflow / serverfault / askubuntu / superuser / math / … (full list at
stackexchange.com/sites).

```python
import requests

def stackexchange_search(query, site="stackoverflow", limit=10, sort="relevance"):
    """Search titles. sort=relevance/votes/newest/activity."""
    r = requests.get("https://api.stackexchange.com/2.3/search",
                     params={"intitle": query, "site": site, "pagesize": limit,
                             "sort": sort, "order": "desc"}, timeout=15)
    r.raise_for_status()
    return [{"title": i.get("title"), "url": i.get("link"), "score": i.get("score"),
             "answers": i.get("answer_count"), "is_answered": i.get("is_answered"),
             "tags": i.get("tags", [])} for i in r.json().get("items", [])]

def stackexchange_answers(question_id, site="stackoverflow"):
    """Answers with HTML body (filter=withbody), highest-voted first."""
    r = requests.get(f"https://api.stackexchange.com/2.3/questions/{question_id}/answers",
                     params={"site": site, "order": "desc", "sort": "votes",
                             "filter": "withbody"}, timeout=15)
    return [{"body_html": i.get("body"), "score": i.get("score"),
             "is_accepted": i.get("is_accepted"),
             "author": i.get("owner", {}).get("display_name")}
            for i in r.json().get("items", [])]
```

`/2.3/search/advanced` adds `q` (full-text), `tagged`, and `answers=1` (has-answer) filters.

---

## 4. Wikipedia REST API

Facts, summaries, definitions. Free, no key, ~0.3s. Multi-language via `lang`
(`en`/`zh`/`ja`/`de`/`fr`/…). Always send a `User-Agent`.

```python
import requests
UA = {"User-Agent": "LingTai/3.0"}

def wikipedia_summary(title, lang="en"):
    """Summary REST endpoint: title, extract, thumbnail, coordinates. None on 404."""
    r = requests.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{title}",
                     headers=UA, timeout=15)
    return None if r.status_code == 404 else (r.raise_for_status() or r.json())

def wikipedia_search(query, lang="en", limit=10):
    """Full-text search via the MediaWiki Action API."""
    return requests.get(f"https://{lang}.wikipedia.org/w/api.php",
                        params={"action": "query", "list": "search", "srsearch": query,
                                "format": "json", "srlimit": limit},
                        timeout=15).json()["query"]["search"]
```

Full plain text: `/api/rest_v1/page/plain/{title}`. **Related articles:** the REST
`/page/related` endpoint was decommissioned by Wikimedia in Feb 2025 (Phabricator
T376297) — use the Action API with `srsearch=morelike:{title}` + `srnamespace=0` instead.

---

## 5. System Status Pages

Many SaaS use Statuspage.io with a standard JSON API. Probe both common URL patterns:

```python
import requests

def check_statuspage(domain):
    """Status indicator from a Statuspage.io page (e.g. domain='github')."""
    for url in (f"https://{domain}.statuspage.io/api/v2/status.json",
                f"https://status.{domain}/api/v2/status.json"):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                d = r.json()
                return {"page": d.get("page", {}).get("name"),
                        "status": d.get("status", {}).get("indicator"),
                        "description": d.get("status", {}).get("description")}
        except Exception:
            continue
    return {"status": "unknown", "error": "Could not find status page"}
```

Active incidents: same two URL patterns with `/api/v2/incidents.json` → `["incidents"]`.
If neither pattern resolves, the service isn't on Statuspage.io — fall back to scraping
its status page HTML.

---

## 6. News & Social Feeds

For real news/social work use the dedicated references — [news-and-rss.md](./news-and-rss.md)
(Google News RSS, RSS discovery, paywall handling, Wayback) and
[social-media.md](./social-media.md) (Reddit, Hacker News, Mastodon, GitHub). Quick
one-liners if you only need a snapshot: Google News RSS
`https://news.google.com/rss/search?q={query}&hl=en` → `feedparser.parse`; Hacker News top
`https://hacker-news.firebaseio.com/v0/topstories.json` → slice IDs → fetch each `item`;
GitHub trending `GET api.github.com/search/repositories?q=stars:>100 language:{lang} pushed:>{date}`.

---

## 7. Caching, Failure Modes, Dependencies

**Caching:** real-time data changes often but not every second. Reuse the bundle's
`cached_get` (`<skill-path>/scripts/cached_get.py`, `ttl` arg) rather than a
bespoke cache. Suggested TTL: stocks 300s, weather 3600s, Wikipedia 86400s.

| Failure | Cause | Fallback |
|---------|-------|----------|
| yfinance empty / KeyError | Yahoo changed structure; field missing for ticker | Retry once; `.get()` with defaults; verify symbol |
| Open-Meteo timeout | Network | Retry once, use cached data |
| Stack Exchange 429 | 300/day no-key limit hit | Register a free key (10K/day) |
| Wikipedia 404 | Wrong title | `wikipedia_search` to find the canonical title |
| Status page not found | Not Statuspage.io | Scrape the status page HTML |
| All sources fail | Outage | Return last cached result if available |

```bash
pip install yfinance requests feedparser beautifulsoup4
```

`yfinance` (finance), `requests` (all HTTP APIs), `feedparser` (news snapshot),
`beautifulsoup4` (status-page HTML fallback).
