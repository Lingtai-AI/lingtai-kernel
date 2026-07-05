---
related_files:
  - ANATOMY.md
  - src/lingtai/ANATOMY.md
  - src/lingtai_kernel/ANATOMY.md
  - src/lingtai/agent.py
  - src/lingtai/prompts/principle/principle.yaml
  - src/lingtai/prompts/principle/principle.md
  - src/lingtai/prompts/meta_guidance/catalog/INDEX.md
  - tests/test_prompt_catalog.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# lingtai/prompts

Packaged prompt-source root: the kernel-owned system-prompt section bodies, the
per-section semantic **definitions**, and the runtime-guidance catalog (under
`meta_guidance/catalog/`) that generates the `meta_guidance` body. This is the
local navigation anchor for a coding agent editing prompt sources — descend here
instead of entering through the large kernel-root anatomy.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## What this is

Two concerns live here and must stay separate:

- **Definition** — what a system-prompt section *name* means, why it exists, what
  scope it owns, and how its content may be injected. This lives in
  `<section>/<section>.yaml` (`kind: prompt-section-definition`). Definitions are
  for coding agents editing the kernel; they are never rendered into the LLM
  prompt.
- **Injection / body** — the actual content rendered into the prompt. For a few
  kernel-owned sections this is a packaged `<section>/<section>.md` body; for most
  sections the content is generated (from the `meta_guidance/catalog/` guidance
  catalog, tool registry, MCP state, skills/knowledge index) or injected by
  init/recipe/operator, and there is no packaged body.

Each section is a first-class directory directly under `prompts/`: the section
directory *is* the section name and holds that section's `<section>.yaml`
definition (and, for body-backed sections, its `<section>.md` body). This leaves
room for future per-section variants or attachments — the `meta_guidance` section,
for example, nests its generated body's source under `meta_guidance/catalog/`.

The `<section>/<section>.yaml` `related_files` graph is a **definition / progressive-disclosure**
crawl graph: peer section YAMLs for boundary-overlap risk, this anatomy, the
canonical implementation files that own a section's build rules when those rules
live in code (e.g. `meta_guidance.yaml` links `prompt_catalog.py` and
`meta_block.py`), and the canonical manual/reference doc when one owns the
section's expanded semantics (e.g. `substrate.yaml` → the `substrate-manual`
reference, `procedures.yaml` → the `procedures-manual` reference). It is distinct from the `*.md` frontmatter `related_files`
graph, which is the **prompt-source body** crawl graph (principle ↔ body/catalog
sources, catalog INDEX ↔ catalog sections).

## Components

| Path | Role |
|---|---|
| `<section>/` | One directory per prompt section (e.g. `principle/`, `covenant/`, `pad/`). Holds `<section>.yaml` and, for body-backed sections, `<section>.md`. |
| `<section>/<section>.yaml` | `prompt-section-definition` YAML: `name_definition`, `purpose`, `scope`, `injection_contract`, `related_files`, `maintenance`. Present for every section. |
| `principle/principle.md`, `substrate/substrate.md`, `procedures/procedures.md` | The three kernel-owned, packaged section bodies (skill-style frontmatter + Markdown body; frontmatter stripped on render). |
| `meta_guidance/catalog/` | Runtime-guidance Markdown catalog: `INDEX.md` (manifest frontmatter) + one `<id>.md` per section, nested under the `meta_guidance` section it generates. Assembled into the `meta_guidance` body; order is code-owned in `GUIDANCE_SECTION_ORDER`. |

## Render ownership and definition vs injection

Rendered system-prompt order (owned by `src/lingtai_kernel/prompt.py`, mapped in
the kernel-root anatomy):
`principle → covenant → tools → substrate → procedures → meta_guidance →
comment → rules → brief → mcp → skills → knowledge → identity → character → pad`.

Each section has a `<section>/<section>.yaml` definition. Bodies split three ways:

- **Body-backed kernel sections** — `principle`, `substrate`, `procedures`. Packaged
  `<section>/<section>.md`; loaded by `agent.py` via
  `files("lingtai.prompts").joinpath("<section>/<section>.md")`; kernel-owned, not
  operator-overridable; mirrored to `system/<section>.md`.
- **Generated sections** — `meta_guidance` (from `meta_guidance/catalog/`), `tools`
  (tool registry), `mcp` (MCP state), `skills`/`knowledge` (registries), `identity`
  (runtime facts). No packaged body; content is built each turn.
- **Injected sections** — `covenant`, `comment`, `rules`, `brief`, `character`,
  `pad`. No packaged body; content supplied by init/recipe/operator or the
  persistent agent store and mirrored to `system/<section>.md`.

The `injection_contract` block in each YAML is the authority for which of these a
section is: `defined_by`, `injected_by`, `content_source`, optional
`mirror_path`/`derived_mirror`, and `override_policy`.

## Composition

- **Parent:** `src/lingtai/ANATOMY.md` (the `lingtai` wrapper package).
- **Loader:** `src/lingtai/agent.py` reads the three packaged bodies and assembles
  the catalog-derived `system/guidance.json` (catalog now at `meta_guidance/catalog/`).
- **Render order + catalog loader:** `src/lingtai_kernel/prompt.py` (order) and
  `src/lingtai_kernel/prompt_catalog.py` (`load_guidance_catalog`), mapped in
  `src/lingtai_kernel/ANATOMY.md`.

## State

- Packaged resources: `<section>/*.md`, `<section>/*.yaml`, `meta_guidance/catalog/*.md`
  (declared in `pyproject.toml` `[tool.setuptools.package-data]` as `prompts/*/*.md`,
  `prompts/*/*.yaml`, and `prompts/meta_guidance/catalog/*.md` — the catalog glob is
  separate because it nests one level deeper than `prompts/*/*.md`; all carried into
  sdists by `MANIFEST.in`'s recursive prompt includes).
- Disk mirrors written per-workdir on boot/refresh: `system/{principle,substrate,
  procedures,covenant,rules,pad,lingtai}.md` and the derived `system/guidance.json`.

## Notes

- `covenant` and `meta_guidance` are section definitions with no one-to-one body
  file: covenant's content is external (operator/recipe/init), meta_guidance's body
  is generated from `meta_guidance/catalog/`. This is intentional; do not add
  `covenant/covenant.md` or `meta_guidance/meta_guidance.md`.
- YAML-only sections (`comment`, `tools`, `rules`, `brief`, `mcp`, `skills`,
  `knowledge`, `identity`, `character`, `pad`) exist so coding agents have a
  crawlable semantic contract for generated/injected sections even though the
  kernel ships no body for them.
- `related_files` in a `<section>/<section>.yaml` is a compact progressive-disclosure
  crawl graph: peer section YAMLs (boundary-overlap risk), this anatomy, and the
  canonical implementation/navigation files that own a section's concrete build
  rules when those rules live in code (e.g. `meta_guidance.yaml` → `prompt_catalog.py`,
  `meta_block.py`), and the canonical manual/reference doc when one owns the section's
  expanded semantics (e.g. `substrate.yaml` → the `substrate-manual` reference,
  `procedures.yaml` → the `procedures-manual` reference). It never lists a concrete
  prompt `.md` body (the body relation is `injection_contract.content_source`) and never
  lists tests merely because they validate behavior. The section YAML stays contract-level; exact mechanics are
  reached by crawling `related_files`, not inlined. The core five (principle/covenant/
  substrate/procedures/meta_guidance) are reciprocally linked among their peer YAMLs;
  peripheral sections link to the hub sections (procedures/substrate) one-directionally.
