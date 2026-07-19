---
name: skills-manual
description: >
  Meta-manual for the `skills` capability: how your catalog is built from
  `.library/{intrinsic,custom}/` plus `init.json` paths, and how to author,
  validate, install, share, publish, and pin skills. Read when writing a skill in
  `.library/custom/<name>/`, installing an external skill repo, debugging a skill
  missing from the catalog, adding a skills path, or turning a manual into a
  progressive-disclosure router. Does NOT document the bundled skills themselves
  — their own SKILL.md files do.
version: 1.1.0
last_changed_at: "2026-07-19T00:00:00Z"
related_files:
- src/lingtai/tools/skills/__init__.py
- src/lingtai/tools/skills/ANATOMY.md
- src/lingtai/tools/skills/CONTRACT.md
maintenance: |
  Tracks the routed source/resources it summarizes; update when the underlying capability or its sub-references change.
---

# The Skills Capability

This is the skills capability's own manual — how the skills *system* works, not
what is inside it. The capability scans `.library/` plus any extra paths declared
in `init.json`, builds a YAML catalog, and injects it into your system prompt.

**The one rule behind everything below:** a skill exists for you only after it is
(1) installed into a scanned skill root and (2) picked up by
`system({"action": "refresh"})`. A shared URL, a temporary clone, or a file you
just wrote is not yet in the catalog. Every write/install step in this manual
ends with a refresh.

## On-disk layout

Your skill catalog lives at `<agent>/.library/`:

```
<agent>/.library/
├── intrinsic/
│   ├── capabilities/
│   │   └── <cap>/<manual files>
│   └── addons/
│       └── <addon>/<manual files>
└── custom/
```

- `intrinsic/` — **CLI-managed.** Wiped and rewritten from kernel-shipped manual
  bundles on every refresh. Do not edit; your edits will be erased.
  `capabilities/<cap>/` holds each loaded capability's manual (`skills/`,
  `email/`, `psyche/`); `addons/<addon>/` holds each loaded addon's
  (`imap/`, `telegram/`, `feishu/`).
- `custom/` — **your territory.** Authored skills live here; the CLI never
  touches this directory.

Additional roots come from `init.json` at `manifest.capabilities.skills.paths` —
typically `~/.lingtai-tui/utilities/`, plus any project-specific roots. To add
one: `edit` that list (absolute, or relative to your working dir; `~/` expansion
honored), then refresh. `init.json` is the ground truth — there is no runtime
state, so whatever is in `paths` at setup time is the exact set scanned.

`../.library_shared/` is an **opt-in** local-network sharing root, not a default.
For ordinary sharing, install into each receiving agent's `custom/` instead.

If the skills capability is not loaded, the files still exist on disk — you just
get no catalog in your prompt. Reach the manuals with `read`, `grep`, `ls`.

## The catalog, and checking its health

The `skills` section of your system prompt is a YAML list. Each skill is one
`- name: <name>` block with a `location:` (absolute path to that skill's
`SKILL.md`) and a `description:` block scalar. To read a skill's body, `read` the
file at its `location`.

`skills({"action": "info"})` refreshes/reconciles the catalog and returns a
runtime snapshot — `skills_dir`/`library_dir`, `catalog_size`, resolved paths
with exist/skill-count info, and any `problems` (invalid frontmatter, unreadable
folders) — without the manual body. Use it first when a skill you expect is
missing. `skills({"action": "manual"})` returns this SKILL.md body instead. A
`status` of `"degraded"` carries an error message naming the fix — typically a
missing manual under `intrinsic/capabilities/skills/`, meaning the initializer
did not install manuals correctly.

To pin a skill's body into your pad so it survives a molt and rides in the cached
system-prompt prefix:

```
psyche({"object": "pad", "action": "append", "files": ["<location>"]})
```

Pinning is cheap per-token over a session; repeated `read`s of the same file are
not. Pad semantics (read-only reference section, clearing with `files: []`, the
token ceiling) belong to `psyche-manual` §5 — read it there.

## External skill intake (default flow)

When a human, peer, or repository URL shares a skill, do not treat the URL or a
temporary clone as loaded. The default flow is local-first:

1. **Clone or copy into this agent's `<agent>/.library/custom/<skill-name>/`.**
   Keep repository metadata when the skill has an upstream, so future syncs can
   use `git pull --ff-only`.
2. **Validate before trusting it** — run the bundled validator (below) against
   the installed folder and inspect `SKILL.md` frontmatter plus any referenced
   `scripts/`, `assets/`, or `references/`.
3. **Refresh** — call `system({"action": "refresh"})`. Until refresh succeeds
   the skill is only a file on disk.
4. **Load it by catalog entry** — `read` the cataloged `location:` and follow any
   nested references. Record the commit or source URL in your task notes when it
   matters.

