# Anatomy Maintenance Procedure

**Status:** Draft — pending Phase 1 pilot validation.
**Date:** 2026-04-30
**Scope:** `lingtai-kernel/src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/`
**Supersedes:** Nothing (new).
**Cross-refs:** [Anatomy Tree Restructure](../../2026-04-30-anatomy-tree-restructure.md) · [Conductor README](conductor/README.md)

---

## Purpose

The anatomy tree restructure solves two problems: specs without verification (design doc §Motivation), and tests without co-location. But a third problem emerges once the tree exists: **drift**. Kernel code evolves — refactors, renames, protocol changes, new fields — while anatomy specs and tests sit on disk, unbothered. The dual-write principle ("update anatomy when you write code") is a social contract, and social contracts decay.

This procedure closes the loop. It is the scheduled, mechanical check that anatomy still describes the kernel that actually runs. Think of it as `git fsck` for specs: not exciting, but if you skip it long enough, you wake up one morning with a spec that lies.

---

## Trigger points

Run this procedure when any of these occur:

### 1. After a major kernel refactor, new protocol, or rename lands

If `git log $TAG..HEAD -- src/lingtai/` shows commits touching core subsystems — especially `intrinsics/mail.py`, `base_agent.py`, `core/email/`, `core/psyche/`, or any file listed in a leaf's `## Source` section — that session should end with a maintenance run. Use the latest release tag as anchor (same as Step 1); for ad-hoc checks, the maintainer may narrow the range at their discretion.

### 2. Before each release (release-gate)

No release ships with a stale anatomy. The release checklist includes:

```
[ ] Maintenance procedure run — all PASS, no unresolved FAIL
[ ] maintenance-log/<date>.md attached to release notes
```

This is the hard gate. Anatomy drift is a release blocker, not a "nice to have."

### 3. When anatomy itself is modified

Editing a `README.md`, adding a `test.md`, restructuring a leaf directory — these are internal anatomy changes. They require a lightweight variant of this procedure:

- Skip "pull recent diffs" (you know what changed — you changed it).
- Run the full audit on *neighboring* leaves (those listed in `## Related`).
- Run conductor on the modified leaf (and any leaf whose `## Contract` section was touched).
- Update `changelog/README.md`.

This variant is abbreviated because the trigger is deliberate, not a surprise.

---

## Procedure

Five steps. Steps 1–3 are the "audit" — fast, mechanical, low-cost. Steps 4–5 are the "proof" — slower, more expensive, but they produce the evidence that matters.

### Step 1 — Pull recent diffs

Identify what changed in the kernel since the last release. The anchor is the **latest release tag** — not a fixed commit count. Release tags are semantic boundaries ("this is shippable"), making them the natural scope for anatomy self-check. This aligns with trigger point 2 (release-gate): the same tag that gates a release gates the anatomy audit.

```bash
# Find the latest release tag
TAG=$(git describe --tags --abbrev=0)
# Or if using GitHub releases:
# TAG=$(gh release view --json tagName -q .tagName)

# Show commits since the last release
git log $TAG..HEAD --oneline

# Show the actual diff for audit
git diff $TAG..HEAD -- src/lingtai/
```

For non-release maintenance runs (trigger point 1: after a major refactor, or trigger point 3: after anatomy edits), use the last maintenance run as the anchor instead:

```bash
# Find the last maintenance run date (from changelog/README.md or maintenance-log/)
# Then use that as the lower bound:
git log --oneline --since="$LAST_MAINTENANCE_DATE" -- src/lingtai/
git diff $LAST_MAINTENANCE_TAG..HEAD -- src/lingtai/   # if you tagged it
```

Output: a list of commits and a diff. If the diff is empty, skip to Step 5 (log "no kernel changes since $TAG") and stop. If the diff is large, proceed to Step 2 — the audit will tell you what *actually* drifted, regardless of diff size.

### Step 2 — Audit `## Source` references

Every leaf's `README.md` has a `## Source` section with `file:line` references pointing to kernel source. These are the primary drift targets. Scan them all.

