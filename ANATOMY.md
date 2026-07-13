---
related_files:
  - CLAUDE.md
  - CODE_OF_CONDUCT.md
  - CONTRACT.md
  - CONTRIBUTING.md
  - MANIFEST.in
  - README.md
  - SECURITY.md
  - SUPPORT.md
  - dev-guide-skill/SKILL.md
  - docs/references/claude-code-guide.md
  - pyproject.toml
  - setup.py
  - src/lingtai/ANATOMY.md
  - src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/snapshot/ANATOMY.md
  - tests/test_architecture_documents.py
maintenance: |
  This file is both the repository-root anatomy and the normative
  anatomy-of-anatomy for the distributed code navigation system. Keep
  related_files repo-relative, duplicate-free, and linked to real files. Keep
  the root CONTRACT.md reciprocal and update the paired conventions together
  when their boundary changes. Code is the structural source of truth: repair
  stale navigation in the same change that moves files, symbols, connections,
  composition, or state. Preserve the child template and its maintenance rule;
  validate the distributed graph before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# LingTai Distributed Code Navigation Convention

## Purpose

**ANATOMY is the distributed code navigation system.** Each architectural layer
keeps an `ANATOMY.md` beside the code it maps: files, symbols, responsibilities,
connections, composition, and state. Those local maps link into a graph that an
agent can descend from this repository root to the exact code that answers a
structural question.

This file has two roles. It is the repository's top-level map, and it is the
**anatomy of anatomy**: the normative meaning, template, link rules, and
maintenance contract for the distributed navigation system.

`ANATOMY.md` and [`CONTRACT.md`](CONTRACT.md) are a pair, not duplicates:

- Anatomy describes **where code is and how it is composed**. Code is the
  structural source of truth.
- **CONTRACT is the distributed code interface definition system.** It defines
  **how a layer may be used and what it promises**. The contract is normative
  when implementation behavior disagrees.

## Navigation model

Navigation is distributed rather than centralized. The root defines the system
and global entry points; each architectural component maps only the layer it
owns; parent/child and related-file links connect the layers. Do not copy every
local fact into this root file.

For structural questions, descend the anatomy graph: read this file, choose the
relevant component, open its anatomy, and repeat until it points at code. For
enumeration questions such as every callsite or every matching file, use search.
Anatomy is a navigation aid; cited code remains the evidence.

A folder earns an anatomy when a competent agent can reason usefully about it as
an architectural unit without first reading all siblings. Pure helper folders,
single value objects, and trivial leaves do not receive ceremonial anatomies.
If a component owns nested architectural components, each child may own its own
paired anatomy and contract.

The target skeleton for a governed component layer is:

```text
<component>/
├── ANATOMY.md   # distributed code navigation: structure and composition
├── CONTRACT.md  # distributed interface definition: Core/Ports/Adapters
└── ... code
```

Existing anatomy files remain useful navigation during staged migration. A
component enters the paired governed system when its co-located contract is
linked from the root contract. An implementation, Adapter, or navigation-only
Anatomy that owns no separate promise instead points to its one owning
component Contract and explains why no independent local Contract exists. The
full pairing, ownership, progressive-disclosure, and mismatch-reporting rule
lives only in root `CONTRACT.md`; follow it rather than copying it here.

## Frontmatter convention

A root-governed paired component anatomy has exactly two YAML frontmatter
fields, in this order:

1. `related_files`: a non-empty, duplicate-free list of repo-relative regular
   files. It includes the paired `CONTRACT.md` for a governed component, the
   parent and direct-child anatomies needed to traverse the graph, and the code
   files that own the mapped layer.
2. `maintenance`: the canonical generic text in the template below. The root
   uses a root-specific maintenance statement because it also governs the
   system.

Paths MUST be repository-relative, MUST resolve to files, MUST NOT contain `.`
or `..` path segments, and MUST use `/` separators.

## Body convention

A root-governed paired component anatomy starts with one paragraph defining
what the layer is, then uses these five `##` sections once and in this order:

1. `## Components` — files, functions, classes, or child components with
   verified `file:line` citations and one-line purposes.
2. `## Connections` — callers, callees, and data/control flow across the layer.
3. `## Composition` — parent, direct child anatomies, and structurally relevant
   siblings.
4. `## State` — persistent state written and ephemeral state managed.
5. `## Notes` — bounded gotchas or rationale not evident from code.

Root-governed paired component anatomies SHOULD remain near 80 lines. A larger map is evidence that
the layer may contain smaller components. No empty leaf stubs are allowed.
Every structural claim and named symbol in `Components` MUST cite verified code;
links to another anatomy use repo-relative paths.

This root anatomy is the only exception to the component body and size shape: it
also carries the meta-convention and repository-wide entry points.

## Link and pairing semantics

The paired distributed systems obey these structural rules. Root
[`CONTRACT.md`](CONTRACT.md) is the single source for governed-component
pairing, unique implementation/navigation ownership, mutual progressive
disclosure, and fail-loud mismatch reports; do not duplicate that rule here.

1. This root anatomy and root contract list each other in `related_files`.
2. A root-governed component's co-located `ANATOMY.md` and `CONTRACT.md` list
   each other exactly once.
3. Parent/child anatomy links are reciprocal so navigation can descend and
   return. Do not enumerate unrelated downstream callers as graph edges.
4. The component contract owns interface behavior; the component anatomy owns
   structure and composition. Cross-link instead of copying the same rule into
   both files.
