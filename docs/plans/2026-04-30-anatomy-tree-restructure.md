# Anatomy Tree Restructure + Agent-Driven Test Suite

**Status:** Design — not yet implemented. Pilot pending.
**Date:** 2026-04-30
**Scope:** `lingtai-kernel/src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/`
**Related:** Future `lingtai-anatomy` (umbrella TUI-side, not yet shipped) — same pattern will apply there.

---

## Motivation

Current anatomy is **10 flat reference files** (~3300 lines total). It works as documentation but has two structural weaknesses:

1. **No co-located verification.** The spec says how mail works; nothing checks that the running system actually behaves that way. Drift between spec and code is silent.
2. **Existing pytest suite tests the wrong layer.** Most kernel tests are unit-level (data-structure invariants, single-function correctness). They don't catch emergent behavior, prompt regressions, real LLM dispatch quirks, or whole-flow failures (e.g. "agent A sends mail to avatar B mid-molt while a daemon fires").

The proposal: **restructure anatomy as a tree where each leaf is a self-contained verifiable unit** — a concept's spec (`README.md`) lives next to the executable check (`test.md`) that an agent runs to verify the concept holds. A conductor agent fans out across all `test.md` files in parallel, each spawning a short-lived avatar that runs one scenario and writes a markdown report into its workdir. The conductor aggregates results into a rollup. No GitHub issues — everything is filesystem-native.

This is the **dual-write principle** (spec is read AND write target, updated on every architectural change) extended to verification: spec and test live or die as one unit.

---

## Eight Design Decisions

### 1. Granularity rule

> **A leaf is anything an avatar can verify in ≤10 tool calls and ≤5 minutes wall-clock.**
> If a concept needs more, split it. If a concept needs <2 tool calls, it's probably descriptive-only (no `test.md`).

This forces consistent leaf size and is mechanically checkable. Avoids both extremes (one-leaf-per-protocol = too coarse to diagnose; one-leaf-per-line = trivia explosion).

### 2. Leaf anatomy (file contract)

Each leaf directory contains:

- **`README.md`** — required. The spec for this concept.
  - Sections: `## What`, `## Contract`, `## Source` (file:line in kernel), `## Related` (sibling leaves)
- **`test.md`** — optional. Scenario brief an avatar runs to verify the contract.
  - Sections: `## Setup`, `## Steps`, `## Pass criteria`, `## Output template`
- **`assets/`** — optional. Canonical artifacts (sample mailbox message, sample `.agent.json`, etc.) referenced from `README.md`.

**Nothing else.** No `notes.md`, no `history.md`, no `scratch.md`. Drift comes from optional doc surfaces; this contract eliminates them.

### 3. test.md scenario contract

**Input** to the avatar:
- The `test.md` file is loaded as the avatar's initial `.prompt`.
- The avatar can read its sibling `README.md` for spec details.
- The avatar's workdir is fresh (shallow avatar, only `init.json` copied).

**Output** from the avatar:
- Writes `<workdir>/test-result.md` using the standard template (below).
- Exits cleanly after writing.

**Standard `test-result.md` template:**

```markdown
# Scenario: <leaf path, e.g. mail-protocol/send/self-send>
**Status:** PASS | FAIL | INCONCLUSIVE
**Anatomy ref:** <path to sibling README.md>
**Run:** <ISO timestamp>
**Avatar:** <agent_id>

## Steps taken
1. <verbatim>
2. <verbatim>

## Expected (per anatomy)
<quote from sibling README.md>

## Observed
<verbatim tool outputs / file contents / errors>

## Verdict reasoning
<one paragraph: why PASS, FAIL, or INCONCLUSIVE>

## Artifacts
- <path to relevant file dump>
```

`INCONCLUSIVE` exists for "test couldn't run" cases (missing dependency, environment issue) — distinct from `FAIL` (test ran but contract violated).

### 4. Conductor protocol

The conductor is a LingTai agent (probably a daemon-like role) that:

1. **Discovery:** `glob('**/test.md')` from anatomy root → list of scenarios.
2. **Spawn:** for each scenario, spawn a shallow avatar with that `test.md` as the avatar's initial prompt. Spawn in parallel up to a concurrency cap (default 8 to bound cost).
3. **Wait:** poll each avatar's workdir for `test-result.md`. Per-scenario timeout (default 5 minutes; configurable per `test.md` via frontmatter).
4. **Aggregate:** read all `test-result.md` files, write rollup `<conductor>/test-results/<timestamp>/INDEX.md`.
5. **Cleanup:** optionally archive avatar workdirs (off by default — keep for debug).

**Conductor lives in the umbrella `lingtai-anatomy` skill** (TUI-side, when built) since it's a system-level orchestration role. For v0 (before umbrella exists), conductor lives in `lingtai-kernel-anatomy/conductor/` and migrates later.

**Rollup `INDEX.md` shape:**

```markdown
# Test run <timestamp>

**Total:** N scenarios — X PASS, Y FAIL, Z INCONCLUSIVE
**Wall time:** <seconds>
**Anatomy version:** <SKILL.md version>

## Failures
- [mail-protocol/send/dedup](path/to/avatar/test-result.md) — 3rd duplicate not rejected
- ...

## Inconclusive
- [mcp-protocol/licc/roundtrip](path/to/avatar/test-result.md) — no MCP available in test env
- ...

## Passes
- mail-protocol/send/self-send
- mail-protocol/send/peer-send
- ...
```

### 5. SKILL.md navigator shape

Becomes a **two-mode navigator**:

- **"I want to understand X"** → tree dump with one-line summaries pointing to `README.md` chains.
- **"I want to test X"** → same tree but highlighting which leaves have `test.md`.

Probably ~250 lines (up from current 127) to fit ~80–100 leaves. Still loadable; index-only, no spec content duplicated.

Top of SKILL.md keeps the existing "Architecture at a Glance" + "Quick Reference" sections, but the bulk becomes the leaf tree.

### 6. What goes in tests vs. what stays as code

- **Anatomy tests** = agent-driven acceptance. Real agents, real flows, observed behavior vs. spec. Catches: emergent behavior, prompt regressions, capability discoverability, real LLM dispatch quirks, cross-component bugs.
- **Existing pytest** = unit/integration. Invariants on data structures, schema validation, single-function correctness. Catches: regression on individual code paths, fast feedback, runs without LLM.

**Don't replace pytest with anatomy tests.** Different layers, different jobs. Anatomy tests are slower, costlier, less deterministic — appropriate for system-level acceptance, inappropriate for "did this function return the right value." Both stay.

### 7. Migration path

**Phase 0 — Design (this doc).** ✅

**Phase 1 — Pilot one protocol.** Pick `mail-protocol` (cleanest to test, well-organized for splitting). Explode `reference/mail-protocol.md` into the tree under `mail-protocol/`. Write SKILL.md update covering both old and new structure during transition. Write 5–6 representative `test.md` files (self-send, peer-send, dedup, scheduled, identity-card, atomic-write). Manually run one or two scenarios end-to-end (no conductor yet). Validate the leaf contract. **One commit.**

**Phase 2 — Build conductor.** Conductor agent that fans out scenarios, aggregates results. Test against the mail-protocol pilot. **One commit.**

**Phase 3 — Roll out remaining 9 protocols.** One protocol per session, each is its own commit. Order:
1. molt-protocol (next-most-testable after mail)
2. runtime-loop
3. network-topology
4. memory-system
5. mcp-protocol
6. file-formats (mostly schema validation, fast)
7. filesystem-layout (mostly inspection, fast)
8. glossary (descriptive-only, no tests — quick)
9. changelog (descriptive-only, no tests — quick)

**Phase 4 — Delete old flat reference files.** Once their tree replacements have landed and SKILL.md is fully migrated, remove `reference/<topic>.md` files. Don't keep both — drift target.

**Phase 5 — Cross-ref repointing.** Any TUI skill or other doc that referenced `lingtai-kernel-anatomy/reference/<topic>.md` paths needs updating. Known referrers: `tui/internal/preset/skills/lingtai-debug-toolkit/`, `tui/internal/preset/skills/lingtai-issue-report/`, `tui/internal/preset/procedures/procedures.md`. Use grep across both repos.

