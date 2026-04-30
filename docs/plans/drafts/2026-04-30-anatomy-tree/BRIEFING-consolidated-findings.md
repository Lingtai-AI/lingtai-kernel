# Consolidated Briefing — Anatomy Draft Findings

**Date:** 2026-04-30
**From:** draft-maintenance
**To:** lingtai-expert (parent)
**Status:** Awaiting approval

---

## I. Fundamental Discovery

`leaves/` contains **103 files** — a complete anatomy tree already built, covering:
- **capabilities/** — mail (7 leaves), avatar (4), daemon (4), mcp (3), psyche (3), shell/bash (5), shell/codex (2), file (5), library (1), vision (1), web_search (1)
- **core/** — agent-state-machine, config-resolve, molt-protocol, network-discovery, preset-allowed-gate, preset-materialization, venv-resolve, wake-mechanisms
- **init/** — init-schema
- **llm/** — anthropic, deepseek, gemini, minimax, openai, openrouter

Each leaf has `README.md` + `test.md` (with one exception: `shell/bash/README.md` has no `test.md` — it's an overview leaf with children).

The five draft documents were written under the assumption "tree doesn't exist yet." **That assumption is broken.**

---

## II. Five Documents — Repositioned

| # | Document | Original | Revised | Action |
|---|---|---|---|---|
| 1 | `phase1-migration-checklist.md` | How to build the tree | Decision record of completed work | Retain as reference; no longer an execution plan |
| 2 | `open-questions-draft.md` | Prescriptive defaults for future decisions | Retrospective confirmation of decisions already made by the tree | Compare against `leaves/` to confirm or flag deviations |
| 3 | `conductor/` | Phase 2 blueprint | Phase 2 blueprint (unchanged — conductor not yet built) | No change |
| 4 | `pilot-leaf/` | Template to follow during Phase 1 | Historical artifact (the real tree is in `leaves/`) | Archive; skip fix #2 |
| 5 | `5-maintenance-procedure.md` | Future governance spec | **Urgent governance needed now** (103 leaves, no drift check) | This is the most important deliverable |

---

## III. Seven Fixes — Reordered

| # | Issue | Files | Severity | Status | Notes |
|---|---|---|---|---|---|
| **3** | AUDIT.log timezone ambiguous | `5-maintenance-procedure.md` | Medium | **✅ Done** | Changed to UTC across all examples and format spec |
| **4** | Output path base ambiguous | `5-maintenance-procedure.md` | Medium | **✅ Done** | Added "relative to anatomy root" convention blockquote |
| **7** | §Source column headers inconsistent | `open-questions-draft.md` + 9 leaves in `leaves/` | **Medium** | **Ready** | See §IV below. Proposal: standardize to `\| What \| File \| Line(s) \|`. 9 leaves need normalization. |
| **6** | Changelog entry format undocumented | `5-maintenance-procedure.md` | Low | **Held** | Add format convention paragraph to Step 5. Low urgency — no existing entries to conflict with. |
| **1** | test.md frontmatter contradiction | `open-questions-draft.md` vs `phase1-migration-checklist.md` | ~~High~~ → **Low** | **Held** | Checklist is now a retrospective document. The open-questions-draft answer (YAML frontmatter) is authoritative. Add a note to checklist §6 Q3 deferring to open-questions-draft. |
| **2** | Pilot leaf path mismatch | `pilot-leaf/` vs proposed tree | ~~Medium~~ → **N/A** | **Skip** | Pilot is a historical artifact. The real tree at `leaves/capabilities/mail/` has the correct structure. No fix needed. |
| **5** | Commit message format undocumented | `phase1-migration-checklist.md` | ~~Low~~ → **N/A** | **Skip** | Checklist is retrospective; commit already made. Format convention has no forward-looking value. |

---

## IV. §Source Column Normalization (Fix #7 — Detail)

### Current state

49 leaves have `## Source` sections. Two column conventions coexist:

| Convention | Header row | Count | Used by |
|---|---|---|---|
| **A** | `\| What \| File \| Line(s) \|` | ~25 | mail, avatar, bash, molt, wake, init, vision, codex |
| **B** | `\| Component \| File \| Lines \|` | ~9 | mcp (3), core (6) |
| **C** (heading variant) | `## Source (real file:line)` | 3 | mcp only |

Convention A is the majority (~75%). Convention B is a minority.

### What the maintenance procedure sees

The grep in Step 2 searches for file paths (`lingtai_kernel/`) in the data rows — it does not depend on column headers. **No breakage today.**

But: a future automated reference-index builder (the "more systematic approach" in Step 2) would parse column positions. Inconsistent headers = broken parser.

### Proposal

1. **Standardize header to:** `| What | File | Line(s) |`
2. **Standardize heading to:** `## Source` (no parenthetical suffix)
3. **Leaves to update:** 9 leaves using Convention B, 3 using Convention C heading
4. **Add to `open-questions-draft.md`:** the §Source column convention (already drafted as fix #7)
5. **Scope:** Only `## Source` tables. Other tables in the same README (e.g. `| Source | Destination | Method |` in shallow-vs-deep) are not source-reference tables and are exempt.

### Files to change (if approved)

**Heading change (3 files):**
- `leaves/capabilities/mcp/capability-discovery/README.md`
- `leaves/capabilities/mcp/inbox-listener/README.md`
- `leaves/capabilities/mcp/licc-roundtrip/README.md`

**Column header change (9 files):**
- `leaves/capabilities/mcp/capability-discovery/README.md` (same 3 files)
- `leaves/capabilities/mcp/inbox-listener/README.md`
- `leaves/capabilities/mcp/licc-roundtrip/README.md`
- `leaves/core/agent-state-machine/README.md`
- `leaves/core/config-resolve/README.md`
- `leaves/core/network-discovery/README.md`
- `leaves/core/preset-allowed-gate/README.md`
- `leaves/core/preset-materialization/README.md`
- `leaves/core/venv-resolve/README.md`

**Data rows:** May also need `Component → What` and `Lines → Line(s)` in each row. Would need to verify each file individually — the `Lines` column header is cosmetic but `Line(s)` includes the parenthetical hint that ranges are expected.

---

## V. Requested Decisions

1. **Approve §Source normalization?** If yes, I execute the 9-leaf fix + add the convention to `open-questions-draft.md`.
2. **Approve fix #6** (changelog format convention in maintenance procedure)? Low urgency but easy to do now.
3. **Approve fix #1** (one-line note in checklist §6 Q3 deferring to open-questions-draft)? Cosmetic cleanup.
4. **Any other direction?** The maintenance procedure is ready for deployment as-is (fixes 3+4 already applied). If the parent wants to use it against the existing `leaves/` tree, that would be the first real validation.

---

*End of briefing.*