Use a temporary/quarantine clone only to pre-inspect an untrusted source; move or
re-clone a reviewed copy into `.library/custom/<skill-name>/` before relying on
it. **Installing a skill does not authorize the external side effects it
describes** — normal human authorization boundaries still apply.

**Sharing works the same way in reverse.** Send peers the source URL (or artifact
path) plus this recipe. Each receiving agent clones/copies it into its own
`.library/custom/<name>/`, validates it, then refreshes itself. That keeps
ownership, updates, and rollback local to the agent actually using the skill.
A network may instead maintain `.library_shared/<name>/`; add `../.library_shared` to each participating
agent's `manifest.capabilities.skills.paths`. Do not assume `.library_shared` is loaded by default:
it is an explicit opt-in with a shared stewardship burden, never the default
distribution path.

## Authoring a new skill

Create `<agent>/.library/custom/<skill-name>/SKILL.md` starting with YAML
frontmatter, then refresh:

```
---
name: <skill-name>
description: One-line description of what this skill does
version: 1.0.0
# Required for LingTai-maintained skills; optional for custom/external skills.
last_changed_at: "2026-06-29T08:00:00Z"
---

Full instructions in Markdown below...
```

Required: `name`, `description`. Optional: `version`, `author`, `tags`,
`last_changed_at`.

**LingTai-maintained skill metadata.** Every skill bundle maintained inside a
LingTai repository — intrinsic capability manuals, standalone intrinsic skills,
MCP manual bundles, TUI preset utility skills, and their nested reference
`SKILL.md` files — must carry `last_changed_at` as an ISO 8601 timestamp with
timezone (`"2026-06-29T08:00:00Z"`). Update it in the same commit as any
substantive edit to the skill body. For a historical backfill or metadata-only
edit, derive the value from git history
(`git log -1 --format=%cI -- path/to/SKILL.md`) so it points at the latest
meaningful content change rather than the bookkeeping commit.

`tags` is a list of lowercase, hyphenated strings aiding discoverability and
(eventually) tier filtering, along three axes — language/runtime (`python`,
`fortran`, `bash`, `node`), domain (`physics`, `mhd`, `plasma`, `ml`, `web`), and
type (`solver`, `workflow`, `reference`, `cheatsheet`). Example:
`tags: [python, physics, mhd, solver]`. Tags are best-effort metadata, not
load-bearing — the catalog still finds skills without them.

**Check for name collisions first.** Two skills sharing a `name` collide in the
catalog:

```
shell({"command": "grep -rh '^name:' .library/ ~/.lingtai-tui/utilities/ 2>/dev/null"})
```

On a hit: rename, or adapt the existing skill instead of forking a second one.

### Starting from the template

```
cp .library/intrinsic/capabilities/skills/assets/skill-template.md \
   .library/custom/<skill-name>/SKILL.md
```

The template carries placeholder slots (`[SKILL_NAME]`,
`[ONE_LINE_DESCRIPTION]`, …) and a soft heading skeleton (`When this applies` /
`Procedure` / `What to expect` / `Constraints` / `Scripts` / `Assets`). It works
for code/executable skills as-is; for reference-style skills (manuals,
cheatsheets, chronicles) delete the `Procedure` section and write prose — a note
at the top of the template says so.

### Validating before installing or publishing

```
python3 .library/intrinsic/capabilities/skills/scripts/validate.py \
   [--require-last-changed-at] .library/custom/<skill-name>/
```

It checks required frontmatter (`name`, `description`), unfilled `[PLACEHOLDER]`
slots left over from the template, broken internal references (paths under
`scripts/`, `assets/`, `references/` mentioned in `SKILL.md` that do not exist on
disk), and `chmod +x` on Python scripts under `scripts/`. Exits 1 on any FAIL, 0
on PASS (warnings allowed). Add `--require-last-changed-at` for
LingTai-maintained bundles. Run it after authoring, after installing an external
skill, and before any broader distribution.

### Self-test before publishing

The validator catches structure, not content. After writing, walk your skill as a
fresh agent:

1. **Decision-tree test** — start at SKILL.md's first decision and follow each
   branch. Does every reference file exist? Is the content reachable from the
   routing hub?
2. **Assertion test** — `grep` the actual codebase/filesystem for every claim:
   file paths, API signatures, parameter names, default values. Do NOT trust your
   memory of the code.
3. **Regression test** — fix what you found, then repeat step 2.

This catches what the validator cannot see: fictional file paths (e.g.
referencing a `helmholtz*.f90` that does not exist), API signatures from an older
code version, and default parameter values that have since changed. Treat it as
mandatory for skills documenting an external codebase — fabricated paths and
stale signatures are the most damaging failure mode.

## Structuring a skill

