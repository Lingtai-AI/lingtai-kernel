---
name: skills-contract
tool: skills
contract_version: 1
related_files:
  - src/tools/skills/__init__.py
  - src/tools/skills/ANATOMY.md
  - src/tools/_catalog.py
maintenance: |
  Keep related_files as repo-relative paths to real files. If behavior and this
  contract disagree, the code is the source of truth — fix the contract in the
  same change and bump contract_version on breaking contract edits.
---

# Skills capability contract

`skills` is the per-agent, portable skill catalog. It scans the agent's
`.library/{intrinsic,custom}/` plus any declared Tier-1 paths, builds a compact
YAML catalog, and injects it into the protected `skills` system-prompt section.
It is pure presentation: it never writes to `.library/`. The implementation lives
in `src/tools/skills/__init__.py`; the code is the source of truth.

## Routing Card

**Use this when:**
- You are editing the catalog scanner, path resolution, prompt injection, or the
  `info` / `manual` action split.
- You need to verify the skills/knowledge boundary (portable procedures vs.
  private durable memory).

**Do not use this for:**
- Private durable memory: read `src/tools/knowledge/CONTRACT.md` (the
  structurally isomorphic, physically separate sibling).
- Code navigation only: read `src/tools/skills/ANATOMY.md`.
- Shared Markdown catalog mechanics: read `src/tools/_catalog.py`.

**Fast paths:** tool schema -> §Tool surface; on-disk layout & path sources ->
§State & storage; skills vs knowledge -> §Scope.

## Scope

- Canonical capability / tool name: `skills`.
- Former names `library` and `codex` are intentionally NOT compatibility aliases;
  old configs are skipped and their tools are not registered.
- `skills` means the portable procedure catalog. Skills MUST NOT depend on
  private knowledge entry contents, agent-local paths, mail ids, or private
  memory state; the dependency direction is knowledge → skill, never the reverse.
- Non-goals: it does not create or populate `.library/` (the Agent initializer's
  `_install_intrinsic_manuals` does that), and it does not author skills — the
  agent writes `SKILL.md` files with `write`/`edit`.
- Path sources scanned: `.library/intrinsic/`, `.library/custom/`, and each entry
  of `manifest.capabilities.skills.paths` (absolute, workdir-relative, or
  tilde-prefixed).

## Tool surface

Schema requires `action`; the handler is `handle_skills` (dispatched via
`dispatch_action`). Exactly two actions.

| Action | Required inputs | Optional inputs | Success output | Error shapes |
|---|---|---|---|---|
| `info` | `action="info"` | — | reconciles catalog, re-injects prompt, returns `{status, skills_dir, library_dir, catalog_size, paths, problems}` (manual body omitted) | see below |
| `manual` | `action="manual"` | — | `{status: "ok", skills_manual, library_manual, manual_path}` — manual body without refreshing catalog | degraded shape below |

Status is `"ok"` normally; `info` and `manual` return `status: "degraded"` (with
an `error` string and empty manual body) when the skills manual
(`.library/intrinsic/capabilities/skills/SKILL.md`) is missing. `library_manual`
and `library_dir` are back-compat keys mirroring the `skills_*` keys; the on-disk
directory remains `.library`.

**Error shapes** (plain dicts):
- Unknown action: `{"status": "error", "message": "unknown action: <action>, only 'info' or 'manual' is supported"}`.

## State & storage

The capability reads (never writes) the per-agent skill store:

```text
<agent>/.library/
  intrinsic/
    capabilities/<cap>/SKILL.md      # manuals installed by the initializer
    addons/<addon>/
  custom/                            # agent-authored skills (kernel never touches)
```

Plus each `manifest.capabilities.skills.paths` entry, scanned recursively. Each
skill is a directory containing a `SKILL.md` with `name` + `description`
frontmatter; entries missing either are surfaced in `problems`. Only
`name`/`description`/`path` are injected into the prompt — bodies stay on disk
until read. Scanning uses the shared `scan_markdown_catalog` /
`build_catalog_yaml` helpers in `src/tools/_catalog.py`.

## Cross-platform invariants

Do not change any of the following; documented for reviewers only.

- **Path handling:** `_resolve_path` expands `~`, uses absolute paths as-is, and
  resolves relative declared paths against `agent._working_dir`. The on-disk root
  stays `.library` for compatibility even though the tool/keys are named `skills`.
