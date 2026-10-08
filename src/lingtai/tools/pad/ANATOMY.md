---
related_files:
  - src/lingtai/tools/pad/BEHAVIORS.md
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/tool_family/ANATOMY.md
  - src/lingtai/tools/context/ANATOMY.md
  - src/lingtai/tools/pad/__init__.py
  - src/lingtai/tools/pad/_pad.py
  - src/lingtai/tools/pad/glossary-en.md
  - src/lingtai/tools/pad/glossary-zh.md
  - src/lingtai/tools/pad/glossary-wen.md
  - src/lingtai/intrinsic_skills/pad-manual/SKILL.md
maintenance: |
  Keep paths real, repo-relative, duplicate-free, and reciprocal with the paired
  Contract and parent/neighbor anatomies. Update this graph when symbols,
  connections, state ownership, or composition move.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# tools/pad

Mandatory LTP v2 family for pinned Pad reference persistence. Public actions are
`append | manual`; body mutation happens through `shell`, and active prompt
reconstruction is owned by `context.rebuild`.

## Components

- `__init__.py`
  - `_APPEND_INPUT_SCHEMA`, `_CHILD_SPECS`, `ACTION_ORDER` — the sole stateful
    child and derived exact public action set.
  - `_build_children`, `_FAMILY`, `get_schema`, `get_description` — strict LTP
    v2 schema/dispatch composition plus model routing prose.
  - `handle` — drops intrinsic `_tc_id`, dispatches, adapts the manual result,
    and emits Pad-shaped unknown-action errors.
  - `boot` — initial internal `_pad_load` composition only; it registers no
    lifecycle hook.
- `_pad.py`
  - `_load_append_list`, `_save_append_list`, `_resolve_path`,
    `_read_append_content`, `_is_text_file` — persisted-list and validation
    helpers.
  - `_pad_append` — query/set/clear `system/pad_append.json`; validates text and
    the 100k-token ceiling, persists, and explicitly does **not** compose or
    flush the current prompt.
  - `_pad_load` — private canonical composer of `system/pad.md` plus pinned
    reference contents into the derived `pad` prompt section.

## Connections and data flow

- Registry wiring: `tools/registry.py` installs the public `pad` intrinsic;
  `kernel/tool_result_summary.py` and `tools/daemon` carry its cross-cutting
  allowlist/blacklist boundaries.
- Public dispatch flows through `tool_family`; manual content comes from
  `pad-manual`.
- `Agent._reload_prompt_sections` imports `_pad_load` and is itself wrapped by
  `Agent._reconstruct_context`, the one full reconstruction path shared by
  active `context.rebuild`, refresh, and molt. Agent registers that one method as
  the post-molt hook before a fresh session is created.
- `shell` mutates `system/pad.md` without calling this package or changing
  prompt state.
- Persistent: `system/pad.md`, `system/pad_append.json`, referenced text files.
  Derived: prompt section `pad`, `system/system.md`. No module-global mutable
  state.

## Invariants

The public registry contains no `edit` or `load`; `_pad_load` is internal only.
`append` never calls `_pad_load`. Durable changes become prompt-visible only
through the shared full reconstruction path. See the paired Contract for wire,
result, and validation promises.
