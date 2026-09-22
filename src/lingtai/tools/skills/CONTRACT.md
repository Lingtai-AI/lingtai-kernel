---
name: skills-contract
contract_version: 3
related_files:
  - src/lingtai/tools/skills/__init__.py
  - src/lingtai/tools/skills/ANATOMY.md
  - src/lingtai/tools/skills/BEHAVIORS.md
  - src/lingtai/tools/skills/manual/SKILL.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/psyche/ANATOMY.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/kernel/tool_result_summary.py
  - tests/test_skills.py
  - tests/test_psyche_family.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits. This
  capability owns no model-facing ToolFamily; the public manual route is owned
  by the Psyche contract.
---

# Skills capability contract

`skills` is the per-agent, portable skill catalog. It scans the agent's
`.library/{intrinsic,custom}/` plus declared Tier-1 paths, builds a compact YAML
catalog, and injects it into the protected `skills` system-prompt section. It is
pure presentation: it does not author, install, or migrate skill files. The
implementation lives in `src/lingtai/tools/skills/__init__.py`; the code is the
source of truth.

## Routing Card

Guarded by: [SK001](BEHAVIORS.md#behavior-sk001)

**Use this when:**

- You are editing the private catalog scanner, path resolution, prompt
  injection, or refresh lifecycle.
- You need to verify the portable-skills/private-knowledge boundary.
- You are checking the installed Skills manual returned by the public Psyche
  route.

**Do not use this for:**

- Private durable memory: read `src/lingtai/tools/knowledge/CONTRACT.md`.
- Code navigation only: read `src/lingtai/tools/skills/ANATOMY.md`.
- Public manual routing: read `src/lingtai/tools/psyche/CONTRACT.md`, which owns
  the one model-facing root and the `skills` action.
- Shared catalog mechanics: read `src/lingtai/tools/_catalog.py`.

**Fast paths:** public guidance route -> §Public guidance; private
setup/reconciliation -> §Private lifecycle; on-disk layout -> §Storage; the
skills/knowledge boundary -> §Scope.

## Scope

- Canonical capability name: `skills`.
- `skills` means a portable procedure catalog. It MUST NOT depend on private
  knowledge entry contents, agent-local paths, mail ids, or private memory
  state; the dependency direction is knowledge -> skill, never the reverse.
- Former names `library` and `codex` are intentionally not compatibility
  aliases. Old configurations are skipped and their former tools are not
  registered.
- The capability does not create or populate `.library/`, author skills, or
  execute procedures. The Agent initializer installs intrinsic manual bundles;
  the agent or operator authors custom `SKILL.md` files.
- Path sources are `.library/intrinsic/`, `.library/custom/`, and each entry of
  `manifest.capabilities.skills.paths` (absolute, workdir-relative, or
  tilde-prefixed). Registered plugin skill paths follow the plugin-specific
  composition rules in the implementation.

## Public guidance

This package exposes **no model-facing tool**. The former standalone `skills`
root and its `info` / `manual` actions were retired with no alias, wrapper, or
compatibility path. `skills` has no `get_schema`, `get_description`, `handle`, or
`ACTION_ORDER`, is absent from provider tool schemas, and is not a
`ToolFamily` consumer. It is a private capability retained for setup and
catalog reconciliation.

Read-only guidance for this domain is served by the one public
`psyche(action="skills")` call owned by
`src/lingtai/tools/psyche/CONTRACT.md`. Its canonical strict-empty invocation is:

```text
psyche(action="skills", input={}, reasoning="load skills guidance")
```

The public result is `{status, manual, manual_path}`. A missing installed
manual returns `status: "degraded"`, an empty `manual`, the resolved
`manual_path`, and the loader's `error`; it does not return catalog health
fields. Loading the manual reads the installed `SKILL.md` only: it does not scan
or reconcile the catalog, inject a prompt section, or mutate any skill state.
The public `psyche` action inventory is exactly
`pad | lingtai | knowledge | skills | settings | manual`; `info` is not an
accepted action.

The former root is not merely hidden from the provider wire: its package has no
schema or dispatch entry point, and `skills` is not included in the kernel's
`_LTP_V2_MIGRATED_FAMILIES`. Calls must use the `psyche` route.

## Private lifecycle

The capability's private lifecycle remains active and is separate from the
public manual route:

- `setup(agent, paths=...)` calls `_reconcile` with the configured Tier-1 paths.
  It mounts no tool. The Agent initializer installs intrinsic manuals before
  the first catalog refresh; setup itself only scans what is on disk and
  publishes the protected `skills` prompt section.
- `_reconcile(agent, paths, publish=True)` scans intrinsic and custom catalogs,
  resolves declared paths, composes the YAML catalog, and injects it into the
  protected `skills` section. With `publish=False`, it writes the section
  without flushing so full-context reconstruction can publish all sections in
  one final operation.
- The private health result contains `status`, `skills_manual`,
  `library_manual`, `skills_dir`, `library_dir`, `catalog_size`, `paths`, and
  `problems`; it adds `error` when the intrinsic Skills manual is missing. The
  `library_*` names and `.library` path are compatibility names for the storage
  and result shape, not public tool aliases.
- Catalog reconciliation is also used by the Agent's manual-install and full
  context-reconstruction paths. It is never reachable from
  `psyche(action="skills")`; loading guidance has no catalog side effect.
- The capability performs no legacy JSON migration and does not create
  directories or copy files. Missing or malformed entries are reported in the
  private health result while valid entries still contribute to the catalog.

## Storage

The per-agent layout remains:

```text
<agent>/.library/
  intrinsic/
    capabilities/<cap>/SKILL.md   # manuals installed by the initializer
    addons/<addon>/
  custom/                          # agent-authored skills
```

Each catalog entry is a directory containing `SKILL.md` with YAML frontmatter
that includes `name` and `description`. Entries missing either field are
reported in `problems`; the body is not injected into the prompt. Additional
configured paths are scanned recursively, except that a path containing its
own `SKILL.md` is treated as one skill. Each path report records its resolved
path, existence, and valid-skill count.

The shared `scan_markdown_catalog`, `parse_markdown_catalog_file`, and
`build_catalog_yaml` helpers in `src/lingtai/tools/_catalog.py` provide the
catalog mechanics. Only frontmatter metadata (`name`, `description`, and
`location`) is placed in the protected prompt section. Supporting files remain
on disk until an agent explicitly reads them.

## Prompt and path invariants

- `_resolve_path` expands `~`, leaves absolute paths absolute, and resolves
  relative paths against `agent._working_dir`.
- `_reconcile` writes the protected `skills` section through
  `agent.update_system_prompt("skills", ..., protected=True)` for normal
  publication. Its `publish=False` branch writes the section through the prompt
  manager and marks token decomposition dirty for reconstruction.
- The on-disk root remains `.library` even though the capability and prompt
  section are named `skills`.
- Empty catalogs clear the protected section. Catalog bodies and supporting
  files stay out of the prompt.
- The initializer installs the intrinsic `skills/manual/SKILL.md` at
  `.library/intrinsic/capabilities/skills/SKILL.md`; if it is absent,
  reconciliation reports degraded health and the public Psyche loader reports
  its own degraded manual result.

## Skills and knowledge boundary

| Aspect | `skills` | `knowledge` |
|---|---|---|
| Root directory | `<agent>/.library/{intrinsic,custom}/` | `<agent>/knowledge/` |
| Manifest file | `SKILL.md` | `KNOWLEDGE.md` |
| Model-facing tool | none — guidance via `psyche(action="skills")` | none — guidance via `psyche(action="knowledge")` |
| Prompt section | protected `skills` YAML catalog | protected `knowledge` YAML catalog |
| Extra path sources | `manifest.capabilities.skills.paths` | none — strictly per-agent |
| Visibility | portable / shareable | private / agent-owned |

Knowledge entries may reference skills by public path/name. Skills MUST NOT
reference private knowledge entries, private agent paths, mail ids, or other
agent-local memory state.

## Anchored claims

| Claim | Source | Test |
|---|---|---|
| The capability remains registered/configurable while producing no public tool | `src/lingtai/tools/registry.py`, `skills/__init__.py` | `tests/test_skills.py::test_skills_registers_no_public_tool`, `::test_skills_capability_remains_registered_and_configurable` |
| The package exposes no schema or dispatch surface | `src/lingtai/tools/skills/__init__.py` | `tests/test_skills.py::test_skills_package_exposes_no_schema_or_dispatch` |
| The public manual is returned only through Psyche | `src/lingtai/tools/psyche/__init__.py` | `tests/test_skills.py::test_manual_body_is_returned_by_psyche`, `tests/test_psyche_family.py::test_each_action_returns_its_intended_manual` |
| Loading the manual does not reconcile or mutate the catalog | `src/lingtai/tools/psyche/__init__.py`, `skills/__init__.py` | `tests/test_skills.py::test_psyche_skills_manual_has_no_catalog_side_effect` |
| Private reconciliation injects the catalog and reports exact path/problem health | `src/lingtai/tools/skills/__init__.py` | `tests/test_skills.py::test_catalog_injected_into_skills_section`, `::test_reconcile_result_keys_and_health_are_exactly_preserved` |
| Missing intrinsic manual degrades rather than failing setup | `src/lingtai/tools/skills/__init__.py` | `tests/test_skills.py::test_info_reports_degraded_when_intrinsic_missing`, `::test_manual_degrades_with_exact_loader_message` |
| The root is absent from both provider wires and is not a summarize family | provider schema/summary code | `tests/test_skills.py::test_no_skills_root_reaches_either_provider_wire`, `::test_skills_is_no_longer_an_ltp_v2_summarize_family` |
| Exact Psyche action inventory includes `skills` and excludes former roots/actions | `src/lingtai/tools/psyche/__init__.py` | `tests/test_psyche_family.py::test_exact_public_action_inventory`, `::test_unknown_or_retired_action_is_rejected` |
| Declared paths resolve and catalog frontmatter is validated | `skills/__init__.py`, `_catalog.py` | `tests/test_skills.py::test_skills_scans_absolute_path`, `::test_skills_resolves_relative_path_from_working_dir`, `tests/test_catalog_helpers.py::test_scan_recurses_and_sorts` |
| Former `library`/`codex` configurations do not register legacy tools | registry and capability setup | `tests/test_skills.py::test_former_library_config_does_not_register_library_tool`, `::test_former_codex_library_pair_does_not_register_legacy_tools` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| No model-facing `skills` root exists | `tests/test_skills.py::test_skills_registers_no_public_tool` | Boot with `capabilities={"skills": {}}` and inspect tool handlers/schemas | Retired root silently returns |
| Guidance is reachable at Psyche and has no catalog side effect | `tests/test_skills.py::test_manual_body_is_returned_by_psyche`, `::test_psyche_skills_manual_has_no_catalog_side_effect` | Call `psyche(action="skills", input={}, reasoning="x")` before and after adding a skill | Manual loading mutates prompt state |
| Private reconciliation remains active | `tests/test_skills.py::test_catalog_injected_into_skills_section` | Run setup/rebuild and inspect the protected `skills` section | Skills disappear from the model context |
| Invalid frontmatter is reported while valid entries remain | `tests/test_skills.py::test_info_surfaces_problems` | Add a malformed `SKILL.md` and reconcile | One bad entry hides the catalog |
| Portable skills do not depend on private knowledge | documented invariant; enforce by review | Inspect skill bodies for private paths/ids | Shared procedures leak agent state |

Run before merging Skills changes:

```bash
python -m pytest tests/test_skills.py tests/test_validate_skill.py tests/test_catalog_helpers.py -q
```

## Glossary ownership

This package owns `glossary-en.md`, `glossary-zh.md`, and `glossary-wen.md`.
Each has strict YAML frontmatter (`kind: tool-glossary`, `schema_version: 1`,
`tool_package: tools.<pkg>`, `language: <lang>`). Canonical identifiers and
frontmatter fields stay in English; localized glossary text never creates
aliases. Changing a user-visible identifier or concept requires reviewing all
three glossary files. Validate with:

```bash
python -m lingtai.tools.glossary_validator --check
```
