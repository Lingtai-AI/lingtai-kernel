---
name: web-manual-maintenance-bundles
description: >
  Inventory and ownership map for the bundled Web external procedures, assets,
  and retained helper scripts.
version: 2.0.0
last_changed_at: "2026-09-09T10:53:00Z"
related_files:
  - src/lingtai/tools/web_search/manual/SKILL.md
  - src/lingtai/tools/web_search/manual/reference/tier-quick-refs/SKILL.md
  - src/lingtai/tools/web_search/manual/scripts/cached_get.py
  - src/lingtai/tools/web_search/manual/scripts/extract_page.py
  - src/lingtai/tools/web_search/manual/assets/api-endpoints.json
  - src/lingtai/tools/web_search/manual/assets/css-selectors.json
  - src/lingtai/tools/web_search/manual/assets/extraction-pipeline.json
  - src/lingtai/tools/web_search/manual/assets/regex-patterns.json
  - src/lingtai/tools/web_search/manual/assets/search-providers.json
  - src/lingtai/tools/web_search/manual/assets/site-templates.json
maintenance: |
  Keep the asset inventory and one-way ownership routes accurate. Scripts and
  JSON are retained procedure support, not model-facing actions. Do not copy
  script logic into references or turn the bundle into an automatic fallback.
---
# Bundle maintenance

Open this when changing the manual bundle or checking whether a fact belongs in
a script, asset, or reference. The parent manual owns first-call behavior;
[operation-contract.md](../operation-contract.md) owns exact Web semantics; the
[tier index](../tier-quick-refs/SKILL.md) owns recovery routing.

## Retained procedure support

| Path | Owner | Use |
|---|---|---|
| `scripts/extract_page.py` | executable helper | Explicit `--tier`, `--search`, `--fallback`, and `auto_tier()` procedures. |
| `scripts/cached_get.py` | executable helper | Legacy cache helper; currently broken on its normal successful-GET path (below). |
| `assets/api-endpoints.json` | endpoint data | Academic, search, extraction, and real-time endpoint metadata. |
| `assets/css-selectors.json` | selector data | Reusable extraction selectors. |
| `assets/site-templates.json` | site data | Known-site selectors and endpoint hints. |
| `assets/extraction-pipeline.json` | pipeline data | Legacy text: not valid JSON; not a runnable pipeline or Web policy. |
| `assets/regex-patterns.json` | identifier data | DOI/arXiv/PMID/PMC/ISBN and URL patterns. |
| `assets/search-providers.json` | external provider data | Explicit vendor recipes; verify availability and authorization. |

Scripts describe their own implementation only, not the public Web contract.
Assets are historical hints, not current authority: fixed free/unlimited/quota
claims, example contacts, package names and endpoints require upstream checking. Inspect current vendor documentation for endpoints, quotas,
dependencies, and prices; this bundle grants no install, credential, paid-use,
network, or access-control authority. Never put secrets or private paths in
examples or diagnostics.

## Known legacy limitations

- `cached_get.py` has no imported JSON module and its `json` argument shadows
  that name. A normal successful GET reaches `json.dump` and raises
  `AttributeError` after opening its cache file for writing; an existing file
  may already have been truncated. Cache reads also fail. Do not recommend it
  as a working cache or run it over shared/pre-existing data. Its cache key
  includes URL/method but excludes params, headers and identity. No automatic
  purge occurs: old claims about one-day/200-entry eviction were not implemented.
- Cache defaults are `/tmp/web-browsing-cache`, overridden by
  `WEB_BROWSING_CACHE_DIR`, and TTL `3600`, overridden by
  `WEB_BROWSING_CACHE_TTL` at import. `clear_cache(url)` unlinks that GET entry;
  `clear_cache()` unlinks every matching JSON file. Both require explicit
  deletion authorization; neither is a diagnostic. The CLI prints a preview.
- `assets/extraction-pipeline.json` is not valid JSON (`500/month` is an
  unquoted expression). Read it only as legacy notes, not machine configuration.
  The other parseable assets are not automatically consumed by public Web.
- `extract_page.py` is a separate Requests/browser/vendor helper, not the vetted
  public BrowserEngine. Its preview fields are truncated (including the JSON
  saved by `--json`); exit 0 means no `error` key, not complete text or useful
  content. CrossRef helper `is_oa` actually contains a citation count, not an OA
  boolean; verify OA against the real source. `--save`/`--json` overwrite paths.
- `--fallback` can advance through local tiers to **Jina**, sending the target
  URL to an external service. `auto_tier` is a heuristic; explicit `--tier`
  still may do several metadata requests. Authorize the whole selected route,
  dependencies and data disclosure before execution; no implicit chain.
  Helper diagnostics may include raw URLs/exceptions: never feed secrets or
  private URLs. Tier 3 blocks images/styles/fonts/media and returns only a
  preview, so it is not a visual-validation harness.

These are preserved implementation limitations, not runtime fixes in the manual
reduction. For a proposed repair, use the `lingtai-issue-report` protocol and
obtain filing/implementation authority; do not silently patch installed helpers.

## Cleanup / Footprint

Public Web leaves complete `tmp/tool-results/web-*` artifacts; snapshots,
cursors and references are bounded process-local state. Optional helpers leave
explicit downloads/JSON outputs and the cache described above. None are
implicitly disposable: preserve live referenced results, evidence, user data,
configuration and secrets, and never blindly clear a shared cache.

After a large retrieval session or before retirement, follow `skills-manual` →
`reference/cleanup-footprint-contract.md#shared-footprint-check-recipe`. Select
only this Agent's `tmp/tool-results/web-*` plus the exact task-created output
paths; inspect a configured helper cache separately only when authorized.
The shared recipe reports count/bytes without changing inspected files; its
optional `logs/cleanup.jsonl` append is a separate explicit write. Present a
dry-run listing what stays/goes, obtain explicit human consent, then perform
only the approved cleanup and record timestamp/tool/dry-run-or-apply/count/bytes/
path summary/approval. If consent is absent, stop at inspection.
