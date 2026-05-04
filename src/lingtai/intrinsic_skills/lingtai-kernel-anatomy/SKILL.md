---
name: lingtai-kernel-anatomy
description: >
  Self-introspection portal for a LingTai agent that wants to understand its
  own kernel — what it is made of, how its parts connect, where in the code
  any given behavior actually lives. This skill is the entrance, not the
  content. The real anatomy lives in `ANATOMY.md` files distributed across
  the kernel source tree, one per concept-boundary folder, written next to
  the code they describe.

  Reach for this skill when:
    - You feel something is off about your own behavior and want to compare
      what you experienced against what the code actually does.
    - You want to know what subsystems compose you (intrinsics, capabilities,
      LLM layer, services, mailbox, soul, molt, MCP, ...) and where each one
      lives.
    - You are about to ask "how does X work in me" and want to answer from
      the code instead of guessing.

  How to use:
    1. Read this file (you are here).
    2. Open `src/lingtai_kernel/ANATOMY.md` — the kernel root anatomy. It
       enumerates the direct subfolders and files of the kernel and tells
       you which one to descend into next.
    3. Descend into the relevant child `ANATOMY.md`. Repeat until you reach
       the leaf where your question lives.
    4. Read the cited code. The anatomy is a navigation aid; the code is the
       truth.
    5. If anatomy disagreed with the code, update the anatomy before you
       leave the file. Reading and maintaining are the same act.

  Note on history: prior versions of this skill (v2.x) shipped 10 topical
  reference files (file-formats, mail-protocol, mcp-protocol, runtime-loop,
  ...) totalling ~3000 lines. Those files were deleted in v3.0.0 and the
  content moved into per-folder `ANATOMY.md` files adjacent to the code.
  If you remember those file names, they no longer exist — descend the
  ANATOMY tree instead.
version: 3.0.0
---

# LingTai Kernel Anatomy

This skill is the **LingTai-agent-facing entrance** to your own kernel anatomy. The matching coding-agent entrance is `src/lingtai_kernel/ANATOMY.md` in the kernel repository — same destination, different doorway.

## What an `ANATOMY.md` is

An `ANATOMY.md` file is the **structural description of one folder of code**, written for an agent reader, sitting next to the code it describes.

It is **not**:

- A user manual or how-to guide (those are skills, manuals, tutorials).
- An API contract (those are tool schemas).
- A test specification (those are test files, or experience prompts under `.anatomy/prompts/` if a folder has them).
- A description of intent or design philosophy (those are commit messages and discussion notes under `discussions/`).

It **is**: a code-cited map of *what is in this folder, where it lives, what it connects to, and what state it produces or consumes.* Every structural claim it makes is grounded in a `file:line` reference into the code. If you cannot verify a claim by opening the cited file and reading, the claim does not belong in anatomy.

The shape of a typical `ANATOMY.md`:

- **What this is** — one paragraph naming the concept this folder embodies.
- **Components** — what files/functions/classes are here, with `file:line` citations and one-line purposes.
- **Connections** — what calls into this folder, what this folder calls out to, what data flows through.
- **Composition** — parent folder, subfolders (each with their own `ANATOMY.md`), how this folder fits into the larger structure.
- **State** — persistent state this folder writes (files, schema versions), ephemeral state it manages.
- **Notes** — annotations: rationale, history, gotchas. Bounded section, never the body.

Every folder at a meaningful concept boundary has one. Trivial leaves (a single dataclass module, a one-function helper) do not. The discipline is: a folder gets an `ANATOMY.md` when a competent agent could do useful reasoning about it as a unit without first reading its siblings.

## Why you read anatomy instead of greping

You are an agent. Reading 200 lines of code is one tool call. Greping a symbol gives you 50 hits — every hit is its own tool call to evaluate. For navigation questions ("what shape is this part of me, where does behavior X live, what does Y connect to"), descending three `ANATOMY.md` files is dramatically cheaper than greping. For enumeration questions ("every callsite of this function"), grep is still right.

