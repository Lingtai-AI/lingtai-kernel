---
name: psyche-manual
last_changed_at: 2026-08-29T00:00:00Z
description: >
  Routing table for the `psyche` tool — the one public root for your four
  durable domains: pad + lingtai + knowledge + skills = psyche. Read this to
  learn which action loads which manual, inspect Psyche-owned prompt settings, and
  follow the one mutation/rebuild model all four domains share.
related_files:
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/tools/psyche/ANATOMY.md
- src/lingtai/tools/psyche/settings.py
- src/lingtai/agent.py
- src/lingtai/intrinsic_skills/pad-manual/SKILL.md
- src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
- src/lingtai/tools/knowledge/manual/SKILL.md
- src/lingtai/tools/skills/manual/SKILL.md
- src/lingtai/tools/context/manual/SKILL.md
- tests/test_psyche_family.py
maintenance: |
  This is the psyche family's own manual, loaded by
  `psyche(action='manual', input={}, reasoning='...')`.
  It is a routing table by design: keep it short and keep the depth in the four
  domain manuals it points to. Update it together with
  src/lingtai/tools/psyche/{CONTRACT,ANATOMY}.md whenever the public action
  inventory, owned settings, a domain's durable source, or the rebuild model
  changes.
---

# Psyche

Your **psyche** is what survives a molt: the four durable domains that are
re-read and recomposed into every fresh system prompt.

> pad + lingtai + knowledge + skills = psyche

`psyche` is the one public root that teaches them. Its five domain/routing
actions return manuals; `settings` shows a bounded, fully redacted inventory of
the Pad configuration Psyche owns. Every public action is read-only. It owns no
lifecycle action: molt, summarize, and rebuild belong to `context`, and your
name belongs to `system`.

## Routing table

| Call | Returns | Durable source it teaches |
|---|---|---|
| `psyche(action="pad", input={}, reasoning="load Pad guidance")` | `pad-manual` | `system/pad.md` + pinned references in `system/pad_append.json` |
| `psyche(action="lingtai", input={}, reasoning="load identity guidance")` | `lingtai-manual` | `system/lingtai.md` (your 灵台 / character) |
| `psyche(action="knowledge", input={}, reasoning="load knowledge guidance")` | the knowledge manual | `knowledge/<name>/KNOWLEDGE.md` entries |
| `psyche(action="skills", input={}, reasoning="load skills guidance")` | the skills manual | `.library/{intrinsic,custom}/` plus configured skills paths |
| `psyche(action="settings", input={}, reasoning="inspect Psyche prompt configuration")` | eight fully redacted five-field rows | Pad seed plus Psyche's prompt-owner document |
| `psyche(action="manual", input={}, reasoning="load the routing table")` | this routing table | — |

Every action takes a strict empty `input`; any key is rejected before its
provider or manual loader runs.

## The one mutation model

`psyche` has **no** mutating action. That is deliberate, not an omission:
durable content is ordinary text, so it is changed by the ordinary text tools.

1. **Write** the durable source with `file.write` (create or full overwrite) or
   `file.edit` (exact replacement).
2. **Apply** it with one explicit
   `context(action="rebuild", input={}, reasoning="apply durable changes")`.

File mutation never hot-loads the prompt: a durable change written but not
rebuilt is real on disk and simply not yet visible in your context — which is what
makes a batch of edits land atomically instead of one half-composed section at a
time. A full rebuild recomposes **all** enabled canonical sections once, applies
pending summaries, then requests provider replay; passive reconstruction
(`system(action="refresh", ...)` and molt) runs the same contract. There is no
per-domain reload to call.

Catalog upkeep is not yours to trigger either. Skills and Knowledge catalogs are
rescanned and recomposed by that same reconstruction path (and at setup/refresh);
authoring a new `KNOWLEDGE.md` or `SKILL.md` and then rebuilding is the whole
procedure.

## Which domain am I in?

- Working notes, the current task, the living index you tend every turn → **pad**.
- Who you are, your voice, how you carry yourself → **lingtai** (灵台).
- Something you learned, decided, or discovered and want back after a molt,
  possibly referencing local paths, mail ids, or logs → **knowledge**.
- A reusable procedure that would help any agent, not just you → **skills**.

When the choice is genuinely unclear, the domain manuals own that distinction in
depth.

## `summarize`

**Short-result.** Manual actions return one manual body, and `settings` returns
eight compact rows. Leave root `summarize` `false`; summarizing either result loses
the exact procedure or inventory you called it for.

## Settings

