---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# News and RSS Feeds

> Part of the [web-browsing](../SKILL.md) skill.
> Google News RSS, Reddit JSON news feeds, RSS feed discovery and parsing, paywall boundary handling, news archival via Wayback Machine.

本技能专注于新闻类内容的获取、RSS/Atom 订阅源的发现与解析、付费墙边界处理与新闻归档。
当你需要获取新闻、监控信息流或解析 feed 时使用。

---

## 1. 新闻获取决策树

```
新闻需求
  ├─ 特定主题新闻?        → Google News RSS（§2）
  ├─ 社区讨论与热点?      → Reddit / Hacker News（见 social-media.md）
  ├─ 持续监控某网站?      → RSS Feed 发现 + feedparser（§3）
  ├─ 有 URL 但遇付费墙?   → trafilatura → Jina Reader → Wayback（§4）
  └─ 需归档或查历史?      → Wayback Machine API（§5）
```

| 需求 | 推荐方法 | 耗时 | 精度 |
|------|----------|------|------|
| 搜某话题的最新新闻 | Google News RSS | <2s | 高 |
| 了解社区在讨论什么 | Reddit/HN（[social-media.md](./social-media.md)） | <3s | 中 |
| 持续监控某网站 | RSS 发现 + 解析 | 首次<5s | 高 |
| 读到一半被付费墙挡住 | 付费墙绕行链 | 3-10s | 不确定 |
| 确保新闻链接不过期 | Wayback Machine 归档 | 5-60s | 高 |

社区新闻（Reddit `r/worldnews`/`r/news`/`r/technology`、Hacker News）的完整 API
用法见 [social-media.md](./social-media.md)——新闻场景直接复用其 `reddit_subreddit` /
`hn_stories`，本文不重复 Reddit 代码。

---

## 2. Google News RSS

免费公开接口，无需 API Key、无速率限制（仍应合理使用）。返回 RSS XML 用 `feedparser`
解析；标题字段常含 `<source>` 等 HTML 需 BeautifulSoup 清理；返回的链接是 Google
重定向 URL，最终跳转到原文。

```python
import requests, feedparser
from bs4 import BeautifulSoup

def google_news_rss(query, hl="en", gl="US", ceid="US:en", limit=20):
    """Search Google News RSS. hl=语言, gl=国家, ceid={国家}:{语言}."""
    r = requests.get("https://news.google.com/rss/search",
                     params={"q": query, "hl": hl, "gl": gl, "ceid": ceid},
                     headers={"User-Agent": "Mozilla/5.0 (compatible; LingTai/1.0)"},
                     timeout=15)
    r.raise_for_status()
    out = []
    for e in feedparser.parse(r.text).entries[:limit]:
        src = e.get("source")
        out.append({"title": BeautifulSoup(e.title, "lxml").get_text(),
                    "url": e.link, "published": e.get("published"),
                    "source": src.get("title") if isinstance(src, dict) else src})
    return out
```

**参数**：`q` 支持引号精确匹配；常用组合 `hl=en gl=US ceid=US:en`、
`hl=zh-CN gl=CN ceid=CN:zh-Hans`、`hl=ja gl=JP ceid=JP:ja`。
**查询修饰符**：`when:1d`/`when:7d` 限时间范围；`-source:foxnews.com` 排除来源。
主题 feed 的 topic token 不稳定，始终推荐用 `search` 方式。

---

## 3. RSS Feed 发现与解析

持续监控某新闻站时 RSS 最稳定。发现策略：①页面 `<link rel="alternate"
type="application/rss+xml|atom+xml">` 标签；②探测常见路径
（`/feed`, `/rss`, `/feed.xml`, `/rss.xml`, `/index.xml`, `/atom.xml`, `/feeds/all.xml`…）。

