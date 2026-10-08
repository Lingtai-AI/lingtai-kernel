---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/knowledge/BEHAVIORS.md
  - src/lingtai/tools/knowledge/CONTRACT.md
  - src/lingtai/tools/knowledge/__init__.py
  - src/lingtai/tools/knowledge/manual/SKILL.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/CONTRACT.md
  - tests/test_knowledge.py
  - tests/test_tool_family_knowledge_migration_parity.py
  - src/lingtai/tools/knowledge/glossary-en.md
  - src/lingtai/tools/knowledge/glossary-zh.md
  - src/lingtai/tools/knowledge/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# core/knowledge

Knowledge capability — private durable knowledge across molts. The catalog is
filesystem-backed: each immediate subdirectory of `<agent>/knowledge/` with a
`KNOWLEDGE.md` file is one entry. The frontmatter `name` + `description` are
injected as a compact YAML catalog in the system prompt's
`knowledge` section. Bodies and supporting files are loaded on demand through
`shell` (bounded reads).

## Components

- `knowledge/__init__.py` — the capability implementation. It owns legacy JSON
  migration, `_reconcile`, `_knowledge_manual`, the `ToolFamily` composition
  (`_EMPTY_INPUT_SCHEMA`, `_CHILD_SPECS`, one `_build_family(agent | None)`),
  `get_description`, `get_schema`, `handle`, and `setup`, and imports shared
  Markdown-catalog scanning/rendering from `src/lingtai/tools/_catalog.py`.
- `tools/tool_family/` — the generic, optional LTP v2 composition/dispatch
  infrastructure this capability adopts for its schema and dispatch
  (`src/lingtai/tools/tool_family/ANATOMY.md`).
- `src/lingtai/tools/_catalog.py` — shared frontmatter parser, recursive Markdown catalog
  scanner, and YAML catalog renderer used by both `knowledge` and `skills`.
- `knowledge/CONTRACT.md` — public behavior contract: tool surface, on-disk
  layout, prompt injection, knowledge/skill directionality, anchored claims,
  and verification matrix.

## Connections

- `lingtai.tools.registry` maps builtin capability name `knowledge` here. Former
  `library` and `codex` capability names are not registered.
- `setup()` registers exactly one tool, `knowledge`, as one LTP v2 family with
  the two public actions `info` and `manual`. The historical `knowledge_limit`
  kwarg is accepted and ignored.
- `handle()` is the Host layer: it dispatches through the family and normalizes
  the generic `ACTION_REQUIRED` envelope failure back to knowledge's exact
  pre-migration unknown-action result.
- `_reconcile()` writes protected prompt section `knowledge`.
- `kernel/tool_result_summary.py` lists `knowledge` in
  `_LTP_V2_MIGRATED_FAMILIES`, so root `summarize` is the canonical a-priori
  summary control for this tool and its `status: "failed"` envelope errors are
  never summarized.
- `skills/` is the structurally isomorphic, physically separate sibling
  capability — it owns `<agent>/.library/{intrinsic,custom}/<name>/SKILL.md`,
  knowledge owns `<agent>/knowledge/<name>/KNOWLEDGE.md`. Two separate
  modules, two separate tools, two separate prompt sections.

## State

- Root path: `<agent>/knowledge/`.
- Entry layout: `<agent>/knowledge/<name>/KNOWLEDGE.md` plus arbitrary
  supporting files (scripts, assets, notes, raw logs).
- Required frontmatter: `name`, `description`. Optional: `version`.
- Prompt state: protected `knowledge` section holds the preamble + YAML catalog
  (one `- name:` block per entry, with `location:` and `description:` fields).
- No JSON store and no per-entry size cap. A one-time legacy migration
  converts `knowledge/knowledge.json` and old `codex/codex.json` entries into `KNOWLEDGE.md` folders, writes old `supplementary` text to `references/supplementary.md`, and renames the source JSON to `<name>.json.migrated`.

## Invariants

- `knowledge` is private, agent-owned memory. It is not the public skill
  catalog.
- The tool is a signpost only: no action creates, edits, searches, or loads
  knowledge entries. Both children declare a strict-empty `input`, so there is
  no field through which an authoring payload could be smuggled.
- `info` re-scans/reconciles the catalog and returns health
  (`knowledge_dir`/`catalog_size`/`problems`) without loading bodies. `manual`
  returns the current manual body/path and never rescans or mutates.
- `library` and `codex` are gone as durable-memory aliases. This is a breaking
  rename by design.
- The catalog injects only `name`/`description`/`path`. Bodies and supporting
  files never appear in the prompt; the agent loads them via `read`.
- The capability normally never writes inside `<agent>/knowledge/`; the sole
  exception is the one-time legacy JSON migration. After migration, the agent is
  the sole author.
- `SKILL.md` belongs to skills; `KNOWLEDGE.md` belongs to knowledge. The two
  filenames are not aliases.
- For the stable behavior contract, read `src/lingtai/tools/knowledge/CONTRACT.md`
  before editing this capability.