- **Prompt injection:** the catalog is written to the protected `skills` section
  via `agent.update_system_prompt("skills", ..., protected=True)`; empty catalog
  clears the section.
- **Encoding:** `SKILL.md` bodies are read as UTF-8.

## Anchored claims

| Claim | Source `src/tools/skills/...` | Test |
|---|---|---|
| Unknown actions return a `{status: error}` dict | `__init__.py` (`handle_skills`) | `tests/test_skills.py::test_unknown_action_returns_error` |
| `info` omits the manual body | `__init__.py` (`_skills_info`) | `tests/test_skills.py::test_info_omits_skills_manual_body` |
| `manual` returns the skills manual body | `__init__.py` (`_skills_manual`) | `tests/test_skills.py::test_manual_returns_skills_manual_body` |
| Missing intrinsic manual reports `degraded` | `__init__.py` (`_reconcile`) | `tests/test_skills.py::test_info_reports_degraded_when_intrinsic_missing` |
| Declared paths resolve (absolute / relative / `~`) | `__init__.py` (`_resolve_path`) | `tests/test_skills.py::test_skills_scans_absolute_path`, `::test_skills_resolves_relative_path_from_working_dir`, `::test_skills_expands_tilde` |
| Catalog is injected into the `skills` prompt section | `__init__.py` (`_reconcile`) | `tests/test_skills.py::test_catalog_injected_into_skills_section` |
| Former `library`/`codex` configs do not register legacy tools | `__init__.py` / registry | `tests/test_skills.py::test_former_library_config_does_not_register_library_tool` |
| Catalog scan/parse helpers behave per spec | `src/tools/_catalog.py` | `tests/test_catalog_helpers.py::test_scan_recurses_and_sorts` |
| `SKILL.md` frontmatter validation | `src/tools/_catalog.py` | `tests/test_validate_skill.py` |

## Verification matrix

| Invariant | Automated test | Manual check | Risk if broken |
|---|---|---|---|
| Only `info` / `manual` are accepted | `tests/test_skills.py::test_unknown_action_returns_error` | Call `skills(action="foo")` | Silent mis-dispatch |
| Catalog reaches the prompt | `tests/test_skills.py::test_catalog_injected_into_skills_section` | Boot with a custom skill, inspect `skills` prompt section | Skills invisible to the model |
| Body stays out of prompt | `tests/test_catalog_helpers.py::test_build_catalog_yaml_golden` | Author a long-body skill, inspect prompt | Prompt bloat |
| Missing manual is degraded not fatal | `tests/test_skills.py::test_info_reports_degraded_when_intrinsic_missing` | Remove the intrinsic manual, call `info` | Boot failure vs. graceful degrade |
| Legacy names do not register | `tests/test_skills.py::test_former_library_config_does_not_register_library_tool` | Boot an old `library` manifest, inspect tools | Half-applied rename confuses model |

Run before merging:

```bash
python -m pytest tests/test_skills.py tests/test_validate_skill.py tests/test_catalog_helpers.py -q
```

## Schema and glossary ownership

- **Canonical identifiers:** function names, JSON property names, action/enum
  values, required fields, defaults, and bounds are canonical English literals.
  The schema (`get_schema()`) and description (`get_description()`) are
  language-independent; the optional `lang` argument is accepted for source
  compatibility but ignored.
- **Provider wire:** provider adapters send the global `WIRE_TOOL_DESCRIPTION`
  constant as the top-level tool description; `FunctionSchema.description`
  holds the full canonical prose rendered into `## tools`.
- **Glossary resources:** this package owns `glossary-en.md`, `glossary-zh.md`,
  and `glossary-wen.md`. Each has strict YAML frontmatter
  (`kind: tool-glossary`, `schema_version: 1`, `tool_package: tools.<pkg>`,
  `language: <lang>`). English body is empty; zh/wen bodies contain concise
  terminology mappings that quote immutable English identifiers and never offer
  localized aliases.
- **Fallback:** exact normalized language lookup, then English, then no
  appendix. Fail-closed for localized text; fail-open for tool availability.
- **Update triggers:** changing a function name, action/enum value, property
  name, or user-visible concept requires reviewing all three glossary files in
  the same PR.
- **Validation:** `python -m tools.glossary_validator --check`.
