---
name: lingtai-kernel-dev
description: >
  Mandatory repository-local development guide for LingTai's Python kernel and
  runtime. Use this before changing code, architecture documents, tests,
  packaging, capabilities, adapters, or developer documentation in
  lingtai-kernel. Routes each task through the exact baseline, the distributed
  ANATOMY/CONTRACT systems, focused validation, and pull-request safety gates.
related_files:
- ANATOMY.md
- CONTRACT.md
- CONTRIBUTING.md
- docs.yaml
- src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md
- tests/test_architecture_documents.py
maintenance: |
  Mandatory repository-local development router; update it, tests/test_architecture_documents.py's public-entry-points test, and README/Anatomy/Contract entry routes together whenever the workflow, the docs.yaml governance pointer, or the pull-request/side-effect gate changes.
---

# LingTai Kernel Development

Read this skill before every development task in this repository. It owns the
**workflow**; Anatomy owns structure and Contract owns interface promises.
Follow its links instead of copying the root documents into this file.

Above all, root [`CONTRACT.md`](../CONTRACT.md) `## Design principles` is
mandatory reading, and you MUST apply every one of those principles to each
change — including the i18n gate, progressive disclosure, the
manual-per-capability rule, and manual discoverability from **both** the
capability `CONTRACT.md` and its paired `ANATOMY.md` (`related_files` on both
twins; one edge alone is a defect). Route each change to the manual that teaches
the capability it touches.

## First establish the task and baseline

1. Re-read the latest human or maintainer instruction. Separate the requested
   change from suggested follow-up work and from unauthorized side effects.
2. Name the selected baseline: normally live `origin/main`, or an explicit tag
   or commit chosen by the maintainer.
3. Work in a real repository worktree. Before analysis or editing, prove the
   worktree is clean and `HEAD` equals the selected baseline. A directory name
   or recorded SHA without equality is not enough.
4. Use a focused branch and keep unrelated local/runtime worktrees untouched.

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for the public contribution
workflow and its route to the full coding-agent and test reference.

## Read the distributed systems before editing

Use progressive disclosure in this order:

1. Read root [`ANATOMY.md`](../ANATOMY.md), then descend through the nearest
   child anatomy until cited code answers where the relevant files,
   connections, composition, and state live.
2. Read root [`CONTRACT.md`](../CONTRACT.md), beginning with `## Design
   principles`. If the component is governed, read its paired local contract
   before changing its interface or expected behavior.
3. Read the cited code and narrow tests. Anatomy is navigation, not evidence in
   place of code; Contract is the normative promise, not a description to weaken
   when implementation drifts.
4. Load a narrower manual only when the task needs its commands, examples, or
   troubleshooting. Do not preload unrelated references.

The
[`lingtai-kernel-anatomy`](../src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md)
skill owns how to enter and descend those two graphs; this skill owns how to
develop and validate a change.

## Make the smallest complete change

Before editing, state the relevant invariant, the intended variation axis, and
the explicit non-goals. Prefer one behavior-locked boundary or vertical slice
over a directory reshuffle or speculative abstraction.

For every code or architecture-document change, assess both distributed systems:

- Files, symbols, connections, composition, or state ownership changed:
  update the relevant Anatomy in the same PR.
- Port, Adapter, Behavior, error, ordering, retry, cancellation, recovery, or
  state semantics changed: update the relevant Contract and shared contract
  tests in the same PR.
- Both changed: update the pair together.
- Neither changed: record that both were checked; do not create documentation
  churn to simulate compliance.

Follow the repair direction defined by root `CONTRACT.md` `## Maintenance
contract`: verified code is normally the structural truth for Anatomy, while
Contract is normative for interface and Behavior, so implementation drift is a
defect unless a maintainer explicitly authorizes a promise change. Do not create
a second graph registry; maintain YAML `related_files` around the nodes you
touch.

### Classify the Anatomy/Contract relationship first

Root [`CONTRACT.md`](../CONTRACT.md) owns the full pairing and ownership rule: a
governed architectural component owns reciprocal Anatomy/Contract twins, while an
implementation, Adapter, or navigation-only Anatomy instead points to exactly one
owning governed Contract and explains why it has no independent local Contract.
Never manufacture an empty or duplicate Contract for filename symmetry. If the
relationship does not satisfy the root rule, stop and report the root-defined
mismatch fields and suggested action; do not normalize or auto-fix it without
authorization.

### Keep Maintenance guidance aligned

When you create or update a child component `CONTRACT.md`, keep its
`maintenance` note concise and aligned with root [`CONTRACT.md`](../CONTRACT.md):
retain complete, safe `related_files`, reciprocal Anatomy/Contract and ownership
links, and the rule to update the pair when structure or normative behavior
changes. The note is documentation, not a byte-identical snapshot; do not add a
second registry or a generated/hash-based maintenance mechanism.

## Validate in layers

Run the narrowest decisive checks first, then the affected broader checks. At a
minimum:

```bash
python -m pytest -q <targeted-tests>
python -m pytest -q tests/test_architecture_documents.py  # when either graph changes
# Run the canonical Anatomy drift checker in --check mode (see the Anatomy skill).
git diff --check
```

`tests/test_architecture_documents.py` validates frontmatter path safety, root
and child pairing, reciprocal graph links, and unique ownership for linked
implementation Anatomies. If it reports a mismatch, stop and report the offending
path and links rather than silently normalizing or auto-fixing documents.

Also run package, import, build, adapter, or source-drift tests when the diff
crosses those boundaries. Use the repository virtual environment, inspect every
non-zero exit, and never report a timed-out or interrupted suite as passing.
Review the final diff against the human instruction and name any untested risk.

## Pull-request and side-effect gate

Use a pull request; never push directly to `main`. Before committing, pushing,
or opening the PR:

1. re-read the latest human scope and authorization;
2. verify live base equality and stop if the base moved;
3. verify local Git author identity and the intended GitHub CLI account;
4. ensure the staged diff contains only the reviewed change;
5. capture focused validation evidence and unresolved risks.

Commit, push, open/close/merge PRs, publish, install, refresh, release, or change
configuration only within the maintainer's explicit authorization for that
specific side effect. Opening a PR does not imply permission to merge it.

## Keep documentation frontmatter current

Every Markdown file in the repository — including this file's own
frontmatter above — must carry `related_files` and `maintenance` YAML
metadata, checked by [`docs.yaml`](../docs.yaml) and validated by
`scripts/check_docs_governance.py` / `tests/test_docs_governance.py`. This
is a separate, generic baseline from the Anatomy/Contract frontmatter
schemas above — do not conflate them. When you add or edit a doc, fill in
real `related_files`/`maintenance`; when you create a new governed
component ANATOMY.md/CONTRACT.md, its stricter specialized schema already
satisfies this baseline.

## Grow this as the repository agent dev kit

This directory owns the kernel repository's complete reusable development kit,
not only this entry document. Add supporting material when real repeated work
justifies it: `scripts/` for deterministic checks, maintenance, or generation;
`references/` for deep procedures loaded only when needed; `assets/` for
templates, examples, or fixed resources. Do not create empty directories or copy
repository rules into support files.

Keep one `SKILL.md` entry and let it route progressively: detailed architecture
belongs in Anatomy/Contract, and tool-specific recipes belong in the narrower
manuals they own. When this workflow changes, update this skill and the
README/Anatomy/Contract entry routes together; change the normative root
documents only when their own meaning, schema, or promises change.
