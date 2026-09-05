---
related_files:
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/browser/BEHAVIORS.md
  - src/lingtai/tools/browser/CONTRACT.md
  - src/lingtai/tools/browser/core.py
  - src/lingtai/tools/browser/port.py
  - src/lingtai/tools/browser/netpolicy.py
  - src/lingtai/tools/browser/fetcher.py
  - src/lingtai/tools/browser/extractor.py
  - src/lingtai/tools/browser/cursor.py
  - src/lingtai/tools/browser/snapshots.py
  - src/lingtai/tools/browser/refstore.py
  - src/lingtai/adapters/browser_transport.py
  - src/lingtai/tools/web_search/ANATOMY.md
  - src/lingtai/tools/browser/__init__.py
  - src/lingtai/tools/browser/glossary-en.md
  - src/lingtai/tools/browser/glossary-wen.md
  - src/lingtai/tools/browser/glossary-zh.md
maintenance: |
  This map describes only the internal browse subcomponent of unified web.
  Keep its Contract edge and parent/child links reciprocal. Browser files are
  retained implementation truth, not a public capability or manual owner.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# Internal browse subcomponent Anatomy

The browser package owns static browse policy and orchestration used by the
public `web` manager. It is deliberately not registered independently.

## Components

- `BrowserEngine` — bounded fetch, extraction, provenance, snapshots, cursors,
  refs, and typed result envelopes (`src/lingtai/tools/browser/core.py:126-327`).
- `BrowserPort` and transport value types — technology-neutral outbound boundary
  (`src/lingtai/tools/browser/port.py:14-64`).
- `netpolicy`, `fetcher`, and `extractor` — SSRF, deadline, redirect, decode,
  undecodable-content, and static text policy
  (`src/lingtai/tools/browser/netpolicy.py:43-224`,
  `src/lingtai/tools/browser/fetcher.py:56-136`,
  `src/lingtai/tools/browser/extractor.py:175-268`).
- `CursorCodec`, snapshot store, and `RefStore` — bounded same-Agent state
  (`src/lingtai/tools/browser/cursor.py:22-143`,
  `src/lingtai/tools/browser/snapshots.py:33-86`,
  `src/lingtai/tools/browser/refstore.py:8-37`).
- `VettedHttpTransport` — production adapter outside this package
  (`src/lingtai/adapters/browser_transport.py:17-165`).
- `__init__.py` — the internal-subcomponent surface. Only the parent `web`
  manager exposes model-facing actions; the concrete network adapter is
  imported inside `setup` so importing the registry stays lazy, and this
  retained module never registers a public browser tool. `browser` is an
  internal browse child rather than a public capability, so agent manual
  installation installs only the `web` owner's manual; there is no separate
  `browser/manual/`.

## Connections

`web_search.WebManager` constructs one engine and dispatches browse calls to it.
The engine calls only `BrowserPort`; the adapter is selected by the composition
root. SearchService and settings selection remain sibling concerns. After a
successful `handle()` call, `WebManager._deliver_browse` also reads the
resulting snapshot back out of `self.snapshots` (by `snapshot_id`) to obtain
the complete joined block text for the inline-vs-artifact delivery decision;
this is a read-only lookup on the engine's own snapshot store, not a new
Core/Port boundary.

## Composition

The parent [`src/lingtai/tools/web_search/ANATOMY.md`](../web_search/ANATOMY.md)
owns the public capability and manual. The parent
[`src/lingtai/tools/ANATOMY.md`](../ANATOMY.md) owns registry composition.
This child has no independent model-facing schema or catalog entry.

## State

The engine owns per-Agent HMAC cursor state, bounded snapshots, and link refs.
It writes no persistent state and never shares refs across Agents.

## Notes

`browser.setup()` does not call `add_tool`; there is no browser-owned manual.
All public documentation routes through `web-manual`
(`src/lingtai/tools/web_search/manual/SKILL.md`).
