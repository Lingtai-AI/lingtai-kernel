---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
maintenance: |
  Keep this short static-page procedure aligned with the tier index and the
  installed helper. Do not promise JavaScript, login, or paywall bypass.
---
# Tier 1.5 — static text extraction

Use for an ordinary public article, blog, news, or documentation page when
plain `web browse` is insufficient and no structured selectors are needed.
Trafilatura does not execute JavaScript or authenticate.

```python
import trafilatura
html = trafilatura.fetch_url(url)
text = trafilatura.extract(html) if html else None
metadata = trafilatura.bare_extraction(html) if html else None
```

`bare_extraction()` may return a `Document`, not a dict; use its supported
conversion/attributes before calling mapping methods. Keep complete text when
needed rather than silently substituting a preview. For structured selectors
use [Tier 2](tier-2-beautifulsoup.md); for JS interaction use the separate
[agent-native browser](agent-native-browser.md).
