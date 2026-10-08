---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/academic-pipeline.md
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
maintenance: |
  Keep this endpoint index and routing note aligned with the academic pipeline
  and bundled endpoint asset. Verify current vendor policy; do not duplicate
  runnable API recipes here.
---
# Tier 1 — public API metadata

Use when a known identifier or a documented public structured API is the right
source. The historical endpoint inventory is
[`assets/api-endpoints.json`](../assets/api-endpoints.json); the
identifier and open-access sequence belongs to
[academic-pipeline.md](academic-pipeline.md).

| Input/need | First owner |
|---|---|
| DOI metadata/citations | CrossRef → OpenAlex; see academic pipeline. |
| arXiv identifier | arXiv Atom API; derive the public PDF URL only when appropriate. |
| PMID/PMC biomedical record | PubMed E-utilities or Europe PMC. |
| CS/ML discovery | DBLP or Papers With Code, then the academic pipeline. |
| OA copy | Unpaywall, then an applicable public repository/API. |
| facts or public structured data | The matching endpoint in the asset and [real-time data](realtime-data.md). |

Check response status, content type, current rate policy, and whether a key or
mailto is required. Use an authorized real contact for Unpaywall under its current policy, not
an invented identity or a copied example address. Preserve a search result when enrichment or OA
lookup fails. Never bypass a publisher paywall or login, and never treat this
external catalog as a built-in Web provider.
