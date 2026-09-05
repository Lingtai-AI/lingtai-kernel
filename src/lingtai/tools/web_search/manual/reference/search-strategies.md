---
related_files:
  - src/lingtai/tools/web_search/manual/reference/tier-5-ai-search.md
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Search Strategies

> External legacy recipe, not a built-in `web` engine or installed-capability
> promise. Start with public `web(search/browse)`; select a separate fallback
> explicitly through [web-manual](../SKILL.md) only when needed. Check the
> selected vendor's current API, dependencies, account access and quotas before
> use. These examples grant no install, credential/config change, paid use or
> access-control bypass authority. No live vendor validation is claimed here.

> Part of the [web-manual](../SKILL.md) skill.
> Decision trees, query optimization, engine comparison, and search+extract workflows.

Choosing the right search engine and crafting the right query. For the Tier-5 auto-router
view of AI-native search, see [tier-5-ai-search.md](./tier-5-ai-search.md); for academic
search specifically, see [academic-pipeline.md](./academic-pipeline.md).

---

## Decision Tree

```
Search need
  ├─ Academic intent
  │    ├─ Known identifier (DOI/arXiv)? → Tier 1 API route (academic-pipeline.md)
  │    └─ Discovery? → OpenAlex > Semantic Scholar > SerpAPI/Serper Scholar
  ├─ General intent
  │    ├─ Free, no setup?      → DuckDuckGo
  │    ├─ Google quality?      → Serper (Google proxy)
  │    ├─ By meaning?          → Exa (neural)
  │    └─ Search + answer?     → Tavily
  └─ Real-time intent → news-and-rss.md / social-media.md / realtime-data.md
Then: return results + optionally extract content from the top hits.
```

| Need | Engine | Why |
|------|--------|-----|
| Quick free search, no setup | DuckDuckGo | Zero config, decent results |
| AI agent workflow (search+answer) | Tavily | Returns answer + raw content |
| Find by meaning, not keywords | Exa | Neural/semantic search |
| Google-quality results | Serper | Google proxy, simple API |
| Privacy/independent index | Brave | Not Google/Bing |
| Unlimited self-hosted | SearXNG | Aggregates all engines |
| Academic papers | OpenAlex > Semantic Scholar > SerpAPI Scholar | See academic-pipeline.md |

---

## Engine Reference

All keyed engines read the key from an env var if not passed; keys go in
`TAVILY_API_KEY` / `EXA_API_KEY` / `SERPER_API_KEY` / `BRAVE_API_KEY`.

