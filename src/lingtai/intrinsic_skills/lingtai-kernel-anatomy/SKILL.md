---
name: lingtai-kernel-anatomy
description: >
  Router for LingTai's distributed code navigation system. Use this before
  navigating, creating, or maintaining kernel ANATOMY.md files. The normative
  anatomy-of-anatomy now lives at the repository-root ANATOMY.md; this skill
  explains how an agent enters and descends that graph, how it pairs with the
  distributed CONTRACT.md interface-definition graph, and what to do when code
  and navigation disagree.
version: 0.3.0
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- ANATOMY.md
- src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/check_anatomy_drift.py
- src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/bench_agent_session_rebuild.py
maintenance: |
  Tracks the ANATOMY.md/CONTRACT.md convention it routes into; update when the root anatomy-of-anatomy or the pairing/link rules it summarizes change.
---

# LingTai Kernel Anatomy — Navigation Router

## Canonical source

The repository-root [`ANATOMY.md`](../../../../ANATOMY.md) is the normative
**anatomy of anatomy**: the template, frontmatter and body conventions,
component-grain gate, link/pairing semantics, and maintenance contract. Root
[`CONTRACT.md`](../../../../CONTRACT.md) owns the governed-component pairing,
ownership, mutual-progressive-disclosure, and fail-loud mismatch rule. Read
them there; do not maintain a competing convention in this skill.

- **ANATOMY** describes where code lives and how it composes. Code is the
  structural source of truth.
- **CONTRACT** defines each layer's Core, Ports, Adapters, promises, and
  expected agent behavior. Contract is normative when implementation behavior
  disagrees.

When this skill is read from an installed package without a source checkout,
locate the checkout you intend to modify and read *its* root `ANATOMY.md`
before editing. A packaged copy is routing help, not evidence that an arbitrary
local checkout follows the same revision.

## Navigation workflow

1. Open the repository-root `ANATOMY.md`.
2. Read its `Components` and `Composition` sections.
3. Choose the relevant child anatomy and descend; repeat until the local
   anatomy points at an exact code citation.
4. Open the cited code. Anatomy is navigation; code is evidence.

For enumeration questions — every callsite, matching file, or import — use
search after anatomy identifies the correct territory.

The current source root is `src/lingtai/`; the kernel implementation descends
through [`src/lingtai/kernel/ANATOMY.md`](../../kernel/ANATOMY.md).

## Maintenance direction

Who repairs drift depends on which agent you are:

- **Coding agents** update the affected anatomy in the same commit as the code
  change that moved files, symbols, ownership, connections, composition, or
  state.
- **LingTai agents** report drift as issues, mail, or PR proposals. Do not
  silently fix.

The repair direction differs by document:

- **Code vs Anatomy:** code is normally the current structural fact. Repair
  stale paths, citations, connections, composition, or state descriptions. If
  the code move itself is defective, fix or report the code rather than
  encoding a false map.
- **Code vs Contract:** do not rewrite the promise to match accidental
  behavior. Treat implementation as defective unless an authorized contract
  change updates the Port, affected Adapters, contract version, and shared
  tests.

Verify every touched citation, then run the repository's architecture-document
validator and the drift checker below.

## Drift checker

This skill owns the canonical advisory citation-rot checker. Run it from the
repository root (cwd is taken as the repo root):

```bash
# Report only; exits 0 even when drift is found.
python src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/check_anatomy_drift.py
# CI / pre-commit gate: exits 1 if any drift is found.
python src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/check_anatomy_drift.py --check
# Narrow the scan (default: src).
python src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/check_anatomy_drift.py --root src/lingtai/kernel
```

It catches only mechanical citation rot — a `file.py:line` target that is
missing or past end-of-file. An in-range citation can still point at the wrong
code, so an agent must open the cited line to confirm the claim.

`scripts/bench_agent_session_rebuild.py` is the companion benchmark cited by
`src/lingtai/kernel/ANATOMY.md` for the tiered `rebuild_agent_session_from_events()`
path; it takes `--events`/`--molt-every` against a synthetic temp agent dir, or
`--agent-dir <path>` to time an existing one.

## Fallback essentials

If the root convention cannot yet be opened, keep these minimal safety rules:

- Each anatomy maps one architectural layer beside its code.
- Components cite verified `repo/relative/file.py:line-line` evidence.
- Parent/child anatomy links and governed Anatomy/Contract pair links are
  reciprocal.
- Pure implementation detail stays out; user manuals, rationale archives, and
  interface promises belong elsewhere.
- Missing files, out-of-range citations, or one-way links are defects, but only
  reading the cited code can confirm semantic correctness.

Return to the root `ANATOMY.md` as soon as the checkout is available; it is the
source of truth for the complete template and rules.
