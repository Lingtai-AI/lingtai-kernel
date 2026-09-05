---
name: browser-internal
contract_version: 4
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/browser/ANATOMY.md
  - src/lingtai/tools/browser/port.py
  - src/lingtai/tools/browser/core.py
  - src/lingtai/tools/browser/fetcher.py
  - src/lingtai/tools/browser/netpolicy.py
  - src/lingtai/tools/browser/extractor.py
  - src/lingtai/tools/browser/cursor.py
  - src/lingtai/tools/browser/snapshots.py
  - src/lingtai/tools/browser/refstore.py
  - src/lingtai/adapters/browser_transport.py
  - src/lingtai/tools/web_search/CONTRACT.md
  - tests/test_browser_capability.py
maintenance: |
  This is the internal browse-subcomponent Contract owned by unified web.
  Keep its Anatomy reciprocal and preserve the Core Port, adapter, SSRF,
  provenance, cursor, snapshot, reference, deadline, and typed-failure rules.
  It is not a public capability and must not acquire a registry, schema, prompt,
  catalog, or manual entry of its own.
---
# Internal browse subcomponent

## Purpose

This package is the bounded static browse Core/Port used by the public `web`
capability. It is retained as a technology-neutral child, not a separate
model-facing browser tool.

## Behavior
Guarded by: [BR001](BEHAVIORS.md#behavior-br001)

`BrowserEngine.handle` accepts browse arguments and returns the existing
structured success or typed failure payload, including provenance, source hash,
SSRF-safe redirects, snapshots, cursors, refs, bounded extraction, and
`untrusted_content`. Internally it still paginates a fetched/continued page
via `paginate_blocks` and its own `max_chars`, but the unified parent
(`WebManager._deliver_browse`) always overrides that internal page with the
complete joined `snapshot.blocks` text before returning: the public contract
never exposes only a first page or partial document for a fresh success. The
unified parent adds public `action`, output-delivery setting metadata, and the
inline-vs-artifact decision over that complete text (see
`web_search/CONTRACT.md`); this Core has no knowledge of `settings/web.json`
or artifact files — it only ever returns its own paginated shape, which the
parent replaces or spills. Manual loading belongs to the parent web manager.

## Port

`BrowserPort.resolve` and `request` remain the Core-owned outbound boundary.
The production pinned HTTP(S) transport is outside this package and receives
vetted targets and remaining end-to-end deadlines.

## Adapters

`src/lingtai/adapters/browser_transport.py` is the production adapter. Tests
inject a fake BrowserPort. No adapter registers a model-facing tool.

## Contract rules

Only public HTTP(S) destinations are accepted. Existing DNS/SSRF, redirect,
content, charset, byte, link, snapshot, ref, cursor, timeout, and typed-error
bounds remain normative. A transport success whose body does not decode to
readable text is not usable content: when a decode-replacement warning applies
and the extracted text is large enough to judge, dominated by replacement
characters, and carries raw control bytes, extraction yields no blocks and the
existing `NO_TEXT_BLOCKS` extract failure is raised with HTTP provenance and a
decode-specific recovery. Cleanly decoded text is never reclassified.
SearchService is not imported or called by this Core.
Refresh/reconstruction must use fresh per-Agent state; no filesystem snapshot,
credential, cookie, or hidden fallback is part of this subcomponent.

## Contract tests

The existing browser capability, policy/cursor-edge, and transport tests cover
this child. Unified web checks additionally prove search result references use
this same live engine without causing a second public registration.

## Maintenance

Keep this internal Contract paired with its Anatomy and linked from the parent
web Contract as an implementation boundary. Do not expose `browser` as a
capability name or give it its own installed manual; `browser` has no
`manual/` of its own.

