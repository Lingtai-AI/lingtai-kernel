# library/paths-resolution

## What

The library capability discovers skills by scanning multiple directory paths for
`SKILL.md` files with YAML frontmatter, then builds an XML catalog injected into
the system prompt. It is pure presentation — it reads from disk but never writes
to `.library/`.

## Contract

### Path sources (scanned in order)

| Source | Path | Written by |
|--------|------|------------|
| Intrinsic | `.library/intrinsic/` | Agent initializer (`_install_intrinsic_manuals`) |
| Custom | `.library/custom/` | Agent authors (never touched by kernel) |
| Tier-1 paths | `manifest.capabilities.library.paths[]` from `init.json` | User-configured; may be absolute, relative to working dir, or `~/`-prefixed |

### Path resolution (`_resolve_path`)

1. `~/foo` → expanduser → `/Users/<user>/foo`
2. `/absolute/path` → used as-is
3. `relative/path` → `(working_dir / relative).resolve(strict=False)`

### SKILL.md parsing (`_parse_frontmatter`)

- Regex: `\A---\s*\n(.*?\n)---\s*\n` (DOTALL)
- YAML loaded via `yaml.safe_load`; coerced to `str→str` dict
- Multi-line scalars (`>`, `|`) collapsed to single-line via `" ".join(str(v).split())`
- Required fields: `name` (non-empty), `description` (non-empty)
- Optional: `version`

### Recursive scanning (`_scan_recursive`)

- Skips directories starting with `.`
- If a directory contains `SKILL.md` → parse it; do NOT recurse into children
- If no `SKILL.md` and has loose files → mark as "corrupted"
- If no `SKILL.md` and only subdirectories → recurse with prefix

### Catalog output

- XML format: `<available_skills>` with `<skill>` children containing
  `<name>`, `<description>`, `<location>` (absolute path to SKILL.md)
- Injected into system prompt section `library` (protected)
- Empty catalog → section deleted

### Health check

- `library(action="info")` re-runs full reconciliation
- Returns: `status`, `library_manual` (body of library SKILL.md), `catalog_size`,
  `paths` (per-path report), `problems` (parse/scan errors)
- Status is `"degraded"` if `.library/intrinsic/capabilities/library/SKILL.md` is missing

## Source

- Frontmatter parser: `core/library/__init__.py:50` — `_FRONTMATTER_RE`
- Path resolver: `core/library/__init__.py:72` — `_resolve_path()`
- Skill parser: `core/library/__init__.py:89` — `_parse_skill_file()`
- Recursive scanner: `core/library/__init__.py:111` — `_scan_recursive()`
- XML builder: `core/library/__init__.py:182` — `_build_catalog_xml()`
- Reconciliation: `core/library/__init__.py:205` — `_reconcile()`
- Setup: `core/library/__init__.py:310` — `setup()` (scans + registers `info`)
- Tool dispatch: `core/library/__init__.py:330` — `handle_library()`

## Related

- **library(action='refresh')** — lighter alternative to `system(refresh)` for
  re-scanning `.library/` without restarting the agent.
- **library-manual skill** — the full authoring/publishing workflow for new skills.
- **init.json** — `manifest.capabilities.library.paths` configures Tier-1 paths.
