---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Social Media Extraction

> Part of the [web-browsing](../SKILL.md) skill.
> Reddit, Hacker News, Mastodon, X/Twitter, GitHub data extraction.

本技能专注于从社交媒体平台提取**公开**数据：Reddit、Hacker News、Mastodon、
X/Twitter、GitHub 的公开 API 与页面提取，含速率限制、认证说明、失败模式与伦理边界。
所有平台的调用形状一致：带 `User-Agent` 的 `requests.get` → 检查状态 → 把 JSON
字段映射成扁平 dict → 捕获并记录异常。下面每个平台给出端点、代表性代码和其独有的坑。

---

## 1. 决策树与选择指南

```
社交媒体数据
  ├─ Reddit?      → JSON API（§2）：URL 追加 .json
  ├─ Hacker News? → Firebase API（§3）：hacker-news.firebaseio.com/v0/
  ├─ Mastodon?    → 公开 API（§4）：/api/v1/ 或 /api/v2/
  ├─ X/Twitter?   → 公开页面/Nitter（§5，受限，不绕过登录）
  └─ GitHub?      → REST/GraphQL API（§6）：api.github.com
```

| 平台 | 免费额度 | 认证需求 | 稳定性 | 数据丰富度 |
|------|----------|----------|--------|-----------|
| Reddit | 60 req/min | 无需（**必须**设 User-Agent） | 高 | 高 |
| Hacker News | 无限制 | 无需 | 极高 | 中 |
| Mastodon | 因实例而异（~300/5min） | 无需（公开端点） | 高 | 高 |
| X/Twitter | 极有限 | 官方 API 需付费 | 低 | 低 |
| GitHub | 60/hr（token 则 5000/hr） | 无需 | 极高 | 极高 |

---

## 2. Reddit JSON API

在任何标准 URL 后追加 `.json` 即得 JSON。**必须**设 `User-Agent`，否则会被封；
请求间隔 ≥ 2s；遇 429 降速；分页用 `after`/`before`（取自返回的 `data.after`）。
**不要模拟登录或绕过认证**；只能取公开子版块，私有/quarantined 无法访问。

| 目标 | URL | 关键参数 |
|---|---|---|
| 子版块帖子 | `/r/{sub}/{sort}.json` | `sort`=hot/new/top/rising/controversial；`top` 需 `t`=day/week/… |
| 帖子评论 | `/comments/{post_id}.json` | 返回 `[post, comments]`；评论是 `kind=="t1"`，`replies` 递归 |
| 搜索 | `/search.json` 或 `/r/{sub}/search.json` | `q`, `sort`, `t`；限定子版块加 `restrict_sr=on` |
| 用户 | `/user/{name}.json` | `t3`=帖子，`t1`=评论 |

代表性代码（其余端点同形，改 URL/字段即可）：

```python
import requests, time

_last = 0.0
def _reddit_get(url, params=None):
    """Rate-limited GET: User-Agent required, ≥2s between requests."""
    global _last
    if (wait := 2.0 - (time.time() - _last)) > 0:
        time.sleep(wait)
    _last = time.time()
    return requests.get(url, headers={"User-Agent": "LingTai/1.0 (Social Extractor)"},
                        params=params, timeout=15)

def reddit_subreddit(subreddit, sort="hot", limit=25, timeframe="day"):
    """Posts from r/{subreddit}. sort=hot/new/top/rising/controversial."""
    params = {"limit": limit}
    if sort in ("top", "controversial"):
        params["t"] = timeframe
    r = _reddit_get(f"https://www.reddit.com/r/{subreddit}/{sort}.json", params)
    r.raise_for_status()
    return [{
        "id": c["data"].get("name"), "title": c["data"].get("title"),
        "url": c["data"].get("url"), "score": c["data"].get("score"),
        "num_comments": c["data"].get("num_comments"),
        "author": c["data"].get("author"),
        "selftext": c["data"].get("selftext", "")[:1000],
        "over_18": c["data"].get("over_18", False),
        "permalink": c["data"].get("permalink"),
    } for c in r.json()["data"]["children"]]

def reddit_comments(post_id, sort="best", limit=25):
    """Comments for a post. Response is [post_listing, comments_listing]."""
    post_id = post_id.removeprefix("t3_")
    r = _reddit_get(f"https://www.reddit.com/comments/{post_id}.json",
                    {"sort": sort, "limit": limit})
    r.raise_for_status()
    data = r.json()
    out = []
    def walk(children, level=0):
        for c in children:
            if c["kind"] != "t1":
                continue
            d = c["data"]
            out.append({"id": d.get("id"), "author": d.get("author"),
                        "body": d.get("body", "")[:2000], "score": d.get("score"),
                        "level": level})
            if isinstance(d.get("replies"), dict):
                walk(d["replies"]["data"]["children"], level + 1)
    if len(data) > 1:
        walk(data[1]["data"]["children"])
    return out
```

