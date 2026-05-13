---
name: knowledge-manual
description: >
  Concise guide to the `knowledge` capability: private agent-owned memory in
  `<agent>/knowledge/<name>/KNOWLEDGE.md`, progressive disclosure through the
  prompt catalog, nested knowledge folders, and cross-references between
  entries. Read this when you need to create, organize, or load private
  knowledge, or when you need to explain how knowledge differs from portable
  skills.
version: 1.0.0
---

# The Knowledge Capability

Knowledge is an agent's private long-term memory. It is for facts, decisions, observations, local paths, mail context, and operational lessons that are useful to this agent but are not necessarily portable to every other agent.

Skills are different: a skill is a reusable procedure meant to travel across agents. Knowledge may point to skills; skills should not depend on private knowledge.


## Names you will see

The current private-memory capability is named `knowledge`, and the only tool it registers is:

```text
knowledge({"action": "info"})
```

Older documentation and UI surfaces may still say `library` or `codex`. Treat those as historical names for the private durable knowledge store unless the text is clearly talking about skills. In current agent code, `library(...)` and `codex(...)` are not registered compatibility aliases.

Do not confuse this with the `.library/` directory: `.library/` is the on-disk home for **skills** (`SKILL.md` files), not for private `knowledge` entries. The naming is legacy but intentional for compatibility.

Quick map:

| Term / path | Current meaning |
|---|---|
| `knowledge` tool / capability | Private per-agent durable memory catalog. |
| `<agent>/knowledge/<name>/KNOWLEDGE.md` | One private knowledge entry. |
| `.library/intrinsic`, `.library/custom`, `.library_shared` | Skill shelves containing `SKILL.md` files. |
| `skills` tool / capability | Catalog of reusable portable procedures. |
| `/skills` in the TUI | Browse the skill catalog for the selected agent. |
| `/knowledge` in the TUI | Browse private durable knowledge/codex entries. |
| `/library` or `/codex` in the TUI | Legacy aliases for `/knowledge`; keep only for compatibility. |
| `recipe.json#library_name` | Legacy schema field naming a recipe-bundled skill library. It appends to `skills.paths`. |

When writing new docs, prefer `knowledge` for private memory and `skills` for reusable procedures. Use `library` only when referring to a literal legacy path or schema field such as `.library/`, `.library_shared`, or `recipe.json#library_name`.

## Layout

Each entry is a folder under `<agent>/knowledge/` with a `KNOWLEDGE.md` file:

```text
<agent>/knowledge/
└── <name>/
    ├── KNOWLEDGE.md
    ├── references/
    ├── scripts/
    ├── assets/
    └── notes/
```

`KNOWLEDGE.md` starts with YAML frontmatter:

```markdown
---
name: <name>
description: One short sentence shown in the prompt catalog.
version: 1.0.0
---

# Title

Full notes live here.
```

Required fields are `name` and `description`. Supporting files are optional and can be any useful text, script, data sample, log, or asset.

## Progressive disclosure

The system prompt only receives a compact catalog: each entry's `name`, `description`, and `location`. The body of `KNOWLEDGE.md` and supporting files stay on disk until you explicitly read them.

Use:

```text
knowledge({"action": "info"})
```

to rescan the catalog and refresh the prompt section, then use `read` on the listed `location` when an entry becomes relevant.

This keeps the prompt small while still making the memory discoverable.

## Nesting

Knowledge may be nested for organization. The scanner descends through folders until it finds `KNOWLEDGE.md` files, so these are valid:

```text
knowledge/project-a/architecture/KNOWLEDGE.md
knowledge/project-a/incidents/2026-05-cache-bug/KNOWLEDGE.md
knowledge/people/reviewers/KNOWLEDGE.md
```

Keep names filesystem-safe and descriptive. Use nesting to group related entries, not to hide information.

## Cross-references

Knowledge entries may reference one another by relative path or by catalog name. Prefer links that remain valid if the whole agent directory moves:

```markdown
See also: ../architecture/KNOWLEDGE.md
See also: ../../people/reviewers/KNOWLEDGE.md
```

Knowledge may also reference skills when a reusable procedure exists:

```markdown
For the repeatable workflow, read `.library/intrinsic/capabilities/skills/SKILL.md`.
```

Direction matters: private knowledge can point outward to skills, but shared skills should not point inward to private knowledge paths, mail IDs, or local logs.

## When to create knowledge

Create or update a knowledge entry when the information is useful beyond the current turn but is not a portable procedure:

- project-specific decisions and rationale;
- collaborator preferences and review history;
- local repo paths, branch relationships, and known gotchas;
- incident notes and debugging evidence;
- conclusions from research that are specific to this agent's work.

If the content is a reusable how-to that another agent should be able to apply without your private context, write a skill instead.
