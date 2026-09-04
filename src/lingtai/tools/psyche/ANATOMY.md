---
related_files:
  - src/lingtai/tools/psyche/BEHAVIORS.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/psyche/__init__.py
  - src/lingtai/tools/psyche/settings.py
  - src/lingtai/tools/psyche/prompt.py
  - src/lingtai/agent.py
  - src/lingtai/cli_project.py
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/tools/psyche/glossary-en.md
  - src/lingtai/tools/psyche/glossary-zh.md
  - src/lingtai/tools/psyche/glossary-wen.md
  - src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
  - tests/test_psyche_family.py
  - tests/test_psyche_prompt_settings.py
  - tests/test_deep_refresh.py
  - tests/test_project_creation.py
  - tests/test_tool_family_avatar_migration.py
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
read-only manual loaders; the reserved `settings` child returns eight fully
redacted Psyche-owned rows: Pad plus the three configurable prompt pairs. The
package owns the closed `settings/psyche.json` v1 parser and a pure immutable
prompt-plan composer for only those six inputs plus the three static resident
sections; it owns no domain catalog composer. Its static official declaration
binds only `workdir` and the read-only `psyche_settings` snapshot Port. Its
`boot` keeps the mandatory intrinsic lifecycle shim, invokes the domain-owned
composers, then mounts the one public handler through the declared ToolPlugin
registrar.

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
- `settings.py::{serialize_prompt_owner_document,read_resolved_prompt_inputs,build_settings_provider}`
  — owns the one v1 writer, reads the bounded stable closed owner document once
  per reconstruction, resolves its three pairs with the existing helper, and
  structurally copies and validates the Agent's last successfully applied
  eight-value snapshot through `PsycheSettingsPort` into the full-redaction
  provider. SHOW performs no source I/O and receives no Agent.
- `prompt.py::{PromptSectionDefinition,PromptSection,PromptPlan,compose_prompt_plan}`
  — the closed three-entry static section registry and pure composition boundary
  that reads one owner-input candidate plus packaged/fallback section bodies into
  immutable values. It does not write mirrors or touch the kernel prompt manager;
  the Agent applies its candidate transactionally.
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
  `Agent._reload_prompt_sections` reuses. The Pad/LingTai domain packages and
  catalog composers do not import Psyche. The sole reverse composition edge is
  `Agent._reload_prompt_sections` importing this package's prompt-plan composer;
  no Psyche public action reaches that edge.
- Live refresh resolves one immutable Psyche prompt plan immediately after its
  successful init read and before teardown; active rebuild and molt resolve one
  plan before `Agent._reload_prompt_sections`. Reconstruction applies the plan's
  three static sections and overlays only its six configurable values, preserves
  the existing base/covenant mirrors and comment non-mirroring, and commits the
  applied plan plus eight-value snapshot only after the successful final prompt
  flush. A failed candidate restores the prior prompt-manager sections, wrapper
  base prompt, plan mirrors, derived mirrors, and SHOW as one generation.
  `PsycheSettingsPort` exposes only that immutable snapshot to SHOW; no Psyche
  public action reaches reconstruction or rereads a source.
- `cli_project` and Avatar call `serialize_prompt_owner_document`; Project Core
  receives the already-serialized content and knows no Psyche schema keys.

## Composition

Parent: [`tools/ANATOMY.md`](../ANATOMY.md). Paired interface promise:
[`CONTRACT.md`](CONTRACT.md). Structurally relevant siblings are the four domain
owners — [`pad`](../pad/ANATOMY.md), [`lingtai`](../lingtai/ANATOMY.md),
[`knowledge`](../knowledge/ANATOMY.md), [`skills`](../skills/ANATOMY.md) — which
retain their private composers, catalogs, and lifecycle; this package routes to
their manuals and reads only Pad configuration plus its six prompt-owner inputs.

## State

The package writes no persistent state. `Agent` owns one narrow ephemeral
`_psyche_prompt_plan` candidate and one `_psyche_settings_snapshot`, initialized
to empty/default values and replaced only by successful canonical reconstruction;
SHOW binds to the snapshot and does not inspect ambient sources. The
independently user-authored
`settings/psyche.json` is a strict owner source, not package state. Prompt
sections, catalogs, `system/pad.md`,
`system/pad_append.json`, `system/lingtai.md`, `knowledge/`, and `.library/` are
owned elsewhere and untouched by every public Psyche action. Agent-owned full
reconstruction may seed a missing/empty durable Pad before its canonical
composer runs.

## Notes

The prompt section named `substrate` is a different concept from this family's
old public root name; its kernel render slot and content bytes are unchanged.
The old `psyche` family's actions live in `context`/`system` and are not
reachable here. Both points are owned by the paired Contract. The five manual
children share one loader and settings is the generic SHOW-only child; every
child has strict-empty input. The read-only promise is structural, not
conventional.