```python
import requests, feedparser
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

def discover_rss(url):
    """Find RSS/Atom feed URLs: <link> tags first, then probe common paths."""
    headers = {"User-Agent": "Mozilla/5.0 (compatible; LingTai/1.0)"}
    soup = BeautifulSoup(requests.get(url, headers=headers, timeout=15).text, "lxml")
    feeds = []
    for link in soup.find_all("link", rel=True):
        t = link.get("type", "")
        if "alternate" in link.get("rel", []) and ("rss" in t or "atom" in t) and link.get("href"):
            feeds.append({"url": urljoin(url, link["href"]), "type": t,
                          "label": link.get("title", "")})
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    for path in ("/feed", "/rss", "/feed.xml", "/rss.xml", "/index.xml",
                 "/atom.xml", "/feeds/all.xml", "/news/rss"):
        try:
            pr = requests.head(base + path, headers=headers, timeout=5, allow_redirects=True)
            if pr.status_code == 200 and "xml" in pr.headers.get("Content-Type", ""):
                feeds.append({"url": base + path, "type": "probed", "label": path})
        except requests.RequestException:
            continue
    seen, out = set(), []
    for f in feeds:
        if f["url"] not in seen:
            seen.add(f["url"]); out.append(f)
    return out

def parse_rss(feed_url, max_entries=20):
    """Parse RSS/Atom into {title, entries[]}. Uses feedparser's bozo for diagnostics."""
    feed = feedparser.parse(feed_url)
    if not feed.entries and feed.bozo and feed.bozo_exception:
        return {"title": None, "entries": [], "error": str(feed.bozo_exception)}
    out = []
    for e in feed.entries[:max_entries]:
        content = (e.content[0].get("value") if e.get("content")
                   else e.get("summary") or e.get("description"))
        if content:
            content = BeautifulSoup(content, "lxml").get_text()
        out.append({"title": e.get("title"), "url": e.get("link"),
                    "published": e.get("published"), "updated": e.get("updated"),
                    "summary": content[:1000] if content else None,
                    "author": e.get("author"),
                    "tags": [t.get("term") for t in e.get("tags", [])]})
    return {"title": feed.feed.get("title"), "link": feed.feed.get("link"), "entries": out}
```

**诊断**：`feed.bozo` 为真表示解析有警告（编码/日期问题），但 feedparser 通常仍能提取；
查 `feed.bozo_exception`、`len(feed.entries)`、`feed.entries[0].keys()`。
已知 feed 直接 `parse_rss("https://feeds.bbci.co.uk/news/world/rss.xml")` 即可；
批量监控就对 feed 列表逐个 `parse_rss`。

---

## 4. 付费墙绕行边界

**判断原则：内容需登录才能看到，就应停止尝试。** 客户端付费墙（正文在 HTML 中但被 JS
隐藏）是技术策略，可尝试提取；服务器端/硬付费墙（正文不在 HTML 中，或必须登录）是明确
付费意图，不得绕过。

| 能做 ✅ | 不能做 ❌ |
|---------|----------|
| RSS 公开摘要、Google 缓存、Wayback 归档 | 破解登录、绕过服务器端付费墙 |
| trafilatura（对客户端付费墙有效）、Jina Reader | 盗用付费订阅、模拟已付费身份 |
| 提取 meta / OG 摘要 | 破解 CAPTCHA、分发付费内容副本 |

| 付费墙类型 | 特征 | trafilatura | Jina | Wayback |
|------|------|:-----------:|:----:|:-------:|
| 客户端 (JS overlay) | 正文在 HTML，被 JS 遮盖 | ✅ | ✅ | ✅ |
| 计量 (metered) | 允许 N 篇/月 | ✅ | ✅ | ✅ |
| 服务器端 | HTML 中无正文 | ❌ | ❌ | ✅ |
| 硬付费墙 | 必须登录 | ❌ | ❌ | ❌ |

按尊重程度逐级降级，每层失败优雅继续，最终仅返回 meta 摘要或 `None`（绝不绕过硬墙）：

