---
related_files:
  - ANATOMY.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/system_prompt.py
  - src/lingtai/agent.py
  - src/lingtai/prompts/principle/principle.yaml
  - src/lingtai/prompts/principle/principle.md
  - src/lingtai/prompts/tools/tools.yaml
  - src/lingtai/prompts/meta_guidance/catalog/INDEX.md
  - src/lingtai/kernel/base_agent/tools.py
  - src/lingtai/kernel/tool_glossary.py
  - src/lingtai/tools/psyche/prompt.py
  - tests/test_prompt_catalog.py
  - ENVIRONMENT_VARIABLES.md
  - src/lingtai/prompts/brief/brief.yaml
  - src/lingtai/prompts/character/character.yaml
  - src/lingtai/prompts/comment/comment.yaml
  - src/lingtai/prompts/covenant/covenant.yaml
  - src/lingtai/prompts/identity/identity.yaml
  - src/lingtai/prompts/knowledge/knowledge.yaml
  - src/lingtai/prompts/mcp/mcp.yaml
  - src/lingtai/prompts/meta_guidance/catalog/notification_handling.md
  - src/lingtai/prompts/meta_guidance/catalog/review_delegation_instruction_check.md
  - src/lingtai/prompts/meta_guidance/catalog/summarize_best_practice.md
  - src/lingtai/prompts/meta_guidance/catalog/summarize_reconstruction_threshold.md
  - src/lingtai/prompts/meta_guidance/catalog/token_efficiency.md
  - src/lingtai/prompts/meta_guidance/meta_guidance.yaml
  - src/lingtai/prompts/pad/pad.yaml
  - src/lingtai/prompts/procedures/procedures.md
  - src/lingtai/prompts/procedures/procedures.yaml
  - src/lingtai/prompts/rules/rules.yaml
  - src/lingtai/prompts/skills/skills.yaml
  - src/lingtai/prompts/substrate/substrate.md
  - src/lingtai/prompts/substrate/substrate.yaml
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# lingtai/prompts

Packaged prompt-source root: the static system-prompt section bodies, the
per-section semantic **definitions**, and the runtime-guidance catalog (under
`meta_guidance/catalog/`) that generates the `meta_guidance` body. Psyche's
three-entry prompt-plan registry owns the static body composition input; the
kernel still owns rendering mechanics. This is the local navigation anchor for
a coding agent editing prompt sources — descend here instead of entering
through the large kernel-root anatomy.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## What this is

Two concerns live here and must stay separate:

- **Definition** — what a system-prompt section *name* means, why it exists, what
  scope it owns, and how its content may be injected. This lives in
  `<section>/<section>.yaml` (`kind: prompt-section-definition`). Definitions are
  for coding agents editing the kernel; they are never rendered into the LLM
  prompt.
- **Injection / body** — the actual content rendered into the prompt. For a few
  static sections this is a packaged `<section>/<section>.md` body; for most
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
| `<section>/` | One directory per prompt section. The complete set is `brief`, `character`, `comment`, `covenant`, `identity`, `knowledge`, `mcp`, `meta_guidance`, `pad`, `principle`, `procedures`, `rules`, `skills`, `substrate`, and `tools` — every one holds `<section>.yaml`, and the three body-backed ones also hold `<section>.md`. `related_files` enumerates each packaged payload individually, so no prompt source is reachable only by directory convention. |
| `<section>/<section>.yaml` | `prompt-section-definition` YAML: `name_definition`, `purpose`, `scope`, `injection_contract`, `related_files`, `maintenance`. Present for every section. |
| `principle/principle.md`, `substrate/substrate.md`, `procedures/procedures.md` | The three Psyche-plan-owned, packaged section bodies (skill-style frontmatter + Markdown body; frontmatter stripped on render). |
| `meta_guidance/catalog/` | Runtime-guidance Markdown catalog: `INDEX.md` (manifest frontmatter) + one `<id>.md` per section, nested under the `meta_guidance` section it generates. Assembled into the `meta_guidance` body; order is code-owned in `GUIDANCE_SECTION_ORDER`. |
| `tools/tools.yaml` | Semantic contract for the generated `tools` section. The section is opt-in and default off (`LINGTAI_TOOL_PROSE_SECTION_ENABLED`): when opted in the resident tool inventory renders each canonical-English description/schema plus the selected package-owned glossary body and provider tool definitions carry the fixed generic wire pointer; by default the section is omitted and that prose rides on the provider tool definition instead. Nested parameters are unchanged either way. |