Search: `GET /search.json?q=&sort=relevance&t=all` (add `/r/{sub}/` + `restrict_sr=on`
to scope). User: `GET /user/{name}.json?sort=new` — mixes `t3` posts and `t1` comments.

---

## 3. Hacker News Firebase API

完全免费、无 API Key、无速率限制（仍应合理使用）。`text` 字段是 HTML 需清理；
时间是 Unix 时间戳；`url` 为 `None` 表示自帖（Ask HN），内容在 `text`；
`deleted`/`dead` 标记已删除/屏蔽。故事类型：`story`/`comment`/`job`/`poll`。

Endpoints (`https://hacker-news.firebaseio.com/v0`):
`item/{id}.json`; the story lists `topstories`/`newstories`/`beststories`/`askstories`/
`showstories`/`jobstories.json` (each returns an ID array — slice, then fetch each item);
`user/{name}.json`.

```python
import requests
HN = "https://hacker-news.firebaseio.com/v0"

def hn_item(item_id):
    return requests.get(f"{HN}/item/{item_id}.json", timeout=10).json()

def hn_stories(kind="top", limit=30):
    """kind = top/new/best/ask/show/job → the corresponding {kind}stories list."""
    ids = requests.get(f"{HN}/{kind}stories.json", timeout=15).json()[:limit]
    out = []
    for i in ids:
        it = hn_item(i)
        if it:
            out.append({"id": it.get("id"), "type": it.get("type"),
                        "title": it.get("title"), "url": it.get("url"),
                        "text": it.get("text"), "score": it.get("score"),
                        "by": it.get("by"), "time": it.get("time"),
                        "descendants": it.get("descendants", 0),
                        "kids": it.get("kids", [])})
    return out

def hn_comments(story_id, max_depth=3, max_comments=50):
    """Recursively fetch comments; skip deleted/dead; bounded by depth/count."""
    out = []
    def walk(ids, depth=0):
        if depth > max_depth or len(out) >= max_comments:
            return
        for kid in ids:
            if len(out) >= max_comments:
                return
            it = hn_item(kid)
            if not it or it.get("deleted") or it.get("dead"):
                continue
            if it.get("type") == "comment":
                out.append({"id": it["id"], "by": it.get("by"),
                            "text": it.get("text", ""), "level": depth})
                if it.get("kids"):
                    walk(it["kids"], depth + 1)
    story = hn_item(story_id)
    if story and story.get("kids"):
        walk(story["kids"])
    return out
```

User profile: `GET user/{name}.json` → `id`, `karma`, `about` (HTML), `created`,
`submitted` (item IDs). Full-text search is easier via Algolia (see §8 fallback).

---

## 4. Mastodon 公开 API

去中心化：每个实例独立 API，数据不互通。公开端点无需认证。`content` 始终是 HTML
需清理；`account_id` 是数字而非用户名；速率限制因实例而异（`/api/v1/instance` 可查）；
分页用响应头 `Link` 的 `next`/`prev`。搜索结果取决于该实例已知的联邦内容。

Endpoints (`https://{instance}`): `/api/v1/trends/statuses`, `/api/v1/trends/links`,
`/api/v2/search?q=&type=statuses|accounts|hashtags`,
`/api/v1/accounts/{id}/statuses`, `/api/v1/timelines/tag/{tag}`, `/api/v1/instance`.

```python
import requests
from bs4 import BeautifulSoup

def mastodon_search(instance, query, search_type="statuses", limit=20):
    """type = statuses/accounts/hashtags. instance e.g. 'mastodon.social'."""
    r = requests.get(f"https://{instance}/api/v2/search",
                     params={"q": query, "type": search_type, "limit": limit}, timeout=15)
    r.raise_for_status()
    results = r.json().get(search_type, [])
    return [_parse_status(s) for s in results] if search_type == "statuses" else results

def _parse_status(s):
    a = s.get("account", {})
    return {"id": s.get("id"), "url": s.get("url"), "content": s.get("content"),  # HTML
            "created_at": s.get("created_at"),
            "reblogs_count": s.get("reblogs_count"),
            "favourites_count": s.get("favourites_count"),
            "account": {"username": a.get("username"),
                        "display_name": a.get("display_name"), "url": a.get("url")},
            "tags": [t.get("name") for t in s.get("tags", [])],
            "sensitive": s.get("sensitive", False)}

def clean_html(html):
    """Strip HTML tags (Mastodon content, HN text, etc.)."""
    return BeautifulSoup(html or "", "lxml").get_text(separator=" ", strip=True)
```

