---
name: knowledge-contract
contract_version: 3
related_files:
  - src/lingtai/tools/knowledge/__init__.py
  - src/lingtai/tools/knowledge/ANATOMY.md
  - src/lingtai/tools/knowledge/manual/SKILL.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - tests/test_knowledge.py
  - tests/test_tool_family_knowledge_migration_parity.py
  - tests/test_psyche_family.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Knowledge capability contract

`knowledge` is the agent-private durable knowledge capability. It scans the
agent's local `knowledge/` directory for `KNOWLEDGE.md`-bearing entries and
injects a compact catalog into the system prompt. It registers **no**
model-facing tool: since the Psyche consolidation the standalone `knowledge`
root and its `info` / `manual` actions are retired, and guidance for this
domain is served read-only through `psyche(action="knowledge")`. The
implementation lives in `src/lingtai/tools/knowledge/`; the code is the source
of truth.

## Routing Card
Guarded by: [KN001](BEHAVIORS.md#behavior-kn001)


**Use this when:**
- You are editing the private durable knowledge capability.
- You are reviewing the catalog scanner, prompt injection, frontmatter schema,
  legacy migration, or the knowledge/skill boundary.
- You need to verify that knowledge entries can carry private references
  (local paths, mail ids, logs) without violating the skills contract.

**Do not use this for:**
- Skill catalog behavior: read `src/lingtai/tools/skills/CONTRACT.md` (the
  structurally isomorphic, physically separate sibling).
- Code navigation only: read `src/lingtai/tools/knowledge/ANATOMY.md`.
- The public manual-routing envelope: read
  `src/lingtai/tools/psyche/CONTRACT.md`, which owns the one public root.
- Authoring procedures for sharing across agents: write a skill instead.

**Fast paths:** public guidance route -> §Tool surface; private
setup/reconciliation lifecycle -> §Private lifecycle; on-disk layout ->
§Storage; how it differs from skills -> §Knowledge vs skills.

## Scope

- Canonical capability name: `knowledge`.
- Public tool name: none. The former `knowledge` root and its `info` and
  `manual` actions were retired in the Psyche consolidation with no alias,
  wrapper, or compatibility path; those spellings are unknown and fail loudly.
- Former names `library` and `codex` are intentionally not compatibility
  aliases. Old `library`/`codex` capability entries are skipped as unknown
  capabilities; old `library(...)`/`codex(...)` tool calls are unavailable.

`knowledge` means private durable memory: what one agent has learned, decided,
and discovered. `skills` means portable procedure catalog. Knowledge entries
MAY reference public skills; skills MUST NOT depend on private knowledge entry
contents, agent-local paths, mail ids, or other private memory state.

## Knowledge vs skills

The two capabilities are structurally isomorphic but physically separate:

| Aspect | `skills` | `knowledge` |
|---|---|---|
| Root directory | `<agent>/.library/{intrinsic,custom}/` | `<agent>/knowledge/` |
| Manifest file | `SKILL.md` | `KNOWLEDGE.md` |
| Model-facing tool | none — guidance via `psyche(action="skills")` | none — guidance via `psyche(action="knowledge")` |
| Prompt section | protected `skills` (YAML catalog) | protected `knowledge` (YAML catalog) |
| Extra path sources | `manifest.capabilities.skills.paths` | none — strictly per-agent |
| Visibility | portable / shareable | private / agent-owned |
| May reference local paths, mail ids, logs | no | yes |

Shared Markdown catalog mechanics live in `src/lingtai/tools/_catalog.py`; each
capability keeps its own storage root, prompt section, and semantic contract so
private knowledge does not leak into the public skill catalog. Neither
capability registers a tool of its own; both serve guidance through the one
read-only `psyche` root.

## Tool surface

None. This capability registers no model-facing tool, schema, handler, or
action. `src/lingtai/tools/knowledge/__init__.py` exposes no `get_schema`,
`get_description`, `handle`, or `ACTION_ORDER`, and `setup()` mounts nothing
onto the agent's tool surface. `knowledge` is absent from
`_LTP_V2_MIGRATED_FAMILIES` (`src/lingtai/kernel/tool_result_summary.py`), and
it appears in the daemon `EMANATION_BLACKLIST` only as a capability name that
must stay off the borrowable host-tool floor.

The public entry for this domain is the read-only
`psyche(action="knowledge")` call owned by
`src/lingtai/tools/psyche/CONTRACT.md`. It returns the installed knowledge
manual as `{status, manual, manual_path}`; a missing manual returns
`status: "degraded"` with an empty `manual`, the resolved `manual_path`, and an
`error`. The call takes the canonical strict-empty `input`; it creates, edits,
searches, rescans, and loads nothing, and a manual load never reaches the
catalog scanner, the reconciler, or the legacy migration.

The retired surface fails loudly, before any file is read: the old `knowledge`
root, its `info` and `manual` actions, the historical JSON-database actions
(`submit`, `view`, `consolidate`, `delete`), and the former `library`/`codex`
names are all unknown on the one public `psyche` root, with no alias or
compatibility path. There is no in-tool capacity limit; the historical
`knowledge_limit` kwarg is accepted by `setup()` but ignored. The capability
supports no settings file at any level; nothing is configurable and no settings
document is ever read or written.

## Private lifecycle

What survived the retirement is private lifecycle ownership, unchanged:

- `setup()` (the setup/refresh lifecycle entry) runs `_reconcile(agent)`: the
  one-time legacy JSON migration, then catalog composition. It returns the
  health result `{status, knowledge_dir, catalog_size, problems}`; it never
  loads entry bodies into its result and never mutates authored entries.
- `_compose_catalog(agent, *, publish=True)` is the pure read composer: it
  scans `<agent>/knowledge/`, rewrites the protected `knowledge` prompt
  section, and reports the same health shape. With `publish=False` it writes
  the section without flushing, so full-context reconstruction composes every
  canonical section before the one final prompt publication.
- `_reconcile` is the ONLY path that performs the legacy migration, because
  migration writes inside `knowledge/` and renames the legacy source; catalog
  recomposition — including the full-context reconstruction path in
  `Agent._reload_prompt_sections` — deliberately never migrates.
- Neither `_reconcile` nor `_compose_catalog` is reachable from any
  model-facing action.

## Storage

The on-disk layout is:

```text
<agent>/knowledge/
  <entry-1>/
    KNOWLEDGE.md
    scripts/
    assets/
    notes/
  <entry-2>/
    KNOWLEDGE.md
    raw-log.json
  ...
```

Each entry is a directory whose name is the routing handle. The directory must
contain a `KNOWLEDGE.md` file with YAML frontmatter:

```markdown
---
name: <routing handle>
description: <one or more sentences; prompt-visible>
version: <optional>
---

<body — read on demand via `shell`>
```

Required frontmatter fields are `name` and `description`. Entries missing
either are skipped and surfaced in `problems`. Folders without a
`KNOWLEDGE.md` are recursed into so nested namespaces are allowed; folders
with loose files but no `KNOWLEDGE.md` are reported as corrupted.

Entries may carry supporting files (scripts, assets, notes, raw logs,
attachments). Those files are not parsed by the capability; the agent opens
them via `shell` when it loads an entry.

The agent is the sole long-term author of `knowledge/`. The only capability
write is a one-time legacy migration: if `knowledge/knowledge.json` or old
`codex/codex.json` exists, entries are converted to `knowledge/<slug>/KNOWLEDGE.md`,
each legacy `supplementary` field is written to `references/supplementary.md`,
and the source JSON is renamed to `<name>.json.migrated` to prevent repeat
work.

## Prompt injection

On setup/refresh reconciliation (`_reconcile`) and on every full-context
reconstruction (`_compose_catalog`, invoked from `Agent._reload_prompt_sections`
with `publish=False`), the capability rewrites protected prompt section
`knowledge`:

- If there are entries, the section contains a preamble plus a YAML catalog.
  Each entry is rendered as a `- name:` block with `location:` (absolute
  `KNOWLEDGE.md` path) and a `description:` block scalar.
- If there are no entries, the section is cleared.

Only `name`, `description`, and `location` are ever injected. Bodies and
supporting files stay out of the prompt until the agent loads them through the
regular `shell` tool. This mirrors the skills catalog and keeps the always-on
prompt cheap.

## Knowledge / skill directionality

Knowledge entries MAY reference skills by public path/name when an agent has
learned that a skill is useful for a recurring situation.

Skills MUST NOT reference private knowledge entry paths, private agent paths,
mail ids, or agent-local memory state.

Reason: skills are portable shared procedures; knowledge is agent-local
accumulated memory. The dependency direction is knowledge -> skill, never
skill -> private knowledge.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| `knowledge` remains a builtin capability in the registry with empty providers | `src/lingtai/tools/registry.py` | `tests/test_check_caps.py::test_get_all_providers_returns_all_capabilities`, `::test_builtin_capabilities_have_empty_providers` |
| Setup registers no tool and the package exposes no schema/dispatch surface | `src/lingtai/tools/knowledge/__init__.py` | `tests/test_knowledge.py::test_knowledge_setup_registers_no_tool`, `::test_knowledge_package_exposes_no_schema_or_dispatch`, `tests/test_tool_family_knowledge_migration_parity.py::test_knowledge_registers_no_tool_and_is_not_a_summarize_family` |
| The public action inventory for this domain lives on the `psyche` root: `knowledge` present, `info` absent | `src/lingtai/tools/psyche/__init__.py` | `tests/test_knowledge.py::test_knowledge_manual_is_one_psyche_action`, `tests/test_psyche_family.py::test_exact_public_action_inventory` |
| Every retired knowledge action (`submit`, `view`, `consolidate`, `delete`, `info`, …) fails loudly on the psyche root without mutating state | `src/lingtai/tools/psyche/__init__.py` | `tests/test_knowledge.py::test_retired_knowledge_actions_are_rejected_on_the_psyche_root`, `tests/test_psyche_family.py::test_unknown_or_retired_action_is_rejected` |
| `_reconcile` re-scans and returns exact count/problems without loading bodies or mutating entries | `src/lingtai/tools/knowledge/__init__.py` | `tests/test_tool_family_knowledge_migration_parity.py::test_reconcile_rescans_and_reports_exact_count_and_problems`, `::test_reconcile_does_not_load_bodies_or_mutate_entries` |
| Legacy `knowledge/knowledge.json` and `codex/codex.json` migrate once into `KNOWLEDGE.md` folders; `supplementary` becomes `references/supplementary.md`; migration runs only from the setup/refresh lifecycle | `src/lingtai/tools/knowledge/__init__.py` | `tests/test_knowledge.py::test_legacy_knowledge_json_migrates_to_knowledge_md`, `::test_legacy_codex_json_migrates_to_knowledge_md`, `tests/test_tool_family_knowledge_migration_parity.py::test_setup_lifecycle_is_what_runs_the_legacy_migration`, `::test_catalog_recompose_never_runs_the_legacy_migration` |
| Full-context reconstruction recomposes the catalog through the pure `_compose_catalog(publish=False)` composer | `src/lingtai/agent.py` | `tests/test_tool_family_knowledge_migration_parity.py::test_full_context_rebuild_recomposes_the_catalog` |
| The manual is returned through `psyche(action="knowledge")` with no catalog scan, no health fields, and no double wrap | `src/lingtai/tools/psyche/__init__.py`, `src/lingtai/tools/tool_family/manual.py` | `tests/test_tool_family_knowledge_migration_parity.py::test_manual_returns_body_and_path_without_rescanning`, `::test_manual_missing_is_degraded_and_still_no_rescan` |
| Manager-style lookup is exact: `knowledge` resolves and former names produce no tools | `src/lingtai/agent.py`, `src/lingtai/tools/registry.py` | `tests/test_knowledge.py::test_former_alias_capabilities_are_not_tools` |
| Catalog reads `<agent>/knowledge/<name>/KNOWLEDGE.md` and excludes `SKILL.md` entries | `src/lingtai/tools/_catalog.py` | `tests/test_knowledge.py::test_knowledge_md_convention_distinct_from_skill_md` |
| Prompt catalog includes only `name`/`description`/`location` from frontmatter, never bodies or supporting files | `src/lingtai/tools/_catalog.py` | `tests/test_knowledge.py::test_prompt_catalog_only_metadata_not_body` |
| Entries may carry references, scripts, assets, and private local references in the body | filesystem convention | `tests/test_knowledge.py::test_entries_may_have_scripts_and_assets`, `::test_entry_may_reference_local_paths_in_body` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| No model-facing `knowledge` tool, schema, or handler exists | `tests/test_knowledge.py::test_knowledge_setup_registers_no_tool`, `::test_knowledge_package_exposes_no_schema_or_dispatch` | Boot with `capabilities={"knowledge": {}}` and inspect the built tool surface | Retired root silently returns; the model sends dead calls |
| Calling any retired knowledge action fails loudly before any I/O | `tests/test_knowledge.py::test_retired_knowledge_actions_are_rejected_on_the_psyche_root` | Call `psyche(action="info", input={}, reasoning="x")` and read the typed unknown-action error | Removed actions silently no-op; the signpost boundary weakens |
| Skills do not depend on private knowledge | documented invariant; enforce by review | Check shared skill docs for private paths/ids | Shared skills become non-portable |
| Knowledge and skills use distinct manifest filenames | `tests/test_knowledge.py::test_knowledge_md_convention_distinct_from_skill_md` | Drop a `SKILL.md` into `<agent>/knowledge/foo/` and confirm it is not picked up | Physical separation collapses; private/public boundary blurs |
| Body content stays out of prompt catalog | `tests/test_knowledge.py::test_prompt_catalog_only_metadata_not_body` | Author an entry with a long body and inspect the prompt section | Prompt bloat / private detail leakage |
| Legacy migration runs once, from setup/refresh only, never during catalog recomposition | `tests/test_tool_family_knowledge_migration_parity.py::test_setup_lifecycle_is_what_runs_the_legacy_migration`, `::test_catalog_recompose_never_runs_the_legacy_migration` | Place `knowledge/knowledge.json`, run a rebuild, confirm it is untouched; then reconcile and confirm exactly one migration | Repeat migration, or migration mid-reconstruction, loses data |
| Loading the manual never rescans, reconciles, or mutates the catalog | `tests/test_tool_family_knowledge_migration_parity.py::test_manual_returns_body_and_path_without_rescanning` | Author an entry, call `psyche(action="knowledge")`, confirm the prompt section is unchanged | Reading guidance silently rewrites prompt state |

Run before merging knowledge changes:

```bash
python -m pytest tests/test_knowledge.py tests/test_tool_family_knowledge_migration_parity.py tests/test_psyche_family.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, frontmatter
  fields, defaults, and bounds are canonical English literals. Since the
  retirement there is no model-facing schema (`get_schema()`) or description
  (`get_description()`) for this capability and nothing from it reaches the
  provider wire.
- **Glossary resources:** this package still owns `glossary-en.md`,
  `glossary-zh.md`, and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, property name, frontmatter
  field, or user-visible concept requires reviewing all three glossary files
  in the same PR.
- **Validation:** `python -m lingtai.tools.glossary_validator --check`.