```bash
# Extract all Source references from the anatomy tree
# (line ranges use ASCII hyphen per open-questions-draft §Source convention)
cd /path/to/lingtai-kernel-anatomy
grep -rn "lingtai_kernel/" --include="README.md" | grep -E ":[0-9]+|[0-9]+-[0-9]+"

# For each reference, verify the symbol still exists in the kernel source.
# Line numbers drift; symbols are the anchor.
# Example check:
python3 -c "
import re, sys, pathlib

ref = 'lingtai_kernel/intrinsics/mail.py:233'  # example
file_path, line_hint = ref.rsplit(':', 1)
src_root = pathlib.Path('/path/to/lingtai-kernel/src')
target = src_root / file_path

if not target.exists():
    print(f'MISSING: {file_path} — file deleted or renamed')
    sys.exit(1)

# Check if the symbol near line {line_hint} still exists
lines = target.read_text().splitlines()
hint = int(line_hint)
window = lines[max(0, hint-5):hint+10]
print(f'Lines {max(1,hint-5)}-{hint+10} in {file_path}:')
for i, line in enumerate(window, start=max(1,hint-5)):
    print(f'  {i}: {line}')
"
```

For a more systematic approach, build a reference index:

```
# Generate: leaf_path | file_ref | line_ref | symbol_hint
# Output as a CSV or table for manual review of anything that looks wrong.
```

**What you're checking:**
- Does the referenced file still exist at that path?
- Does the function/class/constant named near that line still exist?
- Is the `## Contract` section still consistent with what the code actually does?

Line numbers are *hints*, not anchors. If `mail.py` grew by 40 lines because a new function was added at line 200, the self-send detection at line 233 may now be at line 273. The symbol `_is_self_send` still exists — that's the anchor. Line-number-only drift is a cosmetic fix, not a crisis.

### Step 3 — Classify drift

For each leaf where something is off, classify the drift into one of four categories:

| Drift type | Definition | Action | Severity |
|---|---|---|---|
| **Line-number offset** | File and symbol exist; line number shifted. | Auto-update the `## Source` line number. Scriptable. | Cosmetic |
| **Symbol renamed** | Function/class was renamed (e.g. `_is_self_send` → `_is_selfsend`). | Verify the new name (grep the kernel source). If unambiguous, auto-replace. If ambiguous (multiple candidates), flag for human review. | Low |
| **Contract changed** | New fields added/removed (e.g. mailbox message gains a `priority` field), handshake flow changed, file format evolved. | Must update `README.md` `## Contract` section AND corresponding `test.md` `## Pass criteria`. This is the critical drift — a spec that lies about the contract is worse than no spec. | **High** |
| **Feature removed** | Entire code path deleted. The leaf describes something that no longer exists. | Delete the leaf. Note in `changelog/README.md`: "Leaf `<path>` deleted — `<feature>` removed in commit `<hash>`." | Final |

**Decision rule:** If a leaf's `## Contract` section would be *false* if read by a developer who only has this leaf and the current kernel code — it's a contract change. Everything else is cosmetic.

For contract changes, also check whether the corresponding `test.md` pass criteria are still accurate. A test that passes against a contract that no longer holds is *more* dangerous than no test at all — it breeds false confidence.

### Step 4 — Run conductor before/after

This is the evidence-gathering step. Run the conductor twice.

**Before run** (baseline):

```bash
# Run conductor against current anatomy + current kernel
# Capture the rollup INDEX.md
python3 conductor.py --root /path/to/anatomy --version "$(git rev-parse --short HEAD)"
# Record: test-results/<timestamp-before>/INDEX.md
```

**Apply updates** — fix any drift identified in Steps 2–3. Commit the anatomy changes.

**After run** (post-fix):

```bash
python3 conductor.py --root /path/to/anatomy --version "$(git rev-parse --short HEAD)"
# Record: test-results/<timestamp-after>/INDEX.md
```

**Compare the two INDEX.md files:**

- **New FAILs in "before" that become PASS in "after"** = drift was real, fix worked. Good.
- **Same FAILs in both** = fix incomplete, or the kernel changed in a way the anatomy update didn't capture. Investigate.
- **New FAILs in "after" that were PASS in "before"** = the anatomy update introduced a regression. Investigate.
- **Persistent INCONCLUSIVE** = environment issue, not drift. Note it, move on.