5. A structural or composition change updates anatomy in the same PR. A Port,
   Adapter, or behavioral-promise change updates contract and contract tests in
   the same PR. A change affecting both updates the pair together.
6. Orphans, missing targets, duplicate links, one-way pair links, and unpaired
   governed components are defects and MUST fail validation.
7. A capability's manual is a navigation target linked from **both** owner twins:
   the paired `ANATOMY.md` lists it in `related_files` as a route to the manual,
   and the capability `CONTRACT.md` lists the same manual as its interface owner.
   The normative both-edges requirement is owned by root
   [`CONTRACT.md`](CONTRACT.md) `## Design principles` (principle 4); this anatomy
   only names the navigation edge and does not restate that rule. A manual reached
   from only one twin is a missing-edge defect.

## Components

- [`dev-guide-skill/`](dev-guide-skill/) — the repository-local agent dev kit:
  its skill routes agents into the Anatomy and Contract systems and may grow
  focused scripts, references, templates, or assets as real workflows recur.
- [`.github/`](.github/) — GitHub Actions, issue templates, and pull request
  templates.
- [`crates/lingtai-search-sidecar/`](crates/lingtai-search-sidecar/) — Rust file
  search sidecar packaged with the Python runtime.
- [`docs/`](docs/) — durable documentation, plans, language-specific readmes,
  and long-form references.
- [`src/lingtai/`](src/lingtai/) — public package, compatibility surfaces,
  services, and the kernel implementation; descend through
  [`src/lingtai/ANATOMY.md`](src/lingtai/ANATOMY.md).
- [`tests/`](tests/) — pytest suite for runtime, services, tools, packaging, and
  architecture-document validation.

## Root files

- [`ANATOMY.md`](ANATOMY.md) — this repository map and anatomy-of-anatomy.
- [`CONTRACT.md`](CONTRACT.md) — the distributed code interface definition root
  and contract-of-contract.
- [`README.md`](README.md) — public English network entry point; translated
  readmes live under `docs/readmes/`.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — public contributor entry point.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), [`SECURITY.md`](SECURITY.md), and
  [`SUPPORT.md`](SUPPORT.md) — community and safety entry points.
- [`CLAUDE.md`](CLAUDE.md) — short Claude Code entry point; full guidance is
  [`docs/references/claude-code-guide.md`](docs/references/claude-code-guide.md).
- [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) — legal metadata.
- [`pyproject.toml`](pyproject.toml), [`setup.py`](setup.py), and
  [`MANIFEST.in`](MANIFEST.in) — Python packaging and Rust-sidecar build hooks.

## Composition

`pyproject.toml` declares Python package metadata and delegates sidecar build
hooks to `setup.py`. `MANIFEST.in` connects Rust sources and packaged Markdown
resources to source distributions. Runtime source begins under `src/lingtai/`;
long-form material that is not a root entry point remains under `docs/`.

README exposes the repository knowledge network to humans and agents. The repository-local [kernel development skill](dev-guide-skill/SKILL.md)
supplies the workflow and routes each task into this Anatomy graph, the Contract
graph, focused tests, and narrower manuals. The
distributed navigation graph starts here and descends through the anatomies
listed in `related_files`; the distributed interface graph starts at
`CONTRACT.md`. A governed component joins both graphs through its co-located
pair.

Every paired document carries a uniform `maintenance` frontmatter entry that
backlinks to its normative root: each governed component anatomy copies the
canonical anatomy `maintenance` text from this file's `## Template`, and each
governed component contract copies the canonical Maintenance block from
[`CONTRACT.md`](CONTRACT.md). The contract-side canonical block is the single
source of truth for that promise — its exact text, version, and hash are owned
by `CONTRACT.md` and mechanically enforced by
[`tests/test_architecture_documents.py`](tests/test_architecture_documents.py).
This anatomy only points at that rule; it does not restate a second, possibly
divergent, copy of it.

## State

These architecture documents write no runtime state. Git history records their
changes. Each anatomy describes the persistent and ephemeral state owned by its
mapped component; if a code change moves state ownership or changes a schema,
the relevant anatomy changes in the same PR.

## Maintenance

Maintenance is part of reading:

- If code and anatomy disagree structurally, code is normally the current fact;
  repair the anatomy before leaving the change. If the code move itself is a
  defect, report or fix the code and keep the mismatch visible until resolved.
- If code and contract disagree behaviorally, do **not** rewrite the contract to
  match accidental behavior. Treat the implementation as defective unless an
  authorized contract change updates the Port, adapters, version, and tests.
- Verify every touched citation after moves, renames, splits, or ownership
  changes. The anatomy drift checker catches missing/out-of-range targets, not
  semantic misdescription.
- Keep parent/child and Anatomy/Contract pair or owner links reciprocal. Update
  the root convention, its validator, root development skill, README entry,
  and bundled anatomy router together when this system changes.
- Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.

## Template

```markdown
---
related_files:
  - <repo-relative paired CONTRACT.md>
  - <repo-relative parent ANATOMY.md>
  - <repo-relative direct-child ANATOMY.md, when any>
  - <repo-relative mapped code file>
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep this component's ANATOMY.md and CONTRACT.md reciprocal and keep
  parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# <Component Name> Anatomy

<One paragraph defining the architectural layer this folder embodies.>

## Components

- `<symbol>` — purpose (`repo/relative/file.py:line-line`).

## Connections

## Composition

## State

## Notes
```
