# core/library

Library capability — per-agent skill catalog. Pure presentation: scans
whatever is on disk and builds an XML catalog injected into the system prompt.
Never writes to `.library/`. File installation is the initializer's job.

Every agent has its own `<agent>/.library/`:

- `intrinsic/capabilities/<cap>/` and `intrinsic/addons/<addon>/` — manual
  bundles installed by the Agent initializer (wipe-and-rewrite on every
  `_setup_from_init`).
- `custom/` — agent-authored skills. Never touched by any kernel code.

Additional scan paths come from `init.json` `manifest.capabilities.library.paths`.

## Components

- `library/__init__.py` — the entire capability in a single file. `get_description` (`library/__init__.py:292-293`), `get_schema` (`library/__init__.py:296-307`), `setup` (`library/__init__.py:310-344`). Key functions: `_parse_frontmatter` (`library/__init__.py:53-65`), `_resolve_path` (`library/__init__.py:72-82`), `_parse_skill_file` (`library/__init__.py:89-108`), `_scan_recursive` (`library/__init__.py:111-158`), `_scan` (`library/__init__.py:161-165`), `_build_catalog_xml` (`library/__init__.py:182-198`), `_reconcile` (`library/__init__.py:205-285`).
- `library/manual/` — skill documentation (`SKILL.md`), assets (`skill-template.md`), and scripts (`validate.py`).

## Public API

The `library` tool exposes one action:

| Action | Description |
|--------|-------------|
| `info` | Return the library manual body plus a runtime health snapshot (catalog size, paths report, problems) |

## Internal Module Layout

```
library/__init__.py
  ├── Frontmatter parser
  │   └── _parse_frontmatter()      — extracts YAML frontmatter from SKILL.md files
  │
  ├── Path resolution
  │   └── _resolve_path()           — tilde expansion, absolute passthrough, relative → working_dir
  │
  ├── Skill scanner
  │   ├── _parse_skill_file()       — reads SKILL.md, extracts name + description from frontmatter
  │   ├── _scan_recursive()         — recursively scans directories for SKILL.md files
  │   └── _scan()                   — top-level scan returning (valid, problems)
  │
  ├── XML catalog builder
  │   ├── _escape_xml()             — XML entity escaping
  │   └── _build_catalog_xml()      — renders skills as <available_skills> XML
  │
  ├── Reconciliation
  │   └── _reconcile()              — scans .library/ + Tier-1 paths, injects catalog, reports status
  │
  └── Tool surface
      ├── get_description/schema()  — module-level
      └── setup()                   — registers library tool, runs initial _reconcile
```

## Key Invariants

- **Pure presentation:** The capability never writes to `.library/`. It only reads and renders. File installation is the Agent initializer's job.
- **Skill discovery:** A skill is a directory containing a `SKILL.md` with YAML frontmatter requiring `name` and `description` fields. Directories without `SKILL.md` are recursed into; directories with loose files but no `SKILL.md` are flagged as "corrupted".
- **Dotted directories skipped:** Directories starting with `.` are ignored during scanning.
- **Health signal:** The library capability's own manual at `.library/intrinsic/capabilities/library/SKILL.md` must be present. If missing, status is `degraded`.
- **Path resolution:** Supports absolute paths, tilde-prefixed paths (`~/foo`), and relative paths (resolved against agent working dir).
- **System prompt injection:** The XML catalog is injected into the `library` section of the system prompt with `protected=True`.

## Dependencies

- `yaml` (PyYAML) — `yaml.safe_load` for frontmatter parsing
- `lingtai.i18n` — `t()` for localized strings
- `lingtai_kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `daemon/`, `avatar/`, `mcp/`, `codex/`, `bash/`.
- **Manual:** `library/manual/SKILL.md` — skill authoring guide and contract.
- **Kernel hooks:** `setup()` is called during capability initialization. The daemon capability blacklists `library` to prevent emanations from using it.
