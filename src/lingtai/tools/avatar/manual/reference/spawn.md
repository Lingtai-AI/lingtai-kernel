---
name: avatar-spawn-reference
description: |
  Avatar spawn reference: identity/path gates, shallow/deep payloads,
  mission review, dry-run, confirmation, and prompt inheritance.
version: 1.1.0
last_changed_at: 2026-09-09T11:24:00Z
related_files:
- src/lingtai/tools/avatar/manual/SKILL.md
- src/lingtai/tools/avatar/__init__.py
- src/lingtai/tools/avatar/settings.py
- src/lingtai/tools/avatar/CONTRACT.md
- src/lingtai/tools/psyche/settings.py
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
maintenance: |
  Keep this reference aligned with Avatar spawn validation, serialization, and
  prompt inheritance. Put procedure detail here rather than expanding the
  parent router; preserve the router's settings anchors and direct spawn path.
---

# Avatar spawn and identity

The [Avatar Manual](../SKILL.md) has the direct call and defaults. `action`,
`input`, and root `reasoning` are required. The closed spawn input contains only
`name`, `type`, `comment`, `dry_run`, `confirm`; nullable values use defaults.
Reasoning is the mission/first prompt, never an input property. Do not nest
`reasoning`, `_reasoning`, or `summarize`.

## Canonical identity and path gate

`name` is the public routing identity and sibling-directory basename. Supply
1–64 Unicode letters/digits/underscore/hyphen, with no dots, separators, spaces,
control characters, or leading dot. There is no alternate directory input.
Real spawn checks resolved sibling containment and refuses an existing target
or live ledger peer. Confirmation does not bypass these or launch authority.

## Select the payload

- **Shallow (default):** rewritten `init.json`, narrow Psyche base/covenant
  inputs, and the new comment; it does not copy parent `system/`, knowledge,
  exports, or conversation history.
- **Deep:** also copies `system/`, `knowledge/`, `exports/`, and `combo.json`
  when present. This includes durable character, Pad, summaries and existing
  `system/rules.md`; a fresh conversation does not mean empty durable memory.

Both set `manifest.agent_name`, clear `manifest.admin`, blank the `lingtai`
seed, remove `lingtai_file`, old top-level prompt pairs/brief and top-level
`addons`. This is **not a blanket purge of init fields**: other declarations,
including an explicit `mcp` block, are not removed by this rewrite. Check
integration ownership before authorizing a child that could share a poller.

Only when `manifest.preset.default` is truthy does the rewrite select that
preset as active and remove materialized `manifest.llm` and
`manifest.capabilities` for first-boot materialization. A configuration
without a default retains those fields and its existing active selection.
Relative `default`, `active`, and `allowed` preset paths are re-rooted against
the parent workdir; absolute and tilde-prefixed paths keep their meaning.
Do not infer first-boot capabilities solely from the two ports granted to the
Avatar **tool** binder: those ports are not the child's capability list.

## Mission review, preview, and confirmation

Make the mission actionable: objective, resources, reporting route, done
condition, constraints. A trimmed mission shorter than 20 characters or whose
lowercase text equals a placeholder token, or begins with that token followed
by an **ASCII space**, needs `confirm=true`. Tokens are listed in SHOW's
validation section. This is not arbitrary prefix matching: `testing ...` and
`test ...` differ. Review the returned preview before acknowledging.

`dry_run=true` is a preview, **not launch admission**. It validates name/type,
checks the ledger, and reads parent init/Psyche settings, but returns before
provider admission, resolved-path containment and existing-directory checks.
It may therefore preview a target that a real spawn would refuse. It skips the
mission gate and creates no directory, ledger record, marker, `.prompt`, or
process. Neither preview nor `confirm=true` grants authority.

## Child prompt and comment

`settings/psyche.json` uses Psyche's v1 owner serializer for only base/covenant
pairs plus the new spawn comment. Parent relative pointers are anchored to the
parent workdir. The parent's comment and comment-file pointer are not
inherited; null/empty new comment means none. This persistent note survives
refresh/molt/wake and renders after `meta_guidance` and before `rules`—position,
not precedence. Deep copying does not copy the parent's `settings/` directory.

Parent identity and mission travel in a separate one-time `.prompt` signal,
consumed by the child watcher. The blank `lingtai` seed is not that channel.
Avatar writes no `.rules` signal and distributes no rules. For that protocol,
use `psyche(action="manual", input={}, reasoning="locate rules guidance")`.
