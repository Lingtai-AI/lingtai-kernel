# core/knowledge

Knowledge capability — private durable knowledge across molts. The catalog is
filesystem-backed: each immediate subdirectory of `<agent>/knowledge/` with a
`KNOWLEDGE.md` file is one entry. The frontmatter `name` + `description` are
injected as a compact YAML catalog in the system prompt's
`knowledge` section. Bodies and supporting files are loaded on demand through
the regular `read` tool.

## Components

- `knowledge/__init__.py` — the capability implementation. `_parse_frontmatter`,
  `_scan`, `_build_catalog_yaml`, `_reconcile`, `get_description`, `get_schema`,
  `make_handler`, and `setup` live here. `make_handler(agent)` is the stable
  single-source-of-truth seam: `setup()` registers `make_handler(agent)` via
  `add_tool`, and the SDK bundle bridge `lingtai.core.knowledge_bundle` hosts the
  *same* factory's handler, so the bundle-hosted `knowledge` tool cannot drift
  from the registered one.
- `knowledge/CONTRACT.md` — public behavior contract: tool surface, on-disk
  layout, prompt injection, knowledge/skill directionality, anchored claims,
  and verification matrix.

## Connections

- `lingtai.capabilities` maps builtin capability name `knowledge` here. Former
  `library` and `codex` capability names are not registered.
- `setup()` registers exactly one tool, `knowledge`, with a single `info`
  action. The historical `knowledge_limit` kwarg is accepted and ignored.
- `_reconcile()` writes protected prompt section `knowledge`.
- `lingtai.core.knowledge_bundle` (the wrapper-side SDK bundle bridge, stage 3G)
  injects `make_handler(agent)` into the `lingtai_sdk.knowledge_tools` catalog
  bundle host. Additive only — `setup()` remains the live registration path; the
  bridge installs no guard and changes no dispatch.
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
- `library` and `codex` are gone as durable-memory aliases. This is a breaking
  rename by design.
- The catalog injects only `name`/`description`/`path`. Bodies and supporting
  files never appear in the prompt; the agent loads them via `read`.
- The capability normally never writes inside `<agent>/knowledge/`; the sole
  exception is the one-time legacy JSON migration. After migration, the agent is
  the sole author.
- `SKILL.md` belongs to skills; `KNOWLEDGE.md` belongs to knowledge. The two
  filenames are not aliases.
- For the stable behavior contract, read `src/lingtai/core/knowledge/CONTRACT.md`
  before editing this capability.