Historical endpoint examples below are illustrative, not a live service or
pricing matrix. Use the vendor-owned documentation for current supported API
fields, billing, quotas and account access:
[Tavily](https://docs.tavily.com/), [Exa](https://docs.exa.ai/),
[Serper](https://serper.dev/), [Brave](https://api-dashboard.search.brave.com/),
[SearXNG](https://docs.searxng.org/). For DuckDuckGo wrappers, inspect the
installed package's own help; availability is not guaranteed by LingTai.

```python
import os, requests

def search_ddg(query, max_results=10):
    """DuckDuckGo: no key. Import ddgs (falls back to duckduckgo_search)."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return [{"title": r["title"], "url": r["href"], "snippet": r["body"]}
                for r in ddgs.text(query, max_results=max_results)]
    # .news(query) / .images(query) / .videos(query) for those verticals

def search_tavily(query, api_key=None, max_results=5, search_depth="advanced",
                  include_answer=True):
    """Tavily: AI answer + raw content. search_depth=basic|advanced."""
    api_key = api_key or os.environ["TAVILY_API_KEY"]
    r = requests.post("https://api.tavily.com/search",
                      json={"api_key": api_key, "query": query,
                            "search_depth": search_depth, "include_answer": include_answer,
                            "include_raw_content": True, "max_results": max_results},
                      timeout=30)
    r.raise_for_status()
    d = r.json()
    return {"answer": d.get("answer"), "results": d.get("results", [])}

def search_exa(query, api_key=None, num_results=5, search_type="auto",
               include_domains=None, exclude_domains=None):
    """Exa neural search. type=neural(meaning)/keyword(exact)/auto."""
    payload = {"query": query, "type": search_type, "numResults": num_results,
               "contents": {"text": {"maxCharacters": 3000}}}
    if include_domains:
        payload["includeDomains"] = include_domains
    if exclude_domains:
        payload["excludeDomains"] = exclude_domains
    r = requests.post("https://api.exa.ai/search",
                      headers={"x-api-key": api_key or os.environ["EXA_API_KEY"]},
                      json=payload, timeout=30)
    r.raise_for_status()
    return r.json().get("results", [])

def search_serper(query, api_key=None, gl="us", hl="en", num=10, scholar=False):
    """Google via Serper. scholar=True → Scholar results. Returns organic[], answerBox…"""
    path = "scholar" if scholar else "search"
    r = requests.post(f"https://google.serper.dev/{path}",
                      headers={"X-API-KEY": api_key or os.environ["SERPER_API_KEY"]},
                      json={"q": query, "gl": gl, "hl": hl, "num": num}, timeout=15)
    r.raise_for_status()
    return r.json()
```

Brave: `GET api.search.brave.com/res/v1/web/search` with header `X-Subscription-Token`
and param `q`. SearXNG: `GET {instance}/search?q=&format=json&categories=general`.

---

## Query Optimization

Operators work on most engines:

| Operator | Example | Effect |
|----------|---------|--------|
| `site:` | `"attention" site:arxiv.org` | Restrict to a domain |
| `filetype:` | `"deep learning" filetype:pdf` | Specific file type |
| `""` | `"retrieval-augmented generation"` | Exact phrase |
| `OR` / `-` | `"transformer" OR "attention"` / `"apple" -fruit` | Either / exclude |
| `intitle:` / `inurl:` | `intitle:"large language model"` | Must appear in title / URL |
| `after:`/`before:` | `"AI" after:2024-01-01 before:2024-12-31` | Date range |

**Reformulation:** broaden (drop specific terms, use synonyms), narrow (add
domain-specific terms), or rephrase (different wording), then re-search and merge.

---

## Search + Extract

Tavily and Exa return content inline in one call — pass `include_raw_content: True`
(Tavily) or `contents.text` (Exa) as above and read `raw_content` / `results[].text`.
For keyword engines without extraction (DDG, Serper, Brave), search then extract the top
hits with trafilatura:

```python
import time, trafilatura

def search_then_extract(query, max_extract=3):
    """DDG search → trafilatura-extract each top hit's body."""
    out = []
    for r in search_ddg(query, max_results=max_extract):
        try:
            html = trafilatura.fetch_url(r["url"])
            r["extracted_text"] = (trafilatura.extract(html) or "")[:3000] if html else None
        except Exception:
            r["extracted_text"] = None
        out.append(r)
        time.sleep(0.5)
    return out
```

**Pagination:** wrap any `search_fn(query, offset, limit)` in a loop that stops when a page
returns fewer than `per_page` results, sleeping between pages to respect rate limits.

---

## Comparison & Failure Modes

| Failure | Cause | Fallback |
|---------|-------|----------|
| DuckDuckGo 429 | Too many requests | Honor Retry-After/backoff; choose another authorized source if needed |
| Tavily/Exa/Brave quota exhausted | Account limit | Inspect provider quota; choose an admitted available source |
| Exa returns empty | Too specific | Switch to `type: "keyword"` |
| Serper 401 | Invalid/expired key | Check key, fall back to DDG |
| All engines fail | Outage | Use `web(action='search', input={'query': ...}, reasoning='retry with an admitted engine')` with an admitted available engine |
| Spam / low quality | Bad sources | Add `site:`, use Exa for quality |

```bash
pip install ddgs requests trafilatura   # DDG + search-then-extract
```

Keyed engines (Tavily/Exa/Serper/Brave) need their `*_API_KEY` env var set.
