---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Migration from v2 to v3

> Part of the [web-browsing](../SKILL.md) skill.

In **v2**, web browsing was spread across separate sub-skills
(`web-content-extractor`, `academic-search-pipeline`, `search-strategies`,
`news-and-rss`, `social-media-extraction`, `realtime-data`, `stealth-browsing`)
under `~/.lingtai-tui/utilities/`. **v3** merged all of these into this single
`web-browsing-manual` skill and expanded the tier system from 4 tiers (0–3) to 7
(0–5, with 1.5); the old sub-skill names now map onto this manual's reference
files (`academic-pipeline.md`, `search-strategies.md`, etc.) rather than
standalone directories.

## What changed

| v2 | v3 | Why |
|---|---|---|
| `--tier 0/1/2/3/auto` | `--tier 0/1/1.5/2/3/4/5/auto` | Added Tier 1.5 (trafilatura), Tier 4 (Jina/Firecrawl), Tier 5 (AI search) |
| Auto-tier defaulted generic URLs to Tier 2 (BS4) | Defaults to Tier 1.5 (trafilatura) | 10–50× faster; **breaking** — pass `--tier 2` explicitly if you relied on BS4-specific selectors/templates |
| No search mode | `--search --search-provider {ddg,tavily,exa}` | Search integrated into the script |
| No fallback chain | `--fallback` flag | Automatic tier escalation on failure |
| Tier 1: arXiv, CrossRef only | + Semantic Scholar, Unpaywall, PubMed E-utilities | Broader academic coverage |
| Tier 3: no resource blocking | Blocks images/CSS/fonts in Playwright | ~2× faster |
| ~6 domains in site detection | 30+ domains in `auto_tier()` | Better auto-routing |
| Text preview only | + `text_length`, `jsonld`, `opengraph`, `meta_*` fields | Forward-compatible: existing JSON parsing still works |
| No smoke tests | `--test` flag | Built-in verification |

Tier numbering itself didn't change meaning — Tier 2 is still BS4; old `--tier 2`
calls behave identically. `--save pdf.pdf` is unchanged.

## Post-release patches (2026-04-27)

| Issue | Symptom | Fix |
|-------|---------|-----|
| Bare DOI input crash | `python3 extract_page.py "10.1038/xxx"` → `Invalid URL: No scheme supplied` | Input normalizer: bare DOI → `https://doi.org/...`, bare arXiv ID → `https://arxiv.org/abs/...`, other non-URL text → auto search mode |
| `duckduckgo_search` renamed | `RuntimeWarning: This package has been renamed to ddgs` on every search | Import tries `from ddgs import DDGS` first, falls back to `from duckduckgo_search import DDGS` |
