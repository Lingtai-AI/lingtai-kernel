# Anatomy Tree Restructure — Open Questions Draft Answers

**Status:** Draft — proposed answers for review before Phase 1 pilot.
**Date:** 2026-04-30
**Source:** Five open questions from [2026-04-30-anatomy-tree-restructure.md](../2026-04-30-anatomy-tree-restructure.md#open-questions-to-revisit-before-phase-1)

These are opinionated draft answers with trade-off analysis. Each recommends a default for Phase 1. All are revisitable if the pilot surfaces pain.

---

## Q1. Conductor location for v0

**The question:** Keep conductor in `lingtai-kernel-anatomy/conductor/` and move to umbrella later, OR build umbrella skeleton first so conductor lives in the right place from day 1?

### Options

**A. Temporary home in kernel-anatomy (proposed)**
- Pro: Zero scope creep — pilot stays within `lingtai-kernel-anatomy` as designed.
- Pro: Umbrella `lingtai-anatomy` is explicitly out of scope; blocking on it delays everything.
- Pro: Migration cost is trivial — one `mv`, one `grep` to update references, one changelog entry. AI-maintained docs don't have the "who updates the wiki?" human-tax.
- Con: One extra migration step in a later phase.
- Con: If something external hardcodes the conductor path during v0, that breaks on move.

**B. Build umbrella skeleton first**
- Pro: Conductor lives in its permanent home from day 1. No migration.
- Pro: Forces early thinking about umbrella structure.
- Con: Scope creep. The umbrella is a separate planned piece with its own design questions (TUI flows, presets, init.jsonc). Even a "skeleton" needs a SKILL.md, directory layout, and boundary decisions.
- Con: Delays Phase 1 pilot. The pilot is the cheap validation of the leaf pattern — coupling it to umbrella design adds risk.

### Recommendation: **A — temporary home in kernel-anatomy.**

The whole point of the phased approach is to validate cheaply before committing. Building the umbrella first couples two independent design efforts and delays the pilot by at least one session. The migration cost is genuinely low: the conductor is one directory with a SKILL.md entry and maybe a script. Grep-and-replace across a handful of files, add a changelog row, done. LingTai's own migration protocol (changelog → old path not found → new path) already handles this gracefully for any agent that cached the old location.

**Phase 1 default:** `lingtai-kernel-anatomy/conductor/` with a comment in the conductor's README noting the intended future home.

---

## Q2. Leaf naming convention

**The question:** `kebab-case/` (current proposal) vs. `snake_case/` vs. mixed. Pick once, stick with it.

### Options

**A. `kebab-case/` everywhere**
- Pro: Matches existing anatomy file names (`mail-protocol.md`, `file-formats.md`, `network-topology.md`).
- Pro: URL-friendly (relevant if the portal or web-based viewers render these paths).
- Pro: Visually distinct from Python identifiers (code is `snake_case`, docs are `kebab-case`) — zero ambiguity about what you're looking at.
- Con: Not idiomatic Python (the lingtai codebase is `snake_case`). But these are docs, not code.

**B. `snake_case/` everywhere**
- Pro: Pythonic. Matches the kernel source layout.
- Pro: No ambiguity with shell quoting (kebab-case needs quoting in some shell contexts, though agents rarely care).
- Con: Would require renaming all existing reference files (`mail-protocol.md` → `mail_protocol.md`). Unnecessary churn.
- Con: Visually identical to Python modules — easy to confuse a leaf directory with a package.

**C. Mixed (kebab for topics, snake for sub-leaves)**
- Pro: Could encode hierarchy in the naming convention itself.
- Con: Requires a mental model to remember which convention applies where. The whole point is to pick once and forget.

### Recommendation: **A — `kebab-case/` everywhere.**

It's already the convention in the existing anatomy. Changing it means renaming 10 files and every cross-reference for zero practical benefit. The agents consuming these paths don't care about case conventions — they glob, grep, and read. The only audience that benefits from consistency is humans reviewing the structure, and `kebab-case` is perfectly readable.

Sub-leaves within a protocol also use kebab: `mail-protocol/send/self-send/`, `mail-protocol/send/dedup/`, etc. This keeps the convention uniform: every directory at every level is `kebab-case`. No rules to remember.

**Phase 1 default:** `kebab-case/` for all directories and files, consistent with existing reference file names.

---

## Q3. Frontmatter on test.md

**The question:** Should `test.md` have YAML frontmatter (timeout, concurrency hint, dependencies)? Or keep tests as plain markdown briefs and move config to a sibling `test.yaml`?

### Options

**A. YAML frontmatter on `test.md`**
- Pro: Single file per test. Same pattern as `SKILL.md` — proven in this codebase.
- Pro: Conductor reads one file to get both config and scenario. No split-brain.
- Pro: Agent loaders already handle frontmatter stripping (the library system does this for SKILL.md).
- Con: Frontmatter must be stripped before feeding `test.md` to the avatar as a prompt. Mild implementation cost.
- Con: If a human opens `test.md`, the frontmatter is noise above the scenario.

**B. Sibling `test.yaml` + `test.md`**
- Pro: Clean separation — config is config, prompt is prompt. Avatar gets pure markdown.
- Pro: No frontmatter stripping needed in the avatar loader.
- Con: Two files per test leaf. At ~50–60 test leaves, that's 50–60 extra files.
- Con: Conductor must read two files per leaf to get the full picture. If `test.yaml` exists without `test.md` (or vice versa), that's an orphan error case.
- Con: Introduces a new file-relationship pattern (paired files) that the leaf contract explicitly avoids ("Nothing else. No notes.md, no history.md…").

**C. No config at all — all tests use conductor defaults**
- Pro: Simplest. `test.md` is the only file. Conductor has one timeout, one concurrency setting.
- Con: Some scenarios legitimately need different timeouts (a mail-dedup test is fast; a multi-agent spawn-and-verify test is slow). One-size-fits-all will either be too generous (wasted budget) or too tight (false INCONCLUSIVE).

### Recommendation: **A — YAML frontmatter on `test.md`.**

Same pattern as SKILL.md. The LingTai library system already strips frontmatter when loading skill content — reuse that mechanism. The frontmatter is minimal (3–5 fields: `timeout`, `max_concurrency`, `depends_on`, `skip_reason`). The conductor reads one file, strips frontmatter for config, passes the rest as the avatar's prompt. No sibling files, no orphan detection, no new patterns.

Example:

```markdown
---
timeout: 180
depends_on:
  - mail-protocol/send/self-send
---

# Scenario: Verify mail deduplication

## Setup
You are an agent with a fresh workdir...
```

**Phase 1 default:** YAML frontmatter. Implement the minimal frontmatter fields the pilot tests actually need (probably just `timeout`). Expand as patterns emerge.

---

## Q4. Per-leaf versioning

**The question:** When a leaf's spec changes, do we bump a version in its `README.md`, or rely entirely on git history + changelog?

### Options

**A. Per-leaf version in `README.md`**
- Pro: Each leaf is self-describing. An agent reading the leaf knows its version without checking git.
- Pro: Enables cross-references like "tested against mail-protocol v2.1."
- Con: ~80–100 version numbers to maintain. Every spec change = two edits (content + version bump). Miss a bump and the version is a lie.
- Con: Version semantics are unclear — what counts as a "major" bump on a single concept's spec? The leaf isn't a release artifact.
- Con: Agents don't reason about versions the way humans do. An agent reading a leaf reads the *content*; a version number doesn't tell it anything the content doesn't.

**B. No per-leaf versioning; rely on git + changelog**
- Pro: Zero maintenance surface. The git commit log is the version history, precise and unforgeable.
- Pro: The `changelog/README.md` leaf (restructured from `reference/changelog.md`) already captures breaking changes at the skill level.
- Pro: The `SKILL.md` version (currently 2.1.0) tracks the skill as a whole — the right granularity for "is my copy up to date?"
- Con: An agent can't say "I'm looking at leaf X version Y" without checking git.
- Con: If two agents compare notes on a leaf, they can't easily tell if they're looking at the same revision.

**C. Timestamp in README frontmatter (not semver)**
- Pro: Cheap to maintain — just stamp the last-modified date.
- Pro: Agents can compare timestamps without semver reasoning.
- Con: Not a real version. Doesn't express "this was a breaking change" vs. "typo fix."

### Recommendation: **B — no per-leaf versioning.**

The skill version in `SKILL.md` is the right granularity. Individual leaves aren't released or branched independently — they're parts of one skill. When a leaf changes, the change lands in a commit, gets noted in the changelog if it's breaking, and the `SKILL.md` version bumps on release. That's three layers of version information (commit → changelog → SKILL.md version), all already there.

Adding per-leaf versions creates 80–100 version fields that will drift, rarely get read, and don't change agent behavior. The test suite is the "did the spec hold?" check — if an agent needs to verify a leaf's accuracy, it runs the test, not a version check.

**Phase 1 default:** No per-leaf versioning. `SKILL.md` version + `changelog/README.md` + git log. Revisit only if cross-skill references become complex enough to need leaf-level version pinning (unlikely in the medium term).

---

## Q5. Test result location

**The question:** Conductor's workdir (proposed) vs. fixed `~/.lingtai-tui/test-results/` (cross-conductor visibility). Trade-off is encapsulation vs. discoverability.

### Options

**A. Conductor's workdir (`<conductor>/test-results/<timestamp>/`)**
- Pro: Self-contained. Each conductor owns its results. Cleanup is `rm -rf <conductor>`.
- Pro: No global state to manage. No naming conflicts between conductors.
- Pro: Consistent with LingTai's agent-isolation model — each agent's workdir is its domain.
- Con: Results die with the conductor's workdir (if the conductor is nuked, results go too).
- Con: Cross-conductor visibility requires knowing each conductor's location. No single place to grep for "all test runs ever."

**B. Fixed global location (`~/.lingtai-tui/test-results/`)**
- Pro: One place for all test results across all conductors. Easy to grep, easy to browse.
- Pro: Results survive conductor lifecycle.
- Con: Naming convention needed to avoid conflicts (`<skill>/<timestamp>/` ?).
- Con: Global mutable state — cleanup is manual, needs a retention policy.
- Con: Assumes exactly one machine/user. Doesn't work cleanly in multi-project or CI contexts.
- Con: Violates agent isolation — the conductor writes outside its own workdir, into a shared global space.

**C. Hybrid — conductor workdir + symlink to global**
- Pro: Best of both worlds.
- Con: Symlinks are fragile and platform-dependent. Over-engineered for v0.

### Recommendation: **A — conductor's workdir.**

The design doc correctly identifies that "test-result archival / trend analysis" is out of scope. For Phase 1, the rollup `INDEX.md` is the primary output — it's human-readable, self-contained, and immediately useful. Where it lives matters less than what it contains.

The conductor's workdir is the natural LingTai pattern: agents own their space, results live with the work, cleanup is destruction. When cross-conductor visibility becomes a real need (e.g., comparing anatomy test results across kernel versions), the pattern will be obvious — probably a symlink or a `test-results/` directory at the skill level (not the global level). But that's a future problem with a future solution.

For Phase 1: `test-results/<ISO-timestamp>/INDEX.md` in the conductor's workdir. If we need to share results, we mail them or symlink them. No global state.

**Phase 1 default:** Conductor's workdir. Revisit when trend analysis or cross-version comparison enters scope.

---

## Summary of Defaults for Phase 1

| Question | Default | Rationale |
|---|---|---|
| Q1: Conductor location | `lingtai-kernel-anatomy/conductor/` | Don't block pilot on umbrella design. Migration cost is trivial. |
| Q2: Leaf naming | `kebab-case/` everywhere | Matches existing convention. Zero churn. |
| Q3: test.md frontmatter | YAML frontmatter | Same pattern as SKILL.md. One file per test. |
| Q4: Per-leaf versioning | None — use SKILL.md version + changelog + git | 100 version fields that never get read aren't worth maintaining. |
| Q5: Test result location | Conductor's workdir | Consistent with agent isolation. Cross-conductor visibility is future work. |

All defaults are revisitable after the Phase 1 pilot surfaces real usage patterns. The pilot is the experiment; these are the starting conditions.

---

## Bonus: §Source line-range formatting convention

**The problem:** The `## Source` table in each leaf's `README.md` references kernel code locations. Line ranges can be written as `233–245` (en-dash), `233—245` (em-dash), or `233-245` (ASCII hyphen). The pilot leaf uses en-dash (`–`). If this is left unstandardized, every leaf author picks their own dash, and the maintenance procedure's grep must match all three — a fragile regex that grows with each new convention.

**The convention:** Use **ASCII hyphen** (`-`, U+002D) for line ranges in `## Source` tables.

```markdown
| Self-send detection | `lingtai_kernel/intrinsics/mail.py` | 233-245 (`_is_self_send`) |
```

**Rationale:**
- Machine-parseable with a trivial regex (`[0-9]+-[0-9]+`), no Unicode-aware matching needed.
- Consistent with how line ranges appear in `git diff` output, GitHub URLs, and VS Code's "Go to Line" input.
- The maintenance procedure grep simplifies from `[0-9]+[–\-][0-9]+` to `[0-9]+-[0-9]+`.
- Typography enthusiasts may prefer en-dash, but the primary consumers of these tables are scripts and agents, not readers of printed documents.

**Scope:** This applies to all `## Source` tables across all leaves. Existing content (pilot leaf, migration checklist) should be normalized before Phase 1 merges. The `phase1-migration-checklist.md` leaf boundary table (§0) is exempt — it uses em-dash for aesthetic readability in the overview table, not as a machine-parseable reference.
