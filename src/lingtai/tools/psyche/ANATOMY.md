---
related_files:
  - src/lingtai/tools/psyche/BEHAVIORS.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/psyche/__init__.py
  - src/lingtai/tools/psyche/settings.py
  - src/lingtai/agent.py
  - src/lingtai/tools/psyche/glossary-en.md
  - src/lingtai/tools/psyche/glossary-zh.md
  - src/lingtai/tools/psyche/glossary-wen.md
  - src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
  - tests/test_psyche_family.py
  - tests/test_tool_settings_contract.py
maintenance: |
  Keep paths real, repo-relative, duplicate-free, and reciprocal with the paired
  Contract and parent/neighbor anatomies. Code is the structural source of
  truth: update this graph when symbols, connections, state ownership, or
  composition move.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# tools/psyche

Mandatory LTP v2 family that is the one public root for the four durable
domains (`pad + lingtai + knowledge + skills = psyche`). Five actions are
read-only manual loaders; the reserved `settings` child returns two fully
redacted Psyche-owned Pad rows. The package owns no domain state, catalog, or
composer of its own. Its static official declaration binds only `workdir` and
the read-only `psyche_settings` snapshot Port. Its `boot` keeps the mandatory
intrinsic lifecycle shim, invokes the domain-owned composers, then mounts the
one public handler through the declared ToolPlugin registrar.

## Components

- `DOMAIN_MANUALS` — the one fixed registry mapping each domain action to the
  installed manual it loads (`src/lingtai/tools/psyche/__init__.py:58-64`).
- `ACTION_ORDER` — the exact public inventory `pad | lingtai | knowledge |
  skills | settings | manual`, derived from the composed family so injected
  settings cannot drift (`src/lingtai/tools/psyche/__init__.py:195-211`).
- `_ROUTER_MANUAL` — the routing-table manual name loaded by the reserved
  `manual` child (`src/lingtai/tools/psyche/__init__.py:66-72`).
- `_build_children` — builds all five manual children from the shared
  `build_manual_child` loader with one strict-empty input schema
  (`src/lingtai/tools/psyche/__init__.py:95-106`).
- `_FAMILY`, `_ACTION_ENUM_DESCRIPTION`, `get_description`, `get_schema` —
  settings-opted schema-only family plus the model-facing routing prose
  (`src/lingtai/tools/psyche/__init__.py:109-149`).
- `settings.py::build_settings_provider` — binds the Agent's last successfully
  reconstructed Pad snapshot into the exact two-row provider; both rows carry
  the private full-redaction marker and the provider performs no source I/O
  (`src/lingtai/tools/psyche/settings.py:14-46`).
- `_adapt_manual_result` — the one post-dispatch Host adapter producing the flat
  `{status, manual, manual_path}` shape
  (`src/lingtai/tools/psyche/__init__.py:152-161`).
- `_bind` / `DECLARATION` — statically declare the four operational actions plus
  reserved settings/manual children, bind only `workdir`/`psyche_settings`, drop
  intrinsic `_tc_id`, dispatch through the generic family, and preserve
  Psyche-shaped unknown-action errors
  (`src/lingtai/tools/psyche/__init__.py:164-211`).
- `boot` — lifecycle only: runs the Pad and LingTai domains' private composers
  once at construction, since those packages are no longer registered intrinsics
  and the kernel boot loop no longer reaches them
  (`src/lingtai/tools/psyche/__init__.py:214-227`).

## Connections

- `tools/registry.py` wires this package into `INTRINSICS` as the mandatory
  public `psyche` root; `kernel/tool_result_summary.py` carries it in
  `_LTP_V2_MIGRATED_FAMILIES` and `tools/daemon` in `EMANATION_BLACKLIST`.
- Dispatch and schema composition flow through
  [`tool_family`](../tool_family/ANATOMY.md); every manual child's loader is
  `tool_family/manual.py::build_manual_child`, which reads
  `.library/intrinsic/capabilities/<name>/SKILL.md` via `tools/_manual.py`.
  Generic settings injection places `settings` immediately before `manual`.
- The four domain manuals are installed by
  `Agent._install_intrinsic_manuals`: `pad-manual` and `lingtai-manual` from
  `intrinsic_skills/`, `knowledge` and `skills` from those packages' own
  `manual/` directories. The routing table ships as
  `intrinsic_skills/psyche-manual/`.
- `boot` imports `pad._pad_load` and `lingtai._lingtai_load` and calls them once
  at construction; those are the domains' own composers, the same ones
  `Agent._reload_prompt_sections` reuses. No edge runs the other way: nothing in
  this package is imported by the domain packages, by
  `Agent._reload_prompt_sections`, or by the catalog composers.
- `Agent._reload_prompt_sections` consumes the canonical resolved Pad inputs,
  writes configured Pad content only when `system/pad.md` is missing/empty,
  delegates composition to `pad._pad_load`, and commits the narrow
  `(pad, pad_file)` discovery snapshot only after the complete section pass
  succeeds. No Psyche public action reaches that edge or rereads either source
  (`src/lingtai/agent.py:2479-2788`).

## Composition

Parent: [`tools/ANATOMY.md`](../ANATOMY.md). Paired interface promise:
[`CONTRACT.md`](CONTRACT.md). Structurally relevant siblings are the four domain
owners — [`pad`](../pad/ANATOMY.md), [`lingtai`](../lingtai/ANATOMY.md),
[`knowledge`](../knowledge/ANATOMY.md), [`skills`](../skills/ANATOMY.md) — which
retain their private composers, catalogs, and lifecycle; this package routes to
their manuals and reads only the two Pad configuration inputs it owns.

## State

The package writes no persistent state. `Agent` owns one narrow ephemeral
`_psyche_settings_snapshot` tuple, initialized to the two meaningful defaults
and replaced only by successful canonical reconstruction; SHOW binds to that
tuple and does not inspect ambient sources. Prompt sections, catalogs, `system/pad.md`,
`system/pad_append.json`, `system/lingtai.md`, `knowledge/`, and `.library/` are
owned elsewhere and untouched by every public Psyche action. Agent-owned full
reconstruction may seed a missing/empty durable Pad before its canonical
composer runs.

## Notes

The kernel-owned prompt section named `substrate` is a different concept in a
disjoint namespace and is unchanged by this family's naming; the old `psyche`
family's actions live in `context`/`system` and are not reachable here. Both
points are owned by the paired Contract. The five manual children share one
loader and settings is the generic SHOW-only child; every child has strict-empty
input. The read-only promise is structural, not conventional.