`trends/statuses`, `accounts/{id}/statuses`, `timelines/tag/{tag}` all return status
arrays — map each through `_parse_status`. Common instances: `mastodon.social` (largest),
`fosstodon.org` (FOSS), `hachyderm.io` (tech), `mstdn.jp` (日语).

---

## 5. X/Twitter 公开页面（边界与限制）

X 的数据获取面临严重限制：官方 API 需付费（最低 $100/月，Free tier 仅能发推不能读）；
公开页面严重依赖 JS 渲染且反爬严格；Nitter 实例 2024 年后大量关闭。

**伦理边界（严格遵守）：**
- ✅ 官方 API（如有付费订阅）、Nitter 等公开替代前端（如可用）、提取 OG/Twitter Card meta 标签
- ❌ 不绕过登录、不破解 JS 渲染后的登录墙、不使用被盗/泄露的凭证

```python
import requests
from bs4 import BeautifulSoup

def twitter_meta_extract(url):
    """OG + Twitter Card meta tags from a public X page (only if login-free)."""
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; LingTai/1.0)"},
                         timeout=15)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "lxml")
        og = {t.get("property", "")[3:]: t.get("content", "")
              for t in soup.find_all("meta") if t.get("property", "").startswith("og:")}
        tw = {t.get("name", "")[8:]: t.get("content", "")
              for t in soup.find_all("meta") if t.get("name", "").startswith("twitter:")}
        if not og and not tw:
            return None
        return {"title": og.get("title") or tw.get("title"),
                "description": og.get("description") or tw.get("description"),
                "image": og.get("image"), "url": og.get("url"),
                "card_type": tw.get("card")}
    except Exception:
        return None
```

Fallbacks: a Nitter instance (`https://nitter.net/user/status/{id}`; tweet body is in
`div.tweet-content` — but most instances are down), or Jina Reader (`https://r.jina.ai/{url}`).
Last resort: search-engine cache (`site:twitter.com` via a search provider).

> **底线：** 若以上都无法获取，说明该内容确实不可公开获取。接受限制，不要进一步尝试绕过。

---

## 6. GitHub 公开数据

REST API v3（`api.github.com`）。搜索 API 限额（10/min 无 token）远低于普通 API
（60/hr 无 token；有 token 则搜索 30/min、普通 5000/hr）。`Accept` header 控制格式：
`vnd.github.raw`（README 原文）、`.html`、`.diff`、`.patch`。仓库 `size` 单位是 KB。
Issue 端点也返回 PR，用 `pull_request` 字段区分。代码搜索 (`/search/code`) 需 token。

| 目标 | 端点 | 返回要点 |
|---|---|---|
| 仓库搜索 | `/search/repositories?q=&sort=stars` | `q` 支持 `language:`, `stars:>N`；结果在 `items` |
| 代码搜索 | `/search/code?q=` | **需 token** |
| 仓库详情 | `/repos/{owner}/{repo}` | stars/forks/language/license/topics/size(KB)… |
| README | `/repos/{o}/{r}/readme` + `Accept: vnd.github.raw` | 原始文本，无则 404 |
| 目录 | `/repos/{o}/{r}/contents/{path}` | name/path/type/size |
| Issues/PRs | `/repos/{o}/{r}/issues` 或 `/pulls` | `state`, `sort`, `direction`；issues 含 PR |
| 用户 | `/users/{name}` | name/company/followers/public_repos… |
| 配额 | `/rate_limit` | `resources.core` / `resources.search` |

```python
import requests, time

_gh_last = 0.0
def github_get(url, params=None, token=None):
    """Rate-limited GET; pass a token to raise limits and enable code search."""
    global _gh_last
    if (wait := 1.0 - (time.time() - _gh_last)) > 0:
        time.sleep(wait)
    _gh_last = time.time()
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "LingTai/1.0"}
    if token:
        headers["Authorization"] = f"token {token}"
    r = requests.get(url, headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r

def github_search_repos(query, sort="stars", per_page=10, token=None):
    """Search repos. query supports 'language:python', 'stars:>1000', etc."""
    r = github_get("https://api.github.com/search/repositories",
                   {"q": query, "sort": sort, "order": "desc", "per_page": per_page}, token)
    return [{"full_name": i.get("full_name"), "description": i.get("description"),
             "url": i.get("html_url"), "stars": i.get("stargazers_count"),
             "forks": i.get("forks_count"), "language": i.get("language"),
             "topics": i.get("topics", []),
             "license": (i.get("license") or {}).get("spdx_id")}
            for i in r.json().get("items", [])]
```

