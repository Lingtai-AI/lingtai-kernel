---
name: skills-manual
description: >
  Read before authoring, installing, or sharing a skill in
  `.library/custom/<name>/`, adding configured skill roots, or diagnosing a
  catalog entry; this teaches the Skills system, not bundled skill procedures.
version: 1.2.0
last_changed_at: "2026-09-09T00:00:00Z"
related_files:
- src/lingtai/tools/skills/manual/reference/cleanup-footprint-contract.md
- src/lingtai/tools/skills/manual/assets/skill-template.md
- src/lingtai/tools/skills/manual/scripts/validate.py
- src/lingtai/tools/skills/__init__.py
- src/lingtai/tools/skills/ANATOMY.md
- src/lingtai/tools/skills/CONTRACT.md
maintenance: |
  Keep this router aligned with the Skills capability, its installed template and
  validator, the nested-reference routing shape, and the cleanup reference.
---

# Skills Manual

Skills are reusable, portable procedures. The capability scans `.library/` plus
configured roots, builds a compact YAML catalog, and injects that catalog into the
prompt. A skill is usable only after it is installed in a scanned root **and** a
catalog rebuild (or passive refresh/molt) has picked it up. A URL, temporary clone,
or newly written file is not loaded yet.

## Sources and roots

```text
<agent>/.library/
├── intrinsic/                 # CLI-managed; rewritten on refresh; do not edit
│   ├── capabilities/<cap>/    # installed capability manuals
│   └── addons/<addon>/
└── custom/                    # agent-owned skills
```

Additional roots are `manifest.capabilities.skills.paths` in `init.json` (the
configuration ground truth). A plain rebuild rescans already configured roots; it
does not reread changed configuration. To add a root, obtain authorization, exact
edit `init.json`, then apply the full refresh envelope:

```text
system(action="refresh",
       input={"reason": "pick up new skills paths", "preset": null, "revert_preset": null},
       reasoning="apply changed skills configuration")
```

`.library_shared/` is opt-in, not a default; ordinary sharing installs into each
receiver's `custom/`. If Skills is disabled, files remain on disk but no catalog is
injected.

## Public route and first action

```text
psyche(action="skills", input={}, reasoning="load skills guidance")
```

This strict-empty signpost performs no scan, injection, create, edit, or load.
Routine schema-sufficient calls need no ritual manual reload; read this route when
the catalog, roots, or skill lifecycle is unfamiliar. To inspect a cataloged body,
use its `location` with the complete read call:

```text
file(action="read", input={"file_path": "<location>", "offset": null, "limit": null, "max_chars": null}, reasoning="read cataloged skill")
```

`context(action="rebuild", input={}, reasoning="rescan skills catalog")` rescans
`.library/` and configured roots and recomposes the Skills section. A degraded
manual result usually means the intrinsic manual was not installed correctly.

## Nested reference pattern

Use the **Nested skill/reference pattern for umbrella manuals** only when one
parent must route to substantial children. The catalog scanner treats a directory that already has a `SKILL.md` as a skill boundary, so the parent must inject children's routing metadata explicitly. Every parent carries both:

1. a `## Nested reference catalog` fenced YAML list (`name`, relative `location`,
   `description`) for each child; and
2. a human-readable `## Routing table` mapping the same needs to the same paths.

The YAML is a machine-readable routing table, not decoration. Do not leave the parent as only a prose list of links. Child locations are relative to the parent
and each nested child has its own frontmatter:

```yaml
- name: parent-topic-a
  location: reference/topic-a/SKILL.md
  description: Nested child for the topic-a route
```

Keep this catalog and the human routing table in sync. For the broader example, enter `system-manual` →
`reference/substrate-manual/SKILL.md`. Validate the parent **and each child**
separately using the installed validator’s full path and each folder as its argument.
For example, from the parent folder:
`python3 <agent>/.library/intrinsic/capabilities/skills/scripts/validate.py reference/topic-a/`.
Child names must be unique within the parent; describe the parent and read trigger.
Verify the installed/copied child tree as well as parent metadata. Independent
workflows should remain top-level skills, not hidden children.

## External skill intake (default flow)