```python
import requests, trafilatura
from bs4 import BeautifulSoup

def extract_with_graceful_degradation(url):
    """full text → archive → meta summary → None. Returns a dict with 'level'."""
    # Level 0: trafilatura (client-side paywalls: content in HTML, JS-hidden)
    try:
        html = trafilatura.fetch_url(url)
        if html and (text := trafilatura.extract(html)) and len(text) > 200:
            return {"level": "full", "method": "trafilatura", "content": text}
    except Exception:
        pass
    # Level 1: Jina Reader (clean markdown, often bypasses simple paywalls)
    try:
        r = requests.get(f"https://r.jina.ai/{url}", timeout=30)
        if r.status_code == 200 and len(r.text) > 200:
            return {"level": "full", "method": "jina", "content": r.text}
    except Exception:
        pass
    # Level 2: Wayback Machine (archived version — most respectful)
    try:
        wb = requests.get(f"https://archive.org/wayback/available?url={url}", timeout=15)
        closest = wb.json().get("archived_snapshots", {}).get("closest")
        if closest and closest.get("available"):
            ar = requests.get(closest["url"], timeout=15)
            if (text := trafilatura.extract(ar.text)) and len(text) > 200:
                return {"level": "archived", "method": "wayback",
                        "timestamp": closest["timestamp"], "content": text}
    except Exception:
        pass
    # Level 3: meta / OG summary only — respect the paywall
    try:
        soup = BeautifulSoup(requests.get(url, timeout=15,
                             headers={"User-Agent": "Mozilla/5.0"}).text, "lxml")
        og = soup.find("meta", property="og:description") or \
             soup.find("meta", attrs={"name": "description"})
        if og and og.get("content"):
            title = soup.find("title")
            return {"level": "summary", "title": title.get_text() if title else None,
                    "content": og["content"],
                    "note": "Full content behind paywall — only summary available."}
    except Exception:
        pass
    return {"level": "unavailable", "url": url}
```

---

## 5. 新闻归档（Wayback Machine）

免费网页归档。保存新闻链接防失效或内容被改。

```python
import requests, time

def wayback_check(url):
    """Closest archived snapshot, or {'available': False}."""
    data = requests.get("https://archive.org/wayback/available",
                        params={"url": url}, timeout=15).json()
    c = data.get("archived_snapshots", {}).get("closest")
    return ({"available": True, "url": c["url"], "timestamp": c["timestamp"],
             "status": c.get("status")} if c and c.get("available")
            else {"available": False})

def wayback_all_snapshots(url):
    """All snapshots via the CDX API (timestamp, status, url)."""
    r = requests.get("https://web.archive.org/cdx/search/cdx",
                     params={"url": url, "output": "json",
                             "fl": "timestamp,statuscode,original", "limit": 50}, timeout=30)
    r.raise_for_status()
    rows = r.json()
    return [{"timestamp": t, "status": s,
             "url": f"https://web.archive.org/web/{t}/{o}"} for t, s, o in rows[1:]]

def wayback_save(url):
    """Request archival (async — may take minutes). GET /save/{url}."""
    try:
        r = requests.get(f"https://web.archive.org/save/{url}", timeout=60,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; LingTai/1.0)"})
        if r.status_code == 200:
            loc = r.headers.get("Content-Location", "")
            return {"saved": True, "archive_url": f"https://web.archive.org{loc}"
                    if loc else f"https://web.archive.org/web/*/{url}"}
        return {"saved": False, "status_code": r.status_code}
    except requests.RequestException as e:
        return {"saved": False, "error": str(e)}
```

To save-and-verify: call `wayback_save(url)`, then poll `wayback_check(url)` every 5s
until `available` (bound the wait, e.g. 120s) — archival is asynchronous.

---

## 6. 失败模式与排查

跨源通用：HTTP 429 → 降速、确认 User-Agent；HTTP 403 → 私有/被封；`feedparser` 报错 →
查 `bozo`；RSS 内容字段常含 HTML 需清理。

