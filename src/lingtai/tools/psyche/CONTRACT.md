---
name: psyche-tool-contract
contract_version: 4
root_contract: CONTRACT.md
related_files:
  - src/lingtai/tools/psyche/ANATOMY.md
  - src/lingtai/tools/psyche/BEHAVIORS.md
  - src/lingtai/tools/psyche/__init__.py
  - src/lingtai/tools/psyche/settings.py
  - src/lingtai/tools/psyche/prompt.py
  - src/lingtai/agent.py
  - src/lingtai/cli_project.py
  - src/lingtai/kernel/project/__init__.py
  - src/lingtai/tools/avatar/__init__.py
  - src/lingtai/CONTRACT.md
  - src/lingtai/tools/CONTRACT.md
  - src/lingtai/tools/tool_family/CONTRACT.md
  - src/lingtai/tools/context/CONTRACT.md
  - src/lingtai/tools/pad/CONTRACT.md
  - src/lingtai/tools/lingtai/CONTRACT.md
  - src/lingtai/tools/knowledge/CONTRACT.md
  - src/lingtai/tools/skills/CONTRACT.md
  - src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
  - tests/test_psyche_family.py
  - tests/test_psyche_prompt_settings.py
  - tests/test_deep_refresh.py
  - tests/test_project_creation.py
  - tests/test_tool_family_avatar_migration.py
  - tests/test_context_ownership_redesign.py
  - tests/test_tool_family_context_migration.py
  - tests/test_tool_settings_contract.py
maintenance: |
  This component contract is governed by the root CONTRACT.md and owns the one
  public `psyche` root. Keep the paired ANATOMY.md, the psyche-manual
  routing table, owner settings provider, Pad reconstruction seam, the four
  domain Contracts it points to, glossary resources, and focused tests in sync.
  Bump contract_version for any change to the public action inventory or to the
  read-only/settings promise.
  Follow the root Anatomy/Contract pairing and ownership rules, report
  mismatches, and do not duplicate or auto-fix the rule here.
---
# Psyche tool contract