Other endpoints follow the same `github_get` + field-map shape. README needs the raw
`Accept` header via a plain `requests.get` (not `github_get`) so it returns text, not JSON.

---

## 7. 失败模式与平台特殊注意

跨平台通用：HTTP 429 → 遵守 `Retry-After`/`X-RateLimit-Remaining` 降速；HTTP 403 →
配额耗尽或私有资源；HTTP 404 → 资源不存在，正常返回 `None`；HTML 内容字段（Mastodon
`content`、HN `text`）始终需清理。

| 平台 | 独有坑 |
|------|--------|
| Reddit | URL 必须以 `.json` 结尾（否则返回 HTML）；不存在的子版块也返 200+空列表（查 `data.dist==0`）；`selftext` 是 Markdown；子版块名大小写不敏感但用小写 |
| Hacker News | 无效/已删 item 返 `null`（跳过）；递归过深要设 `max_depth`/`max_comments`；Firebase 偶尔超时需重试；列表可能有重复 ID 需去重 |
| Mastodon | 跨实例内容不可见（去目标用户所在实例查）；`account_id` 是数字非用户名；不同实例速率限制不同 |
| X/Twitter | 官方 API 最低 $100/月；Nitter 大量关闭不可依赖；OG meta 常为空；**绝不绕过登录** |
| GitHub | 搜索 API（10/min）远严于普通 API（60/hr）；配额耗尽等重置或用 token；大结果集用 `page` 分页 |

---

## 8. 重试退避与降级策略

外域接口诡变无常——速率越限、连接超时皆为常态。系统化处理：

- **重试**：可重试状态 `(429, 500, 502, 503, 504)` 与 `Timeout`/`ConnectionError`；
  指数退避 `base_delay * backoff_factor ** attempt`，429 优先读 `Retry-After` header。
- **退避参数**：Reddit 2s/3 次/3x，HN 1s/3 次/2x（无限流但 Firebase 偶超时），
  Mastodon 2s/2 次/2x，GitHub 2s/3 次/2x，X/Twitter 5s/1 次（基本不重试，直接降级）。
- **跨平台降级**：技术讨论可 Reddit → Hacker News（Algolia 搜索
  `https://hn.algolia.com/api/v1/search?query=&tags=story`）→ Mastodon 依次回退。
- **GitHub 配额降级**：先 `/rate_limit` 查 `search.remaining`，为 0 时降级到
  搜索引擎 `site:github.com` 查询。

```python
def retry_with_backoff(fn, max_retries=3, base_delay=2.0, backoff=2.0,
                       retryable=(429, 500, 502, 503, 504)):
    """Call fn() returning a Response; retry on retryable status / network error."""
    import time, requests
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

## 9. 依赖、缓存与日志

```bash
pip install requests beautifulsoup4 lxml   # requests: 所有平台；bs4+lxml: HTML 清理
```

**缓存（强烈建议）**：社交 API 调用频繁极易触发封禁。用共器 `cached_get`（位于
`<skill-path>/scripts/cached_get.py`）——用法同 `requests.get`，多 `ttl`/`cache_name`：

```python
import sys
sys.path.insert(0, "<skill-path>/scripts")
from cached_get import cached_get

r = cached_get("https://www.reddit.com/r/python/hot.json",
               headers={"User-Agent": "LingTai/1.0"}, ttl=120, cache_name="social")
```

特性：原子写入、自动淘汰陈腐（>1 天删除，上限 200 条）、按 `cache_name` 隔离缓存目录。
建议 TTL：HN 60s（实时），Reddit 帖子 120s、搜索 300s，Mastodon 300s，GitHub 仓库搜索 3600s。

**日志**：代码用 `logging` 记录请求成败、降级触发与异常。`DEBUG`=请求详情，
`INFO`=降级/平台切换/结果数，`WARNING`=重试/限流，`ERROR`=全部失败/配额耗尽。

---

## 10. 与主技能的关系

本技能是 web-browsing 手册的子引用，专注社交媒体公开数据提取。通用网页抓取见
[父技能](../SKILL.md)；新闻与 RSS 见 [news-and-rss.md](./news-and-rss.md)；
学术论文见 [academic-pipeline.md](./academic-pipeline.md)；反检测与代理见
[stealth.md](./stealth.md)。