| Content | Shape |
|---|---|
| Under ~300 lines, or one path through it | Flat `SKILL.md` |
| Multi-topic reference, decision tree, ≥300 lines | Two-level: `SKILL.md` router + `reference/<topic>.md` files |
| Umbrella router whose children need their own frontmatter, scripts, or assets | Nested skill/reference pattern (below) |

### Two-level progressive disclosure

```
<skill-name>/
├── SKILL.md              # Routing hub: decision tree + quick start + topic table
├── README.md             # GitHub-facing description (humans, not agents)
└── reference/
    ├── topic-a.md        # Self-contained deep-dive, loaded on demand
    └── topic-b.md
```

`SKILL.md` is a **decision tree** (~150–180 lines): the agent picks a path, then
does a single `read` on the right reference file. Each reference covers ONE topic
(100–300 lines). The agent loads ~150 lines plus one reference instead of a
1000-line monolith. Do not use this for simple skills — single-API wrappers,
linear checklists, prose-only references.

Reference implementations: `huangzesen/laps-skill`,
`huangzesen/helmholtz-skill`.

### Nested skill/reference pattern for umbrella manuals

Use this when a parent skill is itself a **router** and some children need to
behave like mini-skills: their own frontmatter, trigger summary, future
`scripts/` or `assets/`, and a stable addressable folder. This is second-layer
progressive disclosure inside one top-level catalog entry, not a way to hide
unrelated reusable skills.

```
<parent-skill>/
├── SKILL.md                         # Top-level cataloged router
└── reference/
    ├── topic-a/
    │   └── SKILL.md                 # Nested reference, loaded only via parent
    └── topic-b/
        └── SKILL.md
```

**Key rule: a nested `reference/<topic>/SKILL.md` is not automatically promoted
to the global catalog.** The catalog scanner treats a directory that already has
a `SKILL.md` as a skill boundary and does not descend into it for additional
entries. The parent must therefore inject the children's routing metadata explicitly
and keep the heavy procedural content in the children.

Every such parent must contain **both**:

1. a `## Nested reference catalog` section with a fenced YAML list, one item per
   child (`name`, `location`, `description`); and
2. a human-readable `## Routing table` (or equivalent decision table) mapping
   needs to the same child `location`s.

```yaml
- name: parent-topic-a
  location: reference/topic-a/SKILL.md
  description: |
    Nested <parent-skill> reference for ... Read this after loading
    <parent-skill> when ...
- name: parent-topic-b
  location: reference/topic-b/SKILL.md
  description: |
    Nested <parent-skill> reference for ...
```

The YAML catalog is not decoration — it is the machine-readable routing table for
the second layer. Keep it in sync with child frontmatter and with the human
routing table. Do not leave the parent as only a prose list of links.

Use nested references when all of these hold: callers should enter through one
umbrella skill first; the child is substantial enough to deserve frontmatter and
possibly its own `scripts/` or `assets/` later; exposing it standalone would clutter
the catalog or bypass important routing context; and the parent can clearly say
when to read each child. Do **not** use them for independent workflows agents
should find directly from the top-level catalog — those belong in
`.library/custom/<name>/`, an opt-in shared root such as `.library_shared/<name>/`,
or an intrinsic skill root.

Nested child conventions:

- Each child `reference/<topic>/SKILL.md` carries normal skill frontmatter:
  `name` and `description` required, `version`/`tags` optional, plus
  `last_changed_at` when the parent is maintained inside a LingTai repository.
  `name` should be unique within the parent and descriptive
  (`system-manual-sqlite-log-query`, `daily-reflection-data-collection`) even
  though it is not globally cataloged.
- The child `description` and the parent catalog `description` should open by
  saying it is nested, name the parent, and give the trigger condition:
  `Nested system-manual reference for ...`.
- `location` in the parent YAML catalog is relative to the parent folder, so it
  survives copy/install moves and resolves next to the parent `SKILL.md`.
- The parent stays the routing hub. Resident prompts and sibling skills route to
  the parent first, then the nested reference; do not bypass the parent unless
  the caller already has parent context loaded.
- Tests should verify both levels: the parent body contains the YAML catalog,
  every child `name`/`location`, and the human routing table; the
  installed/copied tree contains each child `SKILL.md` with valid frontmatter and
  key content.
- The validator handles one skill folder at a time — validate the parent, then
  each nested child folder directly, e.g.
  `python3 .../validate.py reference/topic-a/` from the parent skill folder.

Reference implementations: `system-manual` is a top-level router with nested
`reference/substrate-manual/SKILL.md`, `reference/procedures-manual/SKILL.md`,
and `reference/sqlite-log-query/SKILL.md`. Utility routers such as
`swiss-knife`, `web-browsing`, and `daily-reflection` use the same two-part
shape: YAML child metadata first, human routing table second.

### Cleanup / Footprint contract for tool manuals