`psyche(action="settings", input={}, reasoning="inspect Psyche prompt configuration")`
is SHOW only. It returns exactly `pad`, `pad_file`, `base_prompt`,
`base_prompt_file`, `covenant`, `covenant_file`, `comment`, then `comment_file`.
Every row has exactly `key`, `current`, `default`, `configurable`, and `comment`
in that order. Both `current` and `default` are always `<redacted>` for every
row, including empty or absent values. The action reports the applied snapshot
from the last successful full reconstruction. Ambient source edits do not change
SHOW until rebuild, refresh, or molt applies them; a failed reconstruction keeps
the last successful snapshot. A provider/snapshot failure returns only the fixed
bounded `SETTINGS_UNAVAILABLE` failure, never partial rows or parser details.

`settings/psyche.json` is Psyche's deliberately small owner document. It is
optional: a missing file means schema v1 with all six owner values absent. When
present it must be UTF-8 JSON object `{"schema_version": 1, ...}` with no keys
other than `base_prompt`, `base_prompt_file`, `covenant`, `covenant_file`,
`comment`, and `comment_file`; each present value is a string (including `""`).
Duplicate/unknown keys, Boolean/non-1 versions, non-regular or symlink files,
files over 64 KiB, unstable reads, invalid UTF-8/JSON, and read failures reject
the complete reconstruction before prompt publication. There is no environment
layer, `set`, `reset`, patch action, migration, or writeback.

The legacy top-level init spellings for these six fields are compatibility-known
but inert. They neither configure the prompt nor populate SHOW. For each owner
pair, a readable `*_file` wins; a missing file falls back to inline. `~` expands
and relative pointers resolve against the agent workdir. Edit the owner document
with `file.write`/`file.edit`, then use `context.rebuild` (or refresh/molt) to
apply it atomically.

### Setting pad

- **Meaning and default:** the configured UTF-8 initial Pad seed. Its meaningful
  default is the empty string.
- **Source and precedence:** top-level `pad_file` wins when it names a readable
  file; otherwise top-level inline `pad` is the fallback. Reconstruction
  materializes, validates, and path-resolves that shape once; SHOW reports the
  resulting applied snapshot without rereading either source.
- **Configurable:** `true`, because the operator may edit the authorized root
  init source or the file it names. SHOW still fully redacts the effective body.
- **Apply timing and procedure:** active or passive full reconstruction seeds
  `system/pad.md` only when that durable body is missing or empty. A nonempty
  durable Pad is preserved. To replace one, edit `system/pad.md` through the Pad
  procedure, then call `context(action="rebuild", input={}, reasoning="apply Pad change")`;
  changing only the configured seed does not overwrite a nonempty durable Pad.

### Setting pad file

- **Meaning and default:** the configured file pointer supplying the initial Pad
  seed. It has no meaningful default, so its underlying default is `null`.
- **Source and precedence:** top-level `pad_file` only. `~` expands and a
  relative path resolves against the agent working directory. A readable file
  supplies `pad`; a missing or blank pointer falls back to inline `pad`. SHOW
  fully redacts the resolved pointer as well as the Pad body.
- **Configurable:** `true`, because the operator may edit the authorized root
  init source. No environment or settings-file layer exists.
- **Apply timing and procedure:** the pointer is re-read on full reconstruction,
  but its content remains only an initial seed for a missing/empty
  `system/pad.md`. To change a nonempty durable Pad, use the Pad file procedure
  and rebuild instead of expecting `pad_file` to overwrite it.

A second SHOW before reconstruction deliberately reports the same applied
snapshot. After an authorized rebuild/refresh/molt succeeds, another SHOW can
verify that discovery remains available, but because both values are always
redacted it cannot reveal or compare the underlying content.

### Setting base prompt

The optional third-party/application prompt body; default `""`, configurable
`true`. Psyche resolves it once per reconstruction, writes nonempty content to
`system/base_prompt.md`, and otherwise falls back to that mirror. The kernel
renders it after raw `principle` and before the remaining Batch 1 sections.

### Setting base prompt file

Optional pointer; default `null`, configurable `true`. A readable file wins
over `base_prompt`; its path is fully redacted and follows the owner-document
relative/`~` rules above.

### Setting covenant

Optional protected operator-contract body; default `""`, configurable `true`.
Nonempty resolved content mirrors to `system/covenant.md` and uses the existing
protected covenant section; absent owner content falls back to that mirror.

### Setting covenant file

Optional pointer; default `null`, configurable `true`. A readable file wins
over `covenant`; the pointer and resolved body remain fully redacted.

### Setting comment

Optional unprotected comment-section body; default `""`, configurable `true`.
Unlike base prompt and covenant, comment has no `system/*.md` mirror: an absent
owner value removes the section on the applied reconstruction.

### Setting comment file

Optional pointer; default `null`, configurable `true`. A readable file wins
over `comment`; it is fully redacted and has no mirror behavior.