This before/after comparison is the core of the maintenance procedure. Without it, you're just doing a drive-by file edit and hoping it's correct.

**Cost note:** A full conductor run against ~50–60 test.md scenarios (estimated for the full tree) at default concurrency 8 with 5-minute timeouts could take 15–40 minutes and consume non-trivial LLM budget. This is acceptable for release-gates and monthly maintenance runs, but not for per-PR CI. The design doc explicitly scopes anatomy tests to "on-demand, not on every commit" — this procedure follows that constraint.

### Step 5 — Update changelog and maintenance log

**Update `changelog/README.md`:**

One entry per maintenance run. Format:

```markdown
## 2026-05-15 — Maintenance run

**Commit range:** v0.4.0..HEAD (latest release tag)
**Kernel changes audited:** 14 commits
**Leaves affected:** 3 (mail-protocol/send/self-send, mail-protocol/receive/atomic-write, molt-protocol/basic)
**Drift found:** 2 line-offset fixes, 1 contract change (mailbox message schema — `priority` field added)
**Conductor before:** 48 PASS / 2 FAIL / 1 INCONCLUSIVE
**Conductor after:** 51 PASS / 0 FAIL / 0 INCONCLUSIVE
**Log:** [maintenance-log/2026-05-15.md](../maintenance-log/2026-05-15.md)
```

**Write `anatomy/maintenance-log/<date>.md`:**

Detailed report. Contains:

1. **Scope** — what commit range was audited, how many kernel diffs were reviewed.
2. **Reference audit results** — table of all `## Source` references checked, with status (OK / offset-fixed / symbol-renamed / contract-changed / feature-removed).
3. **Drift classification** — each affected leaf with its drift type, the fix applied, and the commit that fixed it.
4. **Conductor before/after diff** — the two INDEX.md rollups side by side, with annotations on what changed and why.
5. **Open questions** — anything that couldn't be auto-resolved, deferred to human review.

This log is the audit trail. It answers "who checked this, when, what was found, what was fixed."

**Append to `anatomy/AUDIT.log`:**

For each action taken during this maintenance run, append one line to `AUDIT.log`:

```bash
# Example: after fixing a spec and running conductor
echo -e "2026-05-15 21:30 UTC\tspec-update\tmail-protocol/send/self-send\twake-nap signature updated" >> AUDIT.log
echo -e "2026-05-15 21:31 UTC\tsource-sync\tmail-protocol/send/self-send\tlines 233-245 → 268-280" >> AUDIT.log
echo -e "2026-05-15 21:35 UTC\tconductor-pass\tmail-protocol\t6/6 PASS" >> AUDIT.log
```

Each action gets its own line. The conductor result is a single aggregate line. Do not batch multiple actions into one line — the log is a ledger, not a summary.

---

## Who executes?

Three options, in decreasing order of automation:

### Option A — Fully automated

A conductor-like agent runs the entire procedure on a schedule or trigger. It opens a PR with fixes and the maintenance log. Human reviews and merges.

**Pros:** Consistent, never skipped, catches drift fast.
**Cons:** Auto-fixing contract changes is dangerous — an agent might update a spec to match broken code rather than flagging a code bug. PR review still requires human judgment. Cost is non-trivial.

### Option B — Semi-automated (recommended)

An agent runs Steps 1–3 (pull diffs, audit references, classify drift), produces a structured **drift report**, and presents it to a human. The human reviews the report, approves or rejects fixes, and optionally instructs the agent to apply approved fixes and run Steps 4–5 (conductor before/after, changelog update).

**Pros:** Human judgment on contract changes (the hard part), mechanical rigor on reference scanning (the tedious part). The agent does the work that benefits from automation; the human does the work that requires understanding.
**Cons:** Requires a human in the loop. If the human is busy, the report sits.

### Option C — Fully manual

The procedure document is the deliverable. A human follows it step by step.

**Pros:** Zero infrastructure cost.
**Cons:** Will be skipped. Humans don't follow 5-step checklists reliably under deadline pressure. This defeats the purpose.