| 源 | 独有坑 |
|------|--------|
| Google News RSS | 空结果多因查询编码或 `ceid` 不匹配；非 XML 响应查是否被重定向 |
| Reddit（见 social-media.md） | 不存在的子板块也返 200+空列表（查 `data.dist`）；URL 须以 `.json` 结尾 |
| RSS 发现 | 站点不提供 RSS 时手动拼常见路径；解析失败用 `bozo` 诊断；中文乱码查 `encoding` |
| 付费墙 | trafilatura 返回极短文本 = 服务器端墙，转 Jina/Wayback；Jina 返回付费提示 = 硬墙，仅取摘要 |
| Wayback | `available: False` = 从未归档，用 `wayback_save` 主动归档；保存超时属正常（异步） |

---

## 7. 重试退避与降级

外域接口速率越限、连接超时皆为常态。系统化处理：

- **重试**：可重试状态 `(429, 500, 502, 503, 504)` 与 `Timeout`/`ConnectionError`；
  指数退避 `base_delay * backoff ** attempt`，429 优先读 `Retry-After`。
  参数：Google News 2s/3 次/2x（无 Retry-After），Reddit 2s/3 次/3x（遵守 Retry-After），
  Wayback 5s/2 次/2x（save 超时设 60s），RSS 解析失败不重试直接换 feed。
- **新闻降级链**：Google News RSS →（失败）Reddit 搜索 →（失败）主流媒体 RSS
  （BBC/NYT world feed），逐级 `try/except`，任一层有结果即返回。
- **付费墙降级**：即上文 §4 的 `extract_with_graceful_degradation`（full → archive →
  summary → None）。

```python
import time, requests
def retry_with_backoff(fn, max_retries=3, base_delay=2.0, backoff=2.0,
                       retryable=(429, 500, 502, 503, 504)):
    """Call fn() returning a Response; retry on retryable status / network error."""
    last = None
    for attempt in range(max_retries + 1):
        try:
            r = fn()
            if isinstance(r, requests.Response) and r.status_code in retryable:
                ra = r.headers.get("Retry-After")
                time.sleep(float(ra) if ra else base_delay * backoff ** attempt)
                continue
            if isinstance(r, requests.Response):
                r.raise_for_status()
            return r
        except (requests.Timeout, requests.ConnectionError) as e:
            last = e
            if attempt < max_retries:
                time.sleep(base_delay * backoff ** attempt)
            else:
                raise
    raise last
```

---

## 8. 依赖、缓存与日志

```bash
pip install requests feedparser beautifulsoup4 lxml trafilatura
```

`requests`（HTTP）、`feedparser`（RSS/Atom）、`beautifulsoup4`+`lxml`（HTML 清理/解析）、
`trafilatura`（正文提取）。

**缓存（建议）**：新闻数据有时效性，但短时间重复请求同一 URL 纯属浪费且易触发封禁。
用共器 `cached_get`（`<skill-path>/scripts/cached_get.py`，用法同 `requests.get`，
多 `ttl`/`cache_name`）：

```python
import sys
sys.path.insert(0, "<skill-path>/scripts")
from cached_get import cached_get

r = cached_get("https://news.google.com/rss/search",
               params={"q": "AI"}, ttl=300, cache_name="news")
```

特性：原子写入、自动淘汰陈腐（>1 天删除，上限 200 条）、按 `cache_name` 隔离目录。
建议 TTL：Google News 300s、Reddit 帖子 120s、RSS 600s、Wayback 86400s、付费墙尝试 3600s。

**日志**：代码用 `logging` 记录成败、降级触发与异常。`DEBUG`=请求详情，`INFO`=降级/
数据源切换/结果数，`WARNING`=重试/限流/付费墙检测，`ERROR`=全部失败。

---

## 9. 与主技能的关系

本技能是 web-browsing 手册的子引用，专注新闻获取。通用网页抓取见 [父技能](../SKILL.md)；
社交媒体数据见 [social-media.md](./social-media.md)；学术论文见
[academic-pipeline.md](./academic-pipeline.md)。