The rule of thumb:

| Question type | Tool |
|---|---|
| **Structural** ("what is here, what connects to what, where does X live") | Descend the anatomy tree |
| **Enumeration** ("every reference to this symbol, every file matching this pattern") | grep |

You will reach for grep less often than you used to. That is the point.

## How to descend

1. Start at `src/lingtai_kernel/ANATOMY.md` (the kernel root anatomy).
2. Read its **Components** and **Composition** sections. They list the direct children of `src/lingtai_kernel/`.
3. Pick the child whose territory contains your question. Open `src/lingtai_kernel/<child>/ANATOMY.md`.
4. Repeat. At each layer the file will tell you whether to descend further or read the cited code directly.
5. When you hit code, read it. The cited `file:line` references are the contract.

If at any layer the `ANATOMY.md` is missing or empty, the convention is still being populated. You can read the code directly — and if you understand what you read, you should write the `ANATOMY.md` for that folder before moving on. Empty anatomy files are an invitation to maintain.

## Maintenance is part of reading

The anatomy stays accurate because every agent that reads it is also a maintainer. The contract:

- **If the code matches the anatomy:** read on, no action.
- **If the code disagrees with the anatomy:** decide which is right. The code is almost always right (it was last modified by someone solving a real problem; the anatomy was last modified by someone documenting). Update the anatomy to match the code before you leave the file. If you believe the code itself is wrong (a bug), report it — but still note in your report that anatomy and code disagreed, because that disagreement is itself a clue.
- **If the anatomy is missing or empty for a folder you just read:** write it, briefly. Components, connections, state. ~80 lines is the cap; less is better than more. The next agent will thank you.

You are not writing perfect documentation. You are leaving a slightly cleaner trail than you found.

## Cross-references between anatomy files

`ANATOMY.md` files cross-reference each other by **relative path from the kernel root** — e.g. an entry under `intrinsics/soul/ANATOMY.md` might say "spliced through `intrinsics/system/` (see `src/lingtai_kernel/intrinsics/system/ANATOMY.md`)." References are sparse and one-directional: a folder cites its parent and any folders it actually depends on, never enumerates downstream callers (that is a grep question).

The kernel-root `ANATOMY.md` is the only file that holds a complete child enumeration. Every other anatomy points up to its parent and sideways only when there is a structural connection worth naming.

## Relationship to other skills

- **`lingtai-anatomy`** (umbrella) — describes the LingTai *system* as a user experiences it: TUI flows, presets, init.jsonc, runtime layout under `~/.lingtai-tui/`. Lives outside the kernel. If your question is "how does my init.jsonc get there," start there.
- **Per-tool manuals** (`daemon-manual`, `mcp-manual`, `library-manual`, ...) — operational how-to for invoking specific tools. If your question is "how do I use X," start there.
- **`lingtai-kernel-anatomy` (this skill)** — self-introspection of your own kernel. If your question is "what is X actually doing inside me, where does it live in my code," start here.

The three skills are layered. Manuals tell you how to act. Umbrella anatomy tells you about the world you live in. Kernel anatomy tells you about yourself.

## Version history

- **v3.0.0** (2026-05): Reworked from topical references to per-folder `ANATOMY.md` convention. The 10 reference files (file-formats, filesystem-layout, mail-protocol, mcp-protocol, memory-system, molt-protocol, network-topology, runtime-loop, glossary, changelog) were removed; their content now lives next to the code in per-folder anatomy files. This skill is now the entrance, not the content.
- **v2.1.0** (2026-04-29): Added `mcp-protocol.md`, absorbed standalone `lingtai-changelog`. (Removed in v3.)
- **v2.0.0** (2026-04): Modular rewrite — 8 references replaced the monolithic SKILL.md. (Removed in v3.)
- **v1.2.0**: Original monolithic SKILL.md (474 lines). (Removed in v2.)
