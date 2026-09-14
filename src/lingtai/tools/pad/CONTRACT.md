---
name: pad-contract
tool: pad
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/pad/ANATOMY.md
  - src/lingtai/tools/pad/__init__.py
  - src/lingtai/tools/pad/_pad.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/intrinsic_skills/pad-manual/SKILL.md
  - tests/test_context_ownership_redesign.py
  - tests/test_pad_lingtai_split.py
  - tests/test_pad.py
maintenance: |
  This component contract is governed by root CONTRACT.md. Keep the paired
  ANATOMY.md, manual, schemas, results, glossary resources, and focused tests in
  sync. Bump contract_version for breaking public changes. Version 2 removes
  public edit/load and makes append persistence explicitly non-hot-loading.
---

# Pad capability contract

## Purpose and ownership
Guarded by: [PD001](BEHAVIORS.md#behavior-pd001)


`pad` is a mandatory, model-visible LTP v2 family for the durable pinned
reference list associated with the agent's prompt sketchboard. Its public action
inventory is exactly `append | manual`.

Durable mutation of `system/pad.md` happens through `shell`: create or
overwrite the whole file, or perform an exact text replacement after verifying
the old text exists exactly once, then read the file back to verify.

No filesystem mutation may reload or otherwise mutate the current prompt. The
retired public `pad.edit` and `pad.load` actions have no alias or compatibility
path and fail as unknown Pad actions.

## Public port

The strict root envelope is exactly `action`, `input`, `reasoning`, and optional
root `summarize`, with `additionalProperties: false`; `action`, `input`, and
`reasoning` are required. Root `summarize` is Host presentation only and never
reaches a child.

| Action | Input | Result |
|---|---|---|
| `append` | strict `{files}`; array/null; `[]` clears, null queries | set/clear/query receipt containing `status`, `files`, `count`, `prompt_reload: false`, and `takes_effect`; set/clear also has `action` |
| `manual` | strict `{}` | flat `{status, manual, manual_path}` (+ degraded `error`) |

`append` validates every supplied path as an existing UTF-8 text file and checks
the aggregate 100,000-token limit **before** persisting
`system/pad_append.json`. It never invokes `_pad_load`, flushes a prompt, or
changes a prompt-manager section. Its changes become active only through a
later explicit `context.rebuild` or passive refresh/molt reconstruction.

The list query is also non-mutating and carries the same delayed-effect fields so
results cannot imply a hot load.

## Internal composition

`_pad_load` is private, not a public action. It is the canonical Pad composer
reused by `Agent._reload_prompt_sections`, which is wrapped by
`Agent._reconstruct_context`. It reads `system/pad.md` plus every persisted
append reference into the derived `pad` prompt section. `boot(agent)` performs
initial internal composition only; it does not register a post-molt hook.
Agent owns exactly one post-molt hook, `_reconstruct_context`, shared with
refresh and active `context.rebuild`.

## Contract rules

- Schema and dispatch derive from the same child registry; action/input
  correlation survives Chat and Responses adapters.
- Unknown actions, including `edit` and `load`, fail loudly before I/O. Unknown
  root/input fields, non-object input, and non-boolean root `summarize` produce
  LTP v2 `INVALID_ARGUMENT` failures.
- `_tc_id`, `reasoning`, and root `summarize` never become child input.
- The manual is `pad-manual`, flattened once after generic dispatch.
- `pad` remains in `_LTP_V2_MIGRATED_FAMILIES` and `EMANATION_BLACKLIST`.
- No Pad settings file exists.

## State and evidence

Persistent state is `system/pad.md` and `system/pad_append.json`; `system/pad.md`
is mutated only through `shell` — there is no Pad-owned body writer. The prompt `pad` section and
`system/system.md` are derived by the private `_pad_load` composer.

Focused evidence:

```bash
python -m pytest -q tests/test_context_ownership_redesign.py \
  tests/test_pad_lingtai_split.py tests/test_pad.py
```

These tests pin the exact action set, strict retirement, no-hot-load file and
append behavior, delayed activation, validation-before-persistence, manual
shape, both provider wires, and the one canonical lifecycle hook.