## Render ownership and definition vs injection

Rendered system-prompt order (owned by `src/lingtai/kernel/prompt.py`, mapped in
the kernel-root anatomy):
`principle → covenant → tools → substrate → procedures → meta_guidance →
comment → rules → brief → mcp → skills → knowledge → identity → character → pad`.

Each section has a `<section>/<section>.yaml` definition. Bodies split three ways:

- **Body-backed static sections** — `principle`, `substrate`, `procedures`. Packaged
  `<section>/<section>.md`; the Psyche prompt-plan registry loads them through
  `tools/psyche/prompt.py`, and the Agent applies the candidate via its existing
  reconstruction seam. They are not operator-overridable; the Agent mirrors
  them to `system/<section>.md` while the kernel owns the raw first slot,
  protection, cache batches, and rendering.
- **Generated sections** — `meta_guidance` (from `meta_guidance/catalog/`), `tools`
  (tool registry), `mcp` (MCP state), `skills`/`knowledge` (registries), `identity`
  (runtime facts). No packaged body; content is built each turn.
- **Injected sections** — `covenant` and `comment` are supplied by Psyche's
  closed `settings/psyche.json` owner document. Covenant keeps
  `system/covenant.md` as a durable mirror/fallback; comment has no mirror or
  fallback. `rules`, `brief`, `character`, and `pad` come from their existing
  persistent/configured sources. None has a packaged prompt body.

For the generated `tools` section, `src/lingtai/kernel/base_agent/tools.py`
collects canonical-English descriptions and parameter schemas, then appends the
selected `glossary-{en,zh,wen}.md` body through
`src/lingtai/kernel/tool_glossary.py`; English glossary bodies are deliberately
empty. That whole section is opt-in and default off behind
`LINGTAI_TOOL_PROSE_SECTION_ENABLED` (`src/lingtai/kernel/config.py`) because its
prose duplicates the tool-calling schema description the provider payload already
carries; with it off, `wire_tool_description`
(`src/lingtai/kernel/llm/base.py`) puts that prose on the wire instead, so no
tool loses guidance and no schema changes. The daemon is a sibling system-prompt variant, mapped through
`src/lingtai/tools/daemon/ANATOMY.md` and implemented by
`src/lingtai/tools/daemon/system_prompt.py`: it does not render this resident
section stack or duplicate full tool descriptions. Instead it provides a short
manual/tool/summary/compact/finish operating contract, compact host-tool names,
oneshot context, and the task under a 20,000-character final budget. Both prompt
paths remain separate from provider tool serialization, whose top-level
description is always the fixed generic wire sentence and whose nested parameter
schema remains canonical English.

The `injection_contract` block in each YAML is the authority for which of these a
section is: `defined_by`, `injected_by`, `content_source`, optional
`resident_source`/`disclosure_source`, optional `mirror_path`/`derived_mirror`,
and `override_policy`.

## Composition

- **Parent:** `src/lingtai/ANATOMY.md` (the `lingtai` wrapper package).
- **Loader:** `src/lingtai/tools/psyche/prompt.py` composes the three static bodies
  into an immutable candidate; `src/lingtai/agent.py` applies that candidate and
  assembles the catalog-derived `system/guidance.json` (catalog now at
  `meta_guidance/catalog/`).
- **Render order + catalog loader:** `src/lingtai/kernel/prompt.py` (order) and
  `src/lingtai/kernel/prompt_catalog.py` (`load_guidance_catalog`), mapped in
  `src/lingtai/kernel/ANATOMY.md`.

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
  file: covenant's content comes from Psyche's owner document plus its durable
  `system/covenant.md` mirror fallback, while meta_guidance's body is generated
  from `meta_guidance/catalog/`. This is intentional; do not add
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