### Recommendation: Option B — Semi-automated

**Reasoning:** The split between mechanical audit (Steps 1–3) and judgment-dependent action (Steps 4–5) is natural and clean. An agent can grep, diff, and classify faster and more reliably than a human. But contract changes — the critical drift type — require understanding *intent*: did the code change because the protocol evolved (update the spec), or because the code has a bug (fix the code)? This is the judgment call that shouldn't be automated.

The implementation is straightforward:
1. A scheduled agent (or a human-triggered agent via "run anatomy maintenance") performs the audit.
2. It writes a drift report to `anatomy/maintenance-log/<date>-draft.md` with a classification table.
3. It mails the human with the report and asks: "Approve these fixes? Any contract changes need code-side investigation?"
4. The human replies. For approved fixes, the agent applies them, runs the conductor, updates the changelog, and promotes the draft to `<date>.md`.

This mirrors the conductor's own design philosophy: agents handle the mechanical parallelism, humans handle the judgment. The maintenance procedure is just another test scenario — one where the "pass criteria" are "anatomy matches kernel."

---

## Output contract

Every maintenance run produces exactly four artifacts:

> **Path convention:** All paths in this contract are relative to the anatomy root
> (`src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/`), unless stated otherwise.

| Artifact | Location | Lifetime | Purpose |
|---|---|---|---|
| **Maintenance log** | `maintenance-log/<date>.md` | Permanent | Audit trail — scope, drift found, fixes applied, conductor diff |
| **Updated leaf files** | `README.md` and/or `test.md` within affected leaves | Permanent (as part of anatomy tree) | The actual spec/test corrections |
| **Changelog entry** | `changelog/README.md` (append) | Permanent | One-line summary in the running changelog |
| **AUDIT.log entries** | `AUDIT.log` (append) | Permanent | Machine-readable event log — one line per action, append-only |

The `maintenance-log/` directory lives at the anatomy root, sibling to `changelog/` and the protocol directories. It is `git`-tracked. Each file is self-contained — no cross-file state, no database, no external service.

---

## AUDIT.log — append-only event log

**Location:** Anatomy root, alongside `SKILL.md` and the future `MAINTENANCE.md`.
Example: `lingtai-kernel-anatomy/AUDIT.log`

### Relationship to maintenance-log

Both coexist, neither replaces the other:

| | **maintenance-log** | **AUDIT.log** |
|---|---|---|
| Audience | Human | Machine |
| Depth | Deep — full report per maintenance event | Shallow — one line per action |
| Frequency | Sparse — once per maintenance run | Dense — every action appends a line |
| Content | Scope, changed leaves, conductor before/after diff | What changed, where, with what effect |

Think of it as: maintenance-log is the *memoir*, AUDIT.log is the *ledger*.

### Format

One event per line, four tab-separated columns:

```
<YYYY-MM-DD HH:MM UTC>\t<kind>\t<scope>\t<detail>
```

| Column | Description | Example |
|---|---|---|
| `YYYY-MM-DD HH:MM UTC` | Timestamp in UTC (matches LingTai mail system convention) | `2026-05-01 21:30 UTC` |
| `kind` | Event type (see below) | `spec-update` |
| `scope` | Leaf path or affected area | `mail-protocol/send/dedup` |
| `detail` | One-line description of what changed | `_dup_free_passes 2→3` |

### Kind values

| Kind | Meaning |
|---|---|
| `spec-update` | `README.md` §Contract or §Source changed |
| `test-update` | `test.md` steps or pass criteria changed |
| `test-add` | New `test.md` added to an existing leaf |
| `test-del` | `test.md` removed from a leaf |
| `leaf-add` | New leaf directory created |
| `leaf-del` | Leaf directory deleted |
| `source-sync` | §Source line-number batch correction (no contract change) |
| `conductor-pass` | Conductor run passed (all scenarios green) |
| `conductor-fail` | Conductor run had failures |

### Example

