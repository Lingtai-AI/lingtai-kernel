---
related_files:
  - src/lingtai/tools/web_search/manual/reference/academic-pipeline.md
  - src/lingtai/tools/web_search/manual/SKILL.md
maintenance: |
  Keep this bundled web-search reference synchronized with its parent manual and implementation when behavior or routing changes.
---
# Tier 1 — API Metadata Queries

> External legacy recipe, not a built-in `web` engine or installed-capability
> promise. Start with public `web(search/browse)`; select a separate fallback
> explicitly through [web-manual](../SKILL.md) only when needed. Check the
> selected vendor's current API, dependencies, account access and quotas before
> use. These examples grant no install, credential/config change, paid use or
> access-control bypass authority. No live vendor validation is claimed here.

> Part of the [web-manual](../SKILL.md) skill.

**When it applies:** Known academic IDs (DOI, arXiv, PMID, PMC), or sites with free APIs.
**Tools:** `requests` (HTTP) — call APIs directly from Python.
**Speed:** ~0.5s.

### Academic APIs

| API | Endpoint | Best for |
|-----|----------|----------|
| **arXiv** | `GET https://export.arxiv.org/api/query?id_list={ID}` | CS/Physics/Math papers |
| **OpenAlex** | `GET https://api.openalex.org/works/https://doi.org/{DOI}` | Any DOI → full metadata + citations |
| **CrossRef** | `GET https://api.crossref.org/works/{DOI}` | DOI → metadata (title, authors, journal) |
| **Semantic Scholar** | `GET https://api.semanticscholar.org/graph/v1/paper/{DOI}?fields=...` | AI/ML papers, citation graphs |
| **PubMed E-utilities** | `GET https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={PMID}` | Biomedical literature |
| **CORE** | `GET https://api.core.ac.uk/v3/search/works?q={query}` | Open access full text (30M+ papers) |
| **Unpaywall** | `GET https://api.unpaywall.org/v2/{DOI}?email=lingtai@users.noreply.github.com` | Find free PDF for any paper |
| **Europe PMC** | `GET https://www.ebi.ac.uk/europepmc/webservices/rest/search?query={q}&format=json` | Biomedical + PMC full text |
| **DBLP** | `GET https://dblp.org/search/publ/api?q={query}&format=json&h=10` | Computer science conference papers |
| **Papers With Code** | `GET https://paperswithcode.com/api/v1/search/?q={query}` | ML/AI papers with code + benchmarks |
| **DOAJ** | `GET https://doaj.org/api/search/articles/{query}` | Open access journal articles |
| **Zenodo** | `GET https://zenodo.org/api/records?q={query}` | Research data, software, datasets |
| **NASA ADS** | `GET https://ui.adsabs.harvard.edu/abs/{arxiv_id}/bibtex` | Astrophysics/astronomy |


### Quick Examples

The runnable academic recipes have one owner:
[Academic pipeline](academic-pipeline.md#doi-resolution-chain) for CrossRef,
OpenAlex and Semantic Scholar; [PDF acquisition](academic-pipeline.md#pdf-acquisition-chain)
for Unpaywall/CORE/Europe PMC; its DBLP/Papers With Code section for CS/ML.

### Academic Search Pipeline: Find → Enrich → Get PDF

Use [the end-to-end pipeline](academic-pipeline.md#end-to-end-pipeline), not a
second local copy. For keyword batches, search OpenAlex with `per_page` and
`sort=cited_by_count:desc`, retain title/DOI/year/citations/authors, and enrich
each DOI with the owner's Unpaywall routine; missing OA or a failed lookup
must not discard the search result. Preserve both `url_for_pdf` and landing
`url` when available.

### ID Resolution Chain

The [academic routing table](academic-pipeline.md#quick-routing-table) owns
DOI/arXiv/PMID/title/field routing and fallback order; its PDF acquisition
chain owns Unpaywall → arXiv → CORE → Europe PMC (biomedical) ordering.
Metadata-only callers need not fetch a PDF. Never bypass a paywall or login.