## Purpose
Guarded by: [PY001](BEHAVIORS.md#behavior-py001)


`psyche` is the single mandatory, model-visible LTP v2 family for the four
durable domains that survive a molt. The name states the governing human
contract exactly:

> pad + lingtai + knowledge + skills = psyche

It is a read-only manual router plus owner settings SHOW. Its five manual
actions teach the durable domains and routing model; its reserved `settings`
action exposes exactly eight fully redacted Psyche-owned inputs: the existing
Pad pair plus the three configurable system-prompt pairs. Psyche owns the
small closed `settings/psyche.json` v1 document for those latter six fields.

It replaces four former public roots (`pad`, `lingtai`, `knowledge`, `skills`) as
a clean break. Those roots, the `pad.append` action, and the `skills.info` /
`knowledge.info` actions are retired with no alias, wrapper, or compatibility
path; those tool names are unknown and fail loudly. The domains' capabilities did
not move to this family — they moved to private lifecycle ownership documented in
the four Contracts listed in `related_files`.

### Root reuse is not action compatibility

The name `psyche` previously belonged to a different family whose actions were
`lingtai_update`, `lingtai_load`, `pad_edit`, `pad_load`, `pad_append`,
`context_molt`, `name_set`, `name_nickname`, and `manual`. That family was
dissolved: its lifecycle actions moved to `context`
(`molt | summarize | rebuild | manual`) and its name actions to `system`.

Reusing the root name grants none of them, and the two cases are distinct:

- **The eight old non-`manual` spellings are unknown actions.**
  `lingtai_update`, `lingtai_load`, `pad_edit`, `pad_load`, `pad_append`,
  `context_molt`, `name_set`, and `name_nickname` — together with the lifecycle
  verbs that now belong to `context` (`molt`, `summarize`, `rebuild`) — fail
  before any I/O with no alias, wrapper, or compatibility path.
- **The spelling `manual` is deliberately reused, with new semantics.** It is a
  current, accepted action, so a `manual` call is NOT rejected. What it returns
  is only the new durable-self routing table (`psyche-manual`, the five-manual map
  over Pad / 灵台 / Knowledge / Skills). It never returns the dissolved family's
  manual or body, and accepting it grants no compatibility with any other old
  action.

A Contract statement that "no `psyche` root exists" is historical and now false
about the *root*. The normative statement is narrower and remains true: no old
`psyche` action is reachable, and the one reused action name carries only its
new meaning.

### Prompt-plan composition (PR A)

Psyche owns the pure composition input for the static `principle`, `substrate`,
and `procedures` contributions. `compose_prompt_plan` reads the existing
`PsychePromptInputs` once and packages those three ordered resident bodies plus
their mirror metadata into one immutable `PromptPlan`. It does not write a
mirror, mutate a prompt manager, own the kernel's raw first slot, change cache
batches, or perform provider/session publication. The Agent resolves the full
plan once before refresh teardown (or before an active/passive reconstruction
transaction), carries that same object through the existing seam, and commits
it only after the final flush succeeds. Packaged bodies retain the existing
on-disk mirror fallback and frontmatter-stripping behavior; no public Psyche
action or prompt body changes in this slice.

### The `substrate` prompt section keeps its kernel render mechanics

The `substrate` *prompt section* (`lingtai/prompts/substrate/substrate.md` →
`system/substrate.md`) keeps its name, content, and render order. Its static
source contribution is carried by the Psyche plan, while the kernel retains the
render slot and cache mechanics. This family briefly carried the name `substrate`
as a public root; that root is gone, and nothing keys the two namespaces together.

## Behavior

Agents MUST treat every `psyche` action as read-only. No action authors,
edits, pins, installs, migrates, rescans a catalog, writes a prompt or source
file, or reloads prompt state.

The static prompt plan is an internal composition boundary, not a new action.
Its section tuple is ordered and immutable, and a reconstruction MUST apply the
same candidate that was resolved before the seam began. A failed final flush
MUST restore the prior plan object together with the existing prompt-manager
sections, configurable prompt mirrors, and SHOW.

`settings` MUST report the applied snapshot consumed by the last successful
canonical reconstruction and return exactly `pad`, `pad_file`, `base_prompt`,
`base_prompt_file`, `covenant`, `covenant_file`, `comment`, then `comment_file`.
Ambient edits to init, Pad, or the owner document MUST NOT change SHOW until
active or passive reconstruction successfully consumes them; a malformed owner
document or a failed reconstruction MUST leave the prior SHOW available.
Live refresh MUST resolve the owner document exactly once immediately after a
successful init read and before any runtime teardown; an owner failure leaves
the sealed runtime, service/session, tool/plugin/MCP bindings, prompt state,
mirrors, and SHOW unchanged. If final prompt publication fails, reconstruction
MUST restore the prior prompt-manager sections, wrapper base prompt,
base/covenant and system mirrors, and SHOW together, so a rejected generation
cannot be rebuilt or resurrected through later mirror fallback.
Each row MUST project exactly `key`, `current`, `default`, `configurable`, and
`comment` in that order. The current and default values of all eight rows MUST
be fully redacted, including empty and null defaults. A provider/snapshot/row
failure MUST return
the generic fixed `SETTINGS_UNAVAILABLE` result with no partial inventory or
exception text. The complete response MUST remain subject to the generic
incremental 65,536-byte UTF-8 bound.

To change a durable source, an agent MUST use the generic text operations —
`file.write` for a full create/overwrite, `file.edit` for exact replacement — on
that domain's own source, and then apply the change with one explicit
`context(action="rebuild", input={}, reasoning="...")` or let passive
refresh/molt reconstruction apply it.
File mutation never hot-loads the prompt.

Agents SHOULD read the relevant domain manual before acting on a domain they do
not already know, and SHOULD leave root `summarize` false so exact procedure and
constraints are preserved.

Coding agents MUST keep this contract, the paired Anatomy, the psyche manual,
and the focused tests synchronized whenever the action inventory or the
read-only promise changes.

## Port

The strict LTP v2 root envelope is exactly `action`, `input`, `reasoning`, and
optional root `summarize`, with `additionalProperties: false`; `action`, `input`,
and `reasoning` are required. The public action inventory is exactly:

| Action | Input | Result |
|---|---|---|
| `pad` | strict empty `{}` | flat `{status, manual, manual_path}` (+ degraded `error`) — `pad-manual` |
| `lingtai` | strict empty `{}` | same shape — `lingtai-manual` |
| `knowledge` | strict empty `{}` | same shape — the installed knowledge manual |
| `skills` | strict empty `{}` | same shape — the installed skills manual |
| `settings` | strict empty `{}` | exact `{settings: [...]}` inventory with eight fully redacted five-field rows |
| `manual` | strict empty `{}` | same shape — `psyche-manual`, the routing table |

Every call carries required root `action`, `input`, and `reasoning`; a public
call is spelled `psyche(action="<domain>", input={}, reasoning="...")`. All
six children share one strict-empty `input` schema, so every `input` key is an
unknown key. Unknown or missing actions, any `input` key, non-object `input`,
unknown root fields, and a non-boolean root `summarize` fail with the LTP v2
envelope errors before any file is read. Root `summarize`, `reasoning`, and the
intrinsic-only `_tc_id` never become child input.

## Adapters

Dispatch and schema composition are the generic `tool_family` infrastructure.
Five children are built by the shared `build_manual_child` loader, so there is
one loader and one result adapter for every manual action — no per-domain
handler exists to acquire a side effect. The flat
`{status, manual, manual_path}` presentation shape is rebuilt strictly after
dispatch in this package's own Host layer, per the no-double-wrap rule.

Schema composition opts in with an inert callable so the reserved `settings`
child is injected immediately before `manual`. The static `DECLARATION` binds
only `workdir` and `PsycheSettingsPort`; the provider reads the Agent-owned
applied eight-value snapshot through that one read-only operation, copies and
validates its eight structural scalar fields, and performs no file I/O or Agent
access. Agent reconstruction consumes one immutable Psyche prompt plan, uses the
existing prompt file-over-inline helper, then publishes the replacement plan and
snapshot only after the successful final prompt flush.

`serialize_prompt_owner_document` is Psyche's one public v1 owner-document
writer. Avatar calls it directly; `cli_project` calls it at the composition root
and injects the serialized content into Project Core, which remains opaque to
Psyche schema keys.

`psyche` remains mandatory in `INTRINSICS`, marked `official_plugin=True`: the
intrinsic entry is only the kernel hook/dispatch shim. `boot` runs the private
Pad/LingTai composers and registers the static declaration, so the public schema
and handler are claimed and mounted exactly once through the official registrar.
Neither the binder nor settings provider receives an Agent.

## Contract rules

- Schema and dispatch derive from the same fixed child registry; the advertised
  action enum cannot drift from the dispatch keys.
- The exact action order is `pad | lingtai | knowledge | skills | settings |
  manual`; the reserved settings action is immediately before `manual`.
- Every child is mutation-free. A future mutating action does not belong in this
  family; durable mutation has exactly one owner, `file`.
- The four domain actions load the domains' existing manuals as progressively
  disclosed references. This router MUST NOT copy those manual bodies inline.
- Catalog composition, configured Skills paths, disabled-domain behavior, and the
  one-time Knowledge legacy migration remain owned by those capabilities'
  private `setup()`/refresh lifecycle and MUST NOT be reachable from any
  `psyche` action.
- Full active `context.rebuild` and passive refresh/molt reconstruction re-read
  and recompose all enabled canonical sections once and publish one prompt; this
  family action surface participates in none of it. The Agent reconstruction
  seam applies configured `pad` only as an initial seed for missing/empty
  `system/pad.md`; it preserves every nonempty durable Pad before composing it,
  then retains the successfully resolved Pad inputs for settings discovery.
- SHOW reads only that retained snapshot. Ambient source edits and failed
  reconstruction attempts preserve the prior applied prompt generation and rows
  until a later successful reconstruction replaces them. Refresh rejects an
  invalid owner candidate before destructive teardown.
- `psyche` is in `_LTP_V2_MIGRATED_FAMILIES` and `EMANATION_BLACKLIST`.
- Psyche owns exactly these settings rows:
  - `pad`: default `""`, configurable `true`, fully redacted, comment
    `psyche-manual#setting-pad`.
  - `pad_file`: default `null`, configurable `true`, fully redacted, comment
    `psyche-manual#setting-pad-file`.
  - `base_prompt` / `base_prompt_file`: defaults `""` / `null`, fully
    redacted, anchors `#setting-base-prompt` / `#setting-base-prompt-file`.
  - `covenant` / `covenant_file`: defaults `""` / `null`, fully redacted,
    anchors `#setting-covenant` / `#setting-covenant-file`.
  - `comment` / `comment_file`: defaults `""` / `null`, fully redacted,
    anchors `#setting-comment` / `#setting-comment-file`.
- `settings/psyche.json` is optional; when present it is a bounded (64 KiB),
  stable-read UTF-8 JSON object with exact integer `schema_version: 1`, no
  duplicate/unknown keys, and optional string-only six fields. A non-regular or
  symlink document, invalid bytes/JSON/schema, read race, or I/O failure raises
  one typed Psyche settings error before prompt publication. It has no
  environment layer, mutation action, migration, or writeback.
- Psyche MUST NOT expose LingTai inputs, Skills paths, content, paths, auth, or
  any other row. System MUST NOT duplicate the six Psyche prompt rows.
- `summarize` profile: **short-result** for every action.

## Contract tests

```bash
python -m pytest -q tests/test_psyche_family.py tests/test_psyche_prompt_settings.py
```

These pin the exact six-action inventory and order; strict-empty input on every
child; strict bounded owner parsing and file-over-inline resolution; ambient
edit isolation; last-good preservation after failed reconstruction; exact
five-field projection, eight-row order, defaults, anchors, and full redaction;
whole-inventory failure;
missing/empty-only Pad seeding; unchanged manual routing; no action mutation;
the absence of old roots/actions; and both provider wire shapes. The shared
settings suite additionally pins the 65,536-byte whole-response bound and the
exact production owner opt-ins (`system` plus this intrinsic).

## Maintenance

Keep `related_files` complete and repo-relative, including the paired
`ANATOMY.md`, the psyche manual, the four domain Contracts, and the contract
tests. Update the Port, this contract, the manual, and the tests together when
the action inventory or the read-only promise changes; update the paired Anatomy
when structure changes. Follow the root Anatomy/Contract pairing and ownership
rules, report mismatches, and do not duplicate or auto-fix the rule here.