```
2026-05-01 21:30 UTC	spec-update	mail-protocol/send/dedup	_dup_free_passes 2→3
2026-05-01 21:31 UTC	test-update	mail-protocol/send/dedup	第三条标准: expect ≤2 → ≤3
2026-05-01 21:35 UTC	conductor-pass	mail-protocol	6/6 PASS
```

### Contract

- **Append-only:** Lines may be added. Lines must never be modified or deleted.
- **Automated writes:** During a maintenance run, Step 5 appends one or more lines — one per action taken (each `spec-update`, each `test-update`, each `source-sync`, plus a `conductor-pass` or `conductor-fail` for the aggregate run).
- **Manual writes:** When a developer edits anatomy outside of a maintenance run (trigger point 3), they should manually append the relevant line. This is a soft expectation, not a hard gate.
- **Consumers:** Scripts, CI checks, and trend analysis tools read this file as a machine-parseable changelog. Keep it greppable.

---

## Relationship to the dual-write principle

The anatomy tree restructure rest introduces the **dual-write principle**: when you write kernel code that changes a protocol, you also update the anatomy spec. This is the *author-time* guarantee — the developer's discipline at the moment of the change.

This maintenance procedure is the *verification-time* guarantee — a scheduled, mechanical check that the author-time discipline held. It closes the feedback loop between kernel and anatomy:

```
Kernel code changes
    ↓ (dual-write at author time)
Anatomy spec + test updated
    ↓ (this procedure, scheduled)
Conductor verifies spec matches kernel
    ↓ (if drift found)
Maintenance log + fix
    ↓ (fix applied)
Anatomy re-verified
```

Without this procedure, the dual-write principle is aspirational. With it, it's enforced. The procedure doesn't replace the developer's responsibility to update anatomy when they change code — it catches the cases where they didn't, couldn't, or didn't realize they needed to.

This is analogous to how `mypy` doesn't replace writing correct types — it catches the cases where you didn't. Both are feedback loops that turn a social contract into a mechanical check.

---

## Appendix A — Quick reference for the semi-automated agent

When triggered ("run anatomy maintenance"), the agent does:

1. `TAG=$(git describe --tags --abbrev=0)` → `git log $TAG..HEAD --oneline`
2. `git diff $TAG..HEAD -- src/lingtai/`
3. `grep -rn "lingtai_kernel/" --include="README.md"` from anatomy root
4. For each reference: verify file exists, verify symbol exists, classify drift
5. Write `maintenance-log/<date>-draft.md` with classification table
6. Mail human with report, ask for approval
7. On approval: apply fixes, run conductor, compare before/after, update changelog, append to AUDIT.log, promote draft to `<date>.md`

**Estimated time:** 2–5 minutes for audit (agent), 5–30 minutes for human review (depends on drift count), 15–40 minutes for conductor runs (agent, depends on test count and concurrency).

---

## Appendix B — Drift classification decision tree

```
For each leaf with a ## Source reference:
  Does the referenced file exist?
    NO  → Is the entire feature deleted?
            YES → DRIFT: feature-removed. Delete leaf, note in changelog.
            NO  → File was renamed. Find new path. DRIFT: symbol-renamed (auto-fixable if unambiguous).
    YES → Does the function/class at the reference line exist?
            NO  → Was it renamed? (grep for similar names)
                    YES → DRIFT: symbol-renamed.
                    NO  → Function was deleted but feature still exists (refactored). DRIFT: contract-changed (needs human review).
            YES → Did the line number shift?
                    YES → DRIFT: line-offset. Auto-fix.
                    NO  → Does the ## Contract section match current code behavior?
                            YES → No drift. ✓
                            NO  → DRIFT: contract-changed. Must update README.md AND test.md pass criteria.
```

---

## Appendix C — Concurrency with existing workflows

This procedure does not replace:

- **pytest** — Unit/integration tests on data structures and code paths. Anatomy tests verify observable system behavior. Different layers.
- **Code review** — Reviewers should check "did the anatomy get updated?" but this procedure catches it if they don't.
- **git blame** — Useful for understanding *why* a reference drifted. This procedure tells you *that* it drifted. Use both.

The procedure *does* replace the implicit assumption that "anatomy is correct because it was correct last time we looked." Assumptions decay. Procedures don't.
