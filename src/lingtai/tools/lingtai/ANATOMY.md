---
related_files:
  - src/lingtai/tools/lingtai/BEHAVIORS.md
  - src/lingtai/tools/lingtai/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/context/ANATOMY.md
  - src/lingtai/tools/lingtai/__init__.py
  - src/lingtai/tools/lingtai/_lingtai.py
  - src/lingtai/tools/lingtai/glossary-en.md
  - src/lingtai/tools/lingtai/glossary-zh.md
  - src/lingtai/tools/lingtai/glossary-wen.md
  - src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
maintenance: |
  Keep paths real, repo-relative, duplicate-free, and reciprocal with the paired
  Contract and parent/neighbor anatomies. Update this graph when symbols,
  connections, state ownership, or composition move.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# tools/lingtai

Mandatory manual-only LTP v2 signpost for the agent's 灵台. Durable character
content lives in `system/lingtai.md`; mutation happens through `shell`, and
active prompt reconstruction belongs to `context.rebuild`.

## Components

- `__init__.py`
  - `ACTION_ORDER = ("manual",)`, `_build_children`, `_FAMILY` — the entire
    public registry and strict schema source.
  - `get_schema`, `get_description` — expose the manual-only LTP v2 envelope and
    route mutations to `shell` plus activation to `context.rebuild`.
  - `handle` — drops intrinsic `_tc_id`, generic-dispatches the manual child,
    flattens its result once, and returns LingTai-shaped unknown-action errors.
  - `boot` — initial private `_lingtai_load` composition only; no lifecycle hook.
- `_lingtai.py`
  - `_lingtai_load` — private canonical writer of the protected `character`
    prompt section from `system/lingtai.md`; empty/missing content deletes the
    section. It has no public mutator.

## Connections and data flow

- `tools/registry.py` wires the root. `tool_family` composes/dispatches the
  strict envelope; `lingtai-manual` is the sole public child payload.
- `Agent._reload_prompt_sections` materializes any authoritative configured
  identity seed and then imports `_lingtai_load`. `Agent._reconstruct_context`
  wraps that all-source composer and is shared by active `context.rebuild`,
  refresh, and molt. Agent owns the one post-molt hook.
- `shell` mutates `system/lingtai.md` without importing this package or
  changing the current prompt.
- Persistent: `system/lingtai.md`. Derived: protected `character` and
  `system/system.md`. The package owns no module-global mutable state.

## Invariants

No public mutation or load action exists. `_lingtai_load` never writes
`covenant` or mechanical `identity`; it is internal composition, not an alias.
Forced configured identity and self-evolve modes remain controlled by Agent's
canonical reconstruction path. See the paired Contract for wire and error
promises.