A shared URL or temporary clone is not installed: the skill is
   only a file on disk until a reviewed copy reaches a scanned root. Copy/clone
into `<agent>/.library/custom/<skill-name>/`, retain useful upstream metadata, and
inspect `SKILL.md` plus referenced scripts/assets/references. For each install, run the bundled validator, rebuild, and read the cataloged location. Each receiving agent clones/copies it into its own `.library/custom/<name>`. Do not assume `.library_shared` is loaded by default. If a shared root is explicitly chosen, add `../.library_shared` to each participating agent's configured paths:

```text
python3 .library/intrinsic/capabilities/skills/scripts/validate.py .library/custom/<skill-name>/
context(action="rebuild", input={}, reasoning="rescan skills catalog")
```

`last_changed_at` is optional for custom/external skills. Add
`--require-last-changed-at` when validating a LingTai-maintained bundle; this is
not the default for third-party intake. Record the reviewed source URL/commit.

A quarantine copy is for inspection only; install the reviewed copy into `custom/`
before relying on it. To check for a name collision first, use the complete shell
call `shell(action="run", input={"command": "grep -rh '^name:' .library/", "timeout": null, "working_dir": null, "async": null, "reminder": null}, reasoning="check for skill-name collision")`. Check already configured extra roots too;
rename or reuse an existing skill on a collision. The example only scans `.library/`.
Installation never authorizes side effects described by the skill. For sharing, send the source URL or artifact path and this local-install /
validate / rebuild recipe; each receiver owns its copy and rollback.

To author a skill, use the complete write envelope
`file(action="write", input={"file_path": ".library/custom/<skill-name>/SKILL.md", "content": "..."}, reasoning="author skill")`, then put at least this frontmatter in the file:

```yaml
---
name: <skill-name>
description: What it does, when to use it, and when not to use it
version: 1.0.0
last_changed_at: "2026-09-09T00:00:00Z"
---
```

Use the bundled `assets/skill-template.md` as a starting point. Maintained bundles
must update `last_changed_at` with substantive edits. Validate frontmatter,
placeholders, referenced files, and script executability; warnings do not turn a
failed frontmatter or broken-reference check into a pass. The validator exits 0
only when required checks pass.

For maintained metadata-only/backfill changes, derive `last_changed_at` from
`git log -1 --format=%cI -- path/to/SKILL.md`, not the bookkeeping date. Required
frontmatter is `name` and `description`; version/author/tags remain optional.

Keep simple linear procedures flat; use a small router with self-contained
references for multi-topic work. Put brittle repeated operations in scripts and
concrete templates in assets. Create skills for reusable competence, not one-off
tasks or personality/style preferences (those belong in character/covenant).

A standalone published skill also needs a human-facing README (status, license,
contributors), separate from agent instructions. Publishing requires separate
authorization: copy authored content to a task-owned export directory before
initializing its repository; do not initialize a new repository inside the
existing agent tree. Retain reviewed upstream metadata for future sync/rollback.

Before publishing or trusting a skill, walk every routing branch, verify paths and
API claims against the code/filesystem, and repeat after corrections. This catches
stale signatures and fictional references that structural validation cannot.

## Catalog scope and settings

The catalog contains each entry's `name`, `description`, and `location`; read its
body only on demand. There is no Skills-owned settings file and no
`settings/psyche.skills.json`; Psyche's owner document does not configure Skills.
Pin a relevant body in Pad only through the Pad manual and rebuild afterward.

## Cleanup / Footprint

Skills leave intrinsic and custom libraries plus any configured extra roots. Never
blindly delete intrinsic runtime state. For a read-only footprint report, load the
[shared recipe](reference/cleanup-footprint-contract.md#shared-footprint-check-recipe)
and select `.library/intrinsic/`, `.library/custom/`, and explicitly configured
roots; inspection writes nothing. Cleanup means reviewed validation, renaming,
consolidation, archive, or a PR—not ad-hoc removal. First show a dry-run, explain
what changes, obtain explicit user consent, and record approved work in
`logs/cleanup.jsonl`; then rebuild the catalog. The recipe's audit append is a
separate explicit write.