**Phase 6 — Changelog entry.** Add to `changelog.md` (which by then is at `changelog/README.md` per the new structure). Document the rename and provide the migration table for old paths → new paths.

### 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Restructure midway, decide structure is wrong, end up with half-old/half-new mess | Phase 1 pilot validates pattern on one protocol. Cheap to abort if pattern is wrong (revert one commit). |
| ~80–100 leaves is too many to maintain | Granularity rule (decision 1) bounds growth. Better to merge over-fine leaves than fight pattern. |
| Conductor cost per run exceeds budget | Concurrency cap + per-scenario timeout. Run on-demand, not on every commit. Eventual cron is per-release, not per-PR. |
| Tests are flaky (LLM non-determinism) | Test scenarios designed for **observable behavior**, not "did the LLM say the right thing." Pass criteria are filesystem checks, mailbox checks, status-file checks. Avoid prompts that ask the avatar to assert qualitatively. |
| Avatar can't reach kernel internals to verify (e.g. "did this code path execute") | Tests verify **observable outputs**, not internal state. If something needs internal state, that's a pytest, not an anatomy test. |
| Old anatomy paths cached in agents' molted state | Changelog entry per Phase 6. Old agents will hit "file not found," check changelog, find new path. Pattern already established. |

---

## Out of scope (explicitly)

- **Replacing pytest.** Anatomy tests complement, not replace.
- **CI integration.** Anatomy tests are run on-demand by humans or scheduled per-release. Not on every commit (cost + flakiness).
- **The umbrella `lingtai-anatomy` skill.** That's a separate planned piece. This restructure only touches `lingtai-kernel-anatomy`. The umbrella will follow the same pattern when built.
- **Test-result archival / trend analysis.** First runs go in conductor's workdir. Long-term archival, regression detection across runs, etc. — future work after we know what failure patterns look like.

---

## Open questions (to revisit before Phase 1)

1. **Conductor location for v0:** keep in `lingtai-kernel-anatomy/conductor/` and move to umbrella later, OR build umbrella skeleton first so conductor lives in the right place from day 1?
2. **Leaf naming convention:** `kebab-case/` (current proposal) vs. `snake_case/` vs. mixed. Pick once, stick with it.
3. **Frontmatter on test.md:** should `test.md` have YAML frontmatter (timeout, concurrency hint, dependencies)? Or keep tests as plain markdown briefs and move config to a sibling `test.yaml`?
4. **Per-leaf versioning:** when a leaf's spec changes, do we bump a version in its `README.md`, or rely entirely on git history + changelog?
5. **Test result location:** conductor's workdir (proposed) vs. fixed `~/.lingtai-tui/test-results/` (cross-conductor visibility). Trade-off is encapsulation vs. discoverability.

These are not blockers for Phase 1 — pick reasonable defaults during pilot, revisit if they hurt.

---

## What "done" looks like

- All 10 protocols restructured into leaves under `lingtai-kernel-anatomy/`
- ~80–100 leaves total, each with `README.md`, ~50–60 of those also with `test.md`
- SKILL.md is the navigator
- Conductor agent works: glob → spawn → aggregate → rollup
- One full test run completed end-to-end, rollup viewable
- Old flat reference files deleted
- Cross-refs in TUI repointed
- Changelog entry documenting the migration
- This plan doc updated with "Status: shipped" and a link to the commits

Estimated total: **6–8 focused sessions** from Phase 1 to Phase 6.

---

## When to come back to this

Pick this up when:
- The minimax-cli / xiaomi-mimo / web-browsing skill restructure work is wound down (those were tangential and shouldn't bundle with this).
- There's a clear session block (~2 hours) for Phase 1 pilot — partial pilots create awkward intermediate states.
- No active kernel release pressure (this restructure should not race a release).

Until then: anatomy stays flat. The current 10-file structure is fine; this restructure is improvement, not bug fix.