Every tool/capability manual that owns persistent state must include a
`Cleanup / Footprint` section. It is a contract, not a janitor: it teaches agents
what the tool leaves behind and how to audit it safely. The required fields, the
consent rule, the self-audit rule, and the standard `logs/cleanup.jsonl` record
snippet live in [`reference/cleanup-footprint-contract.md`](reference/cleanup-footprint-contract.md)
— read it before writing or reviewing such a section.

## When to create a skill

**Do** when the task is repeatable with consistent steps; the procedure needs
domain knowledge not reliably available without notes; the workflow involves
multi-step orchestration or error handling worth codifying; or you want to share
expertise with other agents in the network.

**Do NOT** when it is a one-off with no reuse value; the task is just "call this
one API endpoint" (pick it up at the call site); or the instructions are
personality or style preferences — those live in the covenant or your lingtai
character, not here.

## Writing a good skill

1. **Trigger-optimized description.** The `description` is the only thing visible
   in the catalog without loading the file, so it must answer: what does this
   skill do, what domain is it for, and when should the agent reach for it vs
   skip? Aim for 2–4 sentences, and spell out what it does NOT cover — that is
   what stops an agent loading the file on a superficial match.

   - Bad: `description: "Helmholtz solver"` — what about it? when would I use it?
   - Good: `description: "Python implementation of the Helmholtz algorithm — an iterative alternating-projection method for constructing divergence-free, constant-magnitude 3D vector fields. Used to generate SPAW initial conditions for MHD simulations."`
2. **Numbered steps in imperative form.** "Extract the text...", not "You should
   extract...".
3. **Concrete templates in `assets/`** rather than prose describing the desired
   output format.
4. **Deterministic scripts in `scripts/`** for fragile or repetitive operations —
   a script that always produces the same result beats prose the LLM must
   re-derive every time.
5. **Keep `SKILL.md` focused.** Target under 500 lines; offload dense content to
   references and heavy logic to scripts.
6. **Structure subdirectories conventionally.** `scripts/` for executables,
   `references/` for supplementary context (schemas, cheatsheets, worked
   examples), `assets/` for templates and static files.

## SKILL.md vs README.md

Skills published as standalone repos need both — they serve different audiences.

| File | Audience | Loaded by |
|---|---|---|
| `SKILL.md` | LingTai agents | `skills` capability (system prompt) |
| `README.md` | Humans browsing GitHub | Not loaded by agents |

They are not redundant: `README.md` carries project status, license, contributor
notes, and screenshots that agents do not need, while `SKILL.md` carries
frontmatter fields (`tags`, `version`, machine-readable description) humans do
not parse. If you only ship through an opt-in shared root such as
`.library_shared/` and never publish to GitHub, you can omit `README.md`.

## Publishing to GitHub

To share a skill outside the network — with humans, external collaborators, or as
a standalone resource:

1. Author it in `<agent>/.library/custom/<name>/` as usual.
2. Copy out: `cp -r .library/custom/<name> /tmp/<name>`.
3. `cd /tmp/<name> && git init && git add . && git commit -m "Initial release"`.
4. Add `README.md` (human-facing — see above).
5. `gh repo create <owner>/<name>-skill --public --source=. --push`.

Do NOT `git init` inside `.library/custom/` directly — it is a subtree of your
agent working directory and you would entangle two repos. Always copy out first.
Once published, other agents install it with `git clone` into their
`.library/custom/` and refresh.

## Cleanup / Footprint

Skills live under `.library/intrinsic/`, `.library/custom/`, opt-in shared skill
paths when configured, and any extra paths configured in `init.json`. Intrinsic
skills are runtime-owned; do not delete them. Custom skills are portable
procedure memory: cleanup should usually mean validation, renaming,
consolidation, or git removal through a reviewed PR — not ad-hoc `rm`.

Footprint check (read-only, records the audit):

```bash
python3 - <<'PY'
import json, time
from pathlib import Path
agent = Path.cwd()
roots = [p for p in [agent / ".library" / "custom", agent / ".library" / "intrinsic", agent.parent / ".library_shared"] if p.exists()]
def size(p): return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
rows = [(p, size(p)) for p in roots]
total = sum(s for _, s in rows)
print(f"skill roots: {len(rows)}; bytes: {total}")
for p, s in rows: print(f"{s:>12}  {p}")
log = agent / "logs" / "cleanup.jsonl"; log.parent.mkdir(parents=True, exist_ok=True)
log.open("a", encoding="utf-8").write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": "skills", "dry_run": True, "candidates": len(rows), "bytes": total, "human_approved": False, "summary": "skills footprint audit"}) + "\n")
PY
```

Recommended cadence: after authoring/publishing skills, before recipe export, and
monthly for shared libraries. Destructive cleanup requires a dry-run report,
explicit user consent, and a git commit/PR when the skill root is tracked.
