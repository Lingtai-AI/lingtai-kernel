---
name: lingtai-tool-contract
tool: lingtai
contract_version: 2
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/lingtai/ANATOMY.md
  - src/lingtai/tools/lingtai/__init__.py
  - src/lingtai/tools/lingtai/_lingtai.py
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/psyche/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
  - tests/test_context_ownership_redesign.py
  - tests/test_pad_lingtai_split.py
maintenance: |
  This component contract is governed by root CONTRACT.md. Keep the paired
  ANATOMY.md, manual, schema, glossary resources, and focused tests in sync.
  Bump contract_version for breaking changes. Version 2 makes the public family
  a manual-only signpost and removes update/load without aliases.
---

# LingTai capability contract

## Purpose and ownership
Guarded by: [LG001](BEHAVIORS.md#behavior-lg001)


`lingtai` is the model-visible signpost for the agent's 灵台: durable character
content in `system/lingtai.md`, rendered into the protected `character` prompt
section. The public action inventory is exactly `manual`.

Durable mutation of `system/lingtai.md` happens through `shell`: create or
overwrite the complete identity file, or perform an exact text replacement
after verifying the old text exists exactly once, then read the file back to
verify.

No filesystem operation hot-loads the prompt. Apply durable identity changes through
one explicit `context.rebuild`, or let passive refresh/molt reconstruction apply
them. Retired `lingtai.update` and `lingtai.load` have no aliases and fail as
unknown actions.

## Public port

The strict LTP v2 root envelope is exactly `action`, `input`, `reasoning`, and
optional root `summarize`, with `additionalProperties: false`; `action`, `input`,
and `reasoning` are required. The sole action is:

| Action | Input | Result |
|---|---|---|
| `manual` | strict empty `{}` | flat `{status, manual, manual_path}` (+ degraded `error`) |

A manual-only family still has the full valid strict envelope, action/input
correlation, and provider-wire representation. Non-empty/non-object manual input,
unknown root fields, and a non-boolean root `summarize` fail with LTP v2
`INVALID_ARGUMENT`. Root `summarize`, `reasoning`, and `_tc_id` never become
manual input.

## Internal composition and identity modes

`_lingtai_load` remains private and is not a compatibility alias. It is the
single canonical writer of the protected `character` prompt section, composing
from `system/lingtai.md` only; it never touches `covenant` or mechanical
`identity`.

`Agent._reload_prompt_sections` reuses this composer inside the shared
`Agent._reconstruct_context` path:

- a nonempty configured `lingtai`/resolved `lingtai_file` value is authoritative
  and is materialized into `system/lingtai.md` before composition;
- absent/empty configuration preserves the self-authored durable file.

`boot(agent)` performs initial internal composition only. It does not register a
post-molt hook. Agent owns one full reconstruction hook used by refresh, molt,
and active `context.rebuild`.

## Contract rules and evidence

- `manual` resolves `lingtai-manual` and is flattened once after generic
  ToolFamily dispatch.
- Schema and dispatch expose no hidden mutation/reload action on either provider
  wire.
- `lingtai` remains in `_LTP_V2_MIGRATED_FAMILIES` and
  `EMANATION_BLACKLIST`; no settings file exists.
- The package owns guidance for identity, not generic file mutation or active
  prompt reload.

Focused evidence:

```bash
python -m pytest -q tests/test_context_ownership_redesign.py \
  tests/test_pad_lingtai_split.py
```

The tests pin the manual-only schema and dispatch, strict retired-action
rejection, file no-hot-load behavior, canonical private composition, identity
separation, provider-wire parity, and the single lifecycle reconstruction hook.
