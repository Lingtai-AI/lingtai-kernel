---
name: psyche-manual
last_changed_at: 2026-08-01T00:00:00Z
description: >
  Routing table for the `psyche` tool — the one public root for your four
  durable domains: pad + lingtai + knowledge + skills = psyche. Read this to
  learn which action loads which manual, and the one mutation/rebuild model all
  four share.
related_files:
- src/lingtai/tools/psyche/CONTRACT.md
- src/lingtai/tools/psyche/ANATOMY.md
- src/lingtai/intrinsic_skills/pad-manual/SKILL.md
- src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
- src/lingtai/tools/knowledge/manual/SKILL.md
- src/lingtai/tools/skills/manual/SKILL.md
- src/lingtai/intrinsic_skills/context-manual/SKILL.md
maintenance: |
  This is the psyche family's own manual, loaded by
  `psyche(action='manual', input={}, reasoning='...')`.
  It is a routing table by design: keep it short and keep the depth in the four
  domain manuals it points to. Update it together with
  src/lingtai/tools/psyche/{CONTRACT,ANATOMY}.md whenever the public action
  inventory, a domain's durable source, or the rebuild model changes.
---

# Psyche

Your **psyche** is what survives a molt: the four durable domains that are
re-read and recomposed into every fresh system prompt.

> pad + lingtai + knowledge + skills = psyche

`psyche` is the one public root that teaches them. It is a signpost family —
every action returns a manual and changes nothing. It owns no lifecycle action:
molt, summarize, and rebuild belong to `context`, and your name belongs to
`system`.

## Routing table

| Call | Returns | Durable source it teaches |
|---|---|---|
| `psyche(action="pad", input={}, reasoning="load Pad guidance")` | `pad-manual` | `system/pad.md` + pinned references in `system/pad_append.json` |
| `psyche(action="lingtai", input={}, reasoning="load identity guidance")` | `lingtai-manual` | `system/lingtai.md` (your 灵台 / character) |
| `psyche(action="knowledge", input={}, reasoning="load knowledge guidance")` | the knowledge manual | `knowledge/<name>/KNOWLEDGE.md` entries |
| `psyche(action="skills", input={}, reasoning="load skills guidance")` | the skills manual | `.library/{intrinsic,custom}/` plus configured skills paths |
| `psyche(action="manual", input={}, reasoning="load the routing table")` | this routing table | — |

Every action takes a strict empty `input`; any key is rejected before the manual
is read.

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

**Short-result.** Every psyche action returns one manual body, and a summarized
manual loses the exact procedure you called it for — leave root `summarize`
`false`. This is the family-wide rule; the domain manuals do not restate it.

## Settings

No manual in this family owns a settings file at either level — there is no
`settings/psyche.json`, no `settings/psyche.<action>.json`, and no per-domain
equivalent. Nothing to configure; an unrecognized file there is not read.
