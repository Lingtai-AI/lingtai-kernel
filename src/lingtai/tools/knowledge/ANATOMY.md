---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/agent.py
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/_catalog.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/knowledge/BEHAVIORS.md
  - src/lingtai/tools/knowledge/CONTRACT.md
  - src/lingtai/tools/knowledge/__init__.py
  - src/lingtai/tools/knowledge/manual/SKILL.md
  - src/lingtai/tools/psyche/ANATOMY.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/registry.py
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/CONTRACT.md
  - tests/test_knowledge.py
  - tests/test_tool_family_knowledge_migration_parity.py
  - tests/test_psyche_family.py
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
injected as a compact YAML catalog in the system prompt's `knowledge` section.
Bodies and supporting files are loaded on demand through `shell` (bounded
reads). The capability registers no model-facing tool; guidance for this
domain is served read-only by `psyche(action="knowledge")`.

## Components

- `knowledge/__init__.py` — the capability implementation. It owns the legacy
  JSON migration helpers, the pure catalog composer `_compose_catalog`
  (`__init__.py:239-273`), the setup/refresh reconciler `_reconcile`
  (`__init__.py:276-291`), and the lifecycle entry `setup` (`__init__.py:294-310`).
  It exposes no `get_schema`/`get_description`/`handle`/`ACTION_ORDER` and
  imports shared Markdown-catalog scanning/rendering from
  `src/lingtai/tools/_catalog.py`.
- `knowledge/manual/` — the installed knowledge manual (`manual/SKILL.md`),
  the body `psyche(action="knowledge")` returns.
- `src/lingtai/tools/_catalog.py` — shared frontmatter parser, recursive
  Markdown catalog scanner, and YAML catalog renderer used by both `knowledge`
  and `skills`.
- `knowledge/CONTRACT.md` — behavior contract: routing, storage, prompt
  injection, knowledge/skill directionality, anchored claims, and the
  verification matrix.

## Connections

- `lingtai.tools.registry` maps builtin capability name `knowledge` here
  (`registry.py:103`); it is in `CORE_DEFAULTS` and registers no tool. Former
  `library` and `codex` capability names are not registered.
- `setup()` registers nothing; it runs `_reconcile` (one-time legacy migration
  plus catalog compose) and accepts and ignores the historical `knowledge_limit`
  kwarg.
- `Agent._reload_prompt_sections` calls `_compose_catalog(publish=False)`
  during full active/passive reconstruction, so `context.rebuild` and
  refresh/molt recompose this catalog with every other canonical section
  before the one final prompt publication (`src/lingtai/agent.py:2720-2722`).
  That path is pure read; the legacy migration is deliberately not reachable
  from it (`agent.py:2697-2700`).
- [`../psyche/ANATOMY.md`](../psyche/ANATOMY.md) owns the one public root; its
  `knowledge` action loads this package's installed manual and never touches
  the catalog or its composer.
- The daemon capability keeps `knowledge` in `EMANATION_BLACKLIST` as a
  capability name, so no emanation borrows it onto the host-tool floor
  (`../daemon/__init__.py:557-576`). `knowledge` is not in
  `kernel/tool_result_summary.py`'s `_LTP_V2_MIGRATED_FAMILIES` — with no tool
  there is no root `summarize` spelling to recognize.
- `skills/` is the structurally isomorphic, physically separate sibling
  capability — it owns `<agent>/.library/{intrinsic,custom}/<name>/SKILL.md`,
  knowledge owns `<agent>/knowledge/<name>/KNOWLEDGE.md`. Two separate
  modules, two separate prompt sections, neither registering a tool.
- The generic [`../tool_family/`](../tool_family/ANATOMY.md) owns the reusable
  LTP v2 composition/dispatch infrastructure. This package no longer builds a
  `ToolFamily`; only `psyche` does.

## State

- Root path: `<agent>/knowledge/`.
- Entry layout: `<agent>/knowledge/<name>/KNOWLEDGE.md` plus arbitrary
  supporting files (scripts, assets, notes, raw logs).
- Required frontmatter: `name`, `description`. Optional: `version`.
- Prompt state: protected `knowledge` section holds the preamble + YAML
  catalog (one `- name:` block per entry, with `location:` and `description:`
  fields).
- No JSON store and no per-entry size cap. A one-time legacy migration
  converts `knowledge/knowledge.json` and old `codex/codex.json` entries into
  `KNOWLEDGE.md` folders, writes old `supplementary` text to
  `references/supplementary.md`, and renames the source JSON to
  `<name>.json.migrated`.

## Invariants

- `knowledge` is private, agent-owned memory. It is not the public skill
  catalog.
- There is no model-facing surface at all: no tool, schema, handler, or action
  creates, edits, searches, rescans, or loads knowledge entries. The only
  public route is the read-only `psyche(action="knowledge")` manual loader.
- `_reconcile` re-scans/reconciles the catalog and returns health
  (`knowledge_dir`/`catalog_size`/`problems`) without loading bodies; the
  legacy migration it may run is the capability's only write inside
  `knowledge/`.
- `library` and `codex` are gone as durable-memory aliases. This is a breaking
  rename by design.
- The catalog injects only `name`/`description`/`location`. Bodies and
  supporting files never appear in the prompt; the agent loads them via
  `shell`.
- The capability normally never writes inside `<agent>/knowledge/`; the sole
  exception is the one-time legacy JSON migration. After migration, the agent
  is the sole author.
- `SKILL.md` belongs to skills; `KNOWLEDGE.md` belongs to knowledge. The two
  filenames are not aliases.
- For the stable behavior contract, read
  `src/lingtai/tools/knowledge/CONTRACT.md` before editing this capability.
