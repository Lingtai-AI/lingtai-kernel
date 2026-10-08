---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/academic-pipeline.md
maintenance: |
  Keep this explicit PDF fallback aligned with its parent tier index. It is a
  procedure reference, not a Web action; preserve public-access and no-paywall
  bypass boundaries.
---
# Tier 0 — public PDF extraction

Use for a direct PDF URL or a known DOI/arXiv identifier after `web browse`
reports unsupported content. This is an external, explicit procedure; first
confirm the URL is public or otherwise authorized. Do not bypass login,
paywall, robots, or access control.

```bash
curl -L "https://arxiv.org/pdf/2401.12345.pdf" -o paper.pdf
```

For an arXiv ID, retain the dot and use `/pdf/<ID>.pdf`. Extract locally when
`fitz`/PyMuPDF is available:

```python
import fitz
doc = fitz.open("paper.pdf")
text = "\n".join(page.get_text() for page in doc)
```

The URL may lack a `.pdf` suffix; verify the response content type before
assuming it is a PDF. For academic identifier routing and open-access lookup,
load [academic pipeline](academic-pipeline.md), not a second PDF recipe.
