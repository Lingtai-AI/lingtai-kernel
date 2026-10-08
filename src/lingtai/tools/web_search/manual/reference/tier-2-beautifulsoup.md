---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/assets/css-selectors.json
  - src/lingtai/tools/web_search/manual/assets/site-templates.json
maintenance: |
  Keep this structured-extraction procedure aligned with selector assets and
  the tier index. The assets own reusable patterns; avoid copying their tables.
---
# Tier 2 — structured HTML extraction

Use for public pages where the task needs lists, tables, metadata, or known CSS
selectors rather than only article text. Prefer a documented public API (for
example Reddit `.json`, GitHub REST, or a news feed) when one exists.

```python
import requests
from bs4 import BeautifulSoup
response = requests.get(url, headers={"User-Agent":"LingTai/1.0"}, timeout=15)
response.raise_for_status()
soup = BeautifulSoup(response.text, "lxml")
title = soup.title.get_text(strip=True) if soup.title else None
```

Load [`assets/css-selectors.json`](../assets/css-selectors.json) for common
patterns and [`assets/site-templates.json`](../assets/site-templates.json) for
known sites. Treat selectors as hints, not current vendor guarantees. Clean
HTML fields before presenting them, check empty/changed markup, and preserve
source URLs. This route does not execute JavaScript, log in, or bypass an
access control; use Tier 3 or the separate agent-native browser only when the
public task genuinely requires interaction.
