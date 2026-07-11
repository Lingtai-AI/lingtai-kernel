---
name: lingtai-kernel-anatomy
description: >
  Router for LingTai's distributed code navigation system. Use this before
  navigating, creating, or maintaining kernel ANATOMY.md files. The normative
  anatomy-of-anatomy now lives at the repository-root ANATOMY.md; this skill
  explains how an agent enters and descends that graph, how it pairs with the
  distributed CONTRACT.md interface-definition graph, and what to do when code
  and navigation disagree.
version: 0.2.0
last_changed_at: "2026-07-11T01:19:00-07:00"
---

# LingTai Kernel Anatomy — Navigation Router

## Canonical source

The repository-root [`ANATOMY.md`](../../../../ANATOMY.md) is the normative
**anatomy of anatomy**. It defines the distributed navigation system, YAML and
body template, pairing/link rules, component-grain gate, and maintenance
contract. Do not maintain a second competing convention in this skill.

When this skill is read from an installed package without a source checkout,
locate the checkout you intend to modify and read its root `ANATOMY.md` before
editing. A packaged copy is routing help, not evidence that an arbitrary local
checkout follows the same revision.

## Two paired distributed systems

- **ANATOMY is the distributed code navigation system.** It describes where
  files and symbols live, how layers connect and compose, and where state lives.
  Code is the structural source of truth.
- **CONTRACT is the distributed code interface definition system.** It defines
  each layer's Core, inbound/outbound Ports, Adapters, code-interface promises,
  expected agent behavior, and conformance tests. Contract is normative when
  implementation behavior accidentally disagrees; manuals and skills explain
  how agents fulfill its obligations.

A root-governed architectural component keeps `ANATOMY.md` and `CONTRACT.md`
beside its code. The pair cross-links but does not duplicate content.

## Navigation workflow

For a structural question:

1. Open the repository-root `ANATOMY.md`.
2. Read its `Components` and `Composition` sections.
3. Choose the relevant child anatomy and descend.
4. Repeat until the local anatomy points at the exact code citation.
5. Open the cited code. Anatomy is navigation; code is evidence.

For enumeration questions — every callsite, matching file, or import — use
search after anatomy identifies the correct territory.

The current source root is `src/lingtai/`; the kernel implementation descends
through `src/lingtai/kernel/ANATOMY.md`.

## When a layer earns a pair

Create or govern a component layer when a competent agent can reason about it as
an independent architectural unit and it owns a meaningful responsibility or
interface boundary. Do not create ceremonial anatomies for trivial helper
folders, single value objects, or one-function leaves.

During staged migration, existing anatomy files remain useful navigation. A
component joins the paired governed system when its co-located contract is
linked from the root contract; from then on, the reciprocal pair and
parent/child graph rules in root `ANATOMY.md` apply.

## Maintenance direction

The repair direction differs by document:

- **Code vs Anatomy:** code is normally the current structural fact. Repair
  stale paths, citations, connections, composition, or state descriptions in
  the same PR. If the code move itself is defective, fix/report the code rather
  than encoding a false map.
- **Code vs Contract:** do not rewrite the promise to match accidental behavior.
  Treat implementation as defective unless an authorized contract change
  updates the Port, affected Adapters, contract version, and shared tests.

Every code change that moves or renames mapped symbols, changes ownership or
connections, or changes persistent/ephemeral state updates the relevant anatomy
in the same commit. Verify touched citations and run the repository's
architecture-document validator plus the anatomy drift checker.

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
