---
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-1-apis.md
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
  - src/lingtai/tools/web_search/manual/assets/regex-patterns.json
maintenance: |
  Keep this identifier and open-access route aligned with Tier 1 and the
  endpoint/regex assets. It is an explicit external procedure, not a Web
  provider or an automatic fallback chain; verify current API policies.
---
# Academic identifiers and open-access route

Use when the task has a DOI, arXiv ID, PMID/PMC ID, or academic keywords and
needs metadata or a public paper copy. Start with `web` search for discovery,
then choose this procedure explicitly.

## Route by input

| Input | First source | Then |
|---|---|---|
| DOI | CrossRef metadata | OpenAlex/Semantic Scholar for enrichment; Unpaywall for OA. |
| arXiv ID | arXiv API or derived public PDF | Enrich metadata only if needed. |
| PMID/PMC | PubMed E-utilities or Europe PMC | Resolve DOI/PMCID for further public data. |
| CS/ML keywords | DBLP, OpenAlex, or Papers With Code | Enrich selected identifiers. |
| Biomedical keywords | PubMed/Europe PMC | Preserve records even when full text is unavailable. |

DOAJ can discover OA journal articles; Zenodo covers research outputs; NASA ADS
covers astronomy/physics. For BibTeX/RIS export use the verified DOI record
and the source's citation export/content negotiation, checking title/authors/year.

The historical endpoint inventory is [`assets/api-endpoints.json`](../assets/api-endpoints.json)
and identifier patterns are in [`assets/regex-patterns.json`](../assets/regex-patterns.json).
Inspect current vendor docs, rate headers, and key requirements before batching.

## Open-access and citation safety

Check Unpaywall or a publisher/repository-provided public URL before attempting
PDF extraction. Its email parameter must be a real contact authorized for disclosure and
accepted by current vendor policy; never invent a person or reuse an example
address. Keep both a landing URL and `url_for_pdf` when present, and
retain metadata when OA lookup fails. CrossRef's polite `mailto` is optional
only when authorized. Never bypass a subscription, login, CAPTCHA, robots
rule, or other access control; stop with metadata/abstract when no public copy
is available. A PDF download is the explicit [Tier 0](tier-0-pdf.md) route.

Do not treat remembered quotas, API fields, or a returned AI summary as current
truth. Label provider-derived citations and verify the DOI/title before using
them in a final claim.
