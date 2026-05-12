# core/knowledge

Knowledge capability — private durable knowledge across molts. Entry id + title
+ summary are injected into the `knowledge` prompt section; full content and
supplementary material load on demand through `knowledge(view, ids=[...])`.

## Components

- `knowledge/__init__.py` — the capability implementation. `get_description`,
  `get_schema`, `KnowledgeManager`, and `setup` live here.
- `knowledge/CONTRACT.md` — public behavior contract for the implementation,
  including the breaking rename, knowledge/skill directionality, persistence,
  prompt behavior, anchored claims, and verification matrix.

## Connections

- `lingtai.capabilities` maps builtin capability name `knowledge` here. Former
  `library` and `codex` capability names are not registered.
- `setup()` registers exactly one tool, `knowledge`, on the manager handler.
- `_inject_catalog()` writes protected prompt section `knowledge`.
- `skills/` is a sibling capability and remains the portable procedure catalog;
  knowledge may reference public skills, but skills must not reference private
  knowledge entries or agent-local memory.

## State

- Store path: `<agent>/knowledge/knowledge.json`.
- File shape: `{"version": 1, "entries": [...]}`.
- Prompt state: `knowledge` section contains the compact catalog.
- Capacity: `DEFAULT_MAX_ENTRIES = 50`; override with `knowledge_limit`.

## Invariants

- `knowledge` is private, agent-owned memory. It is not the public skill catalog.
- `library` and `codex` are gone as durable-memory aliases. This is a breaking
  rename by design.
- Full `content` and `supplementary` are never injected into the prompt catalog;
  callers must use `view` for depth.
- For the stable behavior contract, read `src/lingtai/core/knowledge/CONTRACT.md`
  before editing this capability.
