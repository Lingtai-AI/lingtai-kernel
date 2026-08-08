---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/agent.py
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/skills/ANATOMY.md
  - pyproject.toml
  - src/lingtai/intrinsic_skills/__init__.py
  - src/lingtai/intrinsic_skills/context-manual/SKILL.md
  - src/lingtai/intrinsic_skills/context-manual/assets/molt-template.md
  - src/lingtai/intrinsic_skills/context-manual/assets/session-journal-entry-template.md
  - src/lingtai/intrinsic_skills/context-manual/reference/summarize-manual/SKILL.md
  - src/lingtai/intrinsic_skills/file-manual/SKILL.md
  - src/lingtai/intrinsic_skills/lingtai-doctor/SKILL.md
  - src/lingtai/intrinsic_skills/lingtai-doctor/scripts/doctor.py
  - src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md
  - src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/reference/mcp-protocol.md
  - src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/bench_agent_session_rebuild.py
  - src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/scripts/check_anatomy_drift.py
  - src/lingtai/intrinsic_skills/lingtai-manual/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/reference/channel-model/SKILL.md
  - src/lingtai/intrinsic_skills/notification-manual/reference/dismissal-safety/SKILL.md
  - src/lingtai/intrinsic_skills/pad-manual/SKILL.md
  - src/lingtai/intrinsic_skills/psyche-manual/SKILL.md
  - src/lingtai/intrinsic_skills/read-manual/SKILL.md
  - src/lingtai/intrinsic_skills/soul-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/goal-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py
  - src/lingtai/intrinsic_skills/system-manual/reference/llm-adapters/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/procedures-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/refresh-precheck/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/sqlite-log-query/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/sqlite-log-query/scripts/event_summary.py
  - src/lingtai/intrinsic_skills/system-manual/reference/substrate-manual/SKILL.md
  - src/lingtai/intrinsic_skills/system-manual/reference/trajectory-mining/SKILL.md
  - tests/test_intrinsic_manual_actions.py
  - tests/test_lingtai_doctor.py
  - tests/test_override_intrinsic.py
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  This package is a bundle of shipped documentation, not a code layer, so it
  owns no CONTRACT.md; each bundle's behavioral promise belongs to the tool it
  documents. Adding a skill bundle, a reference sub-skill, an asset, or a
  script means adding it here in the same change, and means checking the
  pyproject package-data globs still ship it. Keep the parent link to
  src/lingtai/ANATOMY.md bidirectional and run the architecture-document
  validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# src/lingtai/intrinsic_skills/

Kernel-shipped skill bundles that are **not** tied to a specific tool package.
Every subdirectory is copied verbatim into
`.library/intrinsic/capabilities/<name>/` on each agent boot, so this package is
the delivery mechanism for documentation-only skills that have no companion
code under `tools/` (`src/lingtai/intrinsic_skills/__init__.py:1-9`).

## Components

- `__init__.py` — the package marker that states the contract above: one
  subdirectory per bundle, copied verbatim, for skills without companion code.
- `context-manual/` — the `context` family's manual, with `assets/`
  (`molt-template.md`, `session-journal-entry-template.md`) and the nested
  `reference/summarize-manual/SKILL.md` sub-skill.
- `system-manual/` — the largest bundle: the `system` family manual plus ten
  `reference/` sub-skills (`environment-variables`, `goal-manual`,
  `how-to-change-name`, `llm-adapters`, `procedures-manual`,
  `refresh-precheck`, `runtime-update-checks`, `sqlite-log-query`,
  `substrate-manual`, `trajectory-mining`). Two of them
  ship executable helpers — `how-to-change-name/scripts/change_name.py` and
  `sqlite-log-query/scripts/event_summary.py`.
- `notification-manual/` — the `notification` family manual plus the
  `channel-model` and `dismissal-safety` reference sub-skills.
- `lingtai-kernel-anatomy/` — the repository's own navigation skill: how to read
  and maintain the Anatomy/Contract graph. It carries
  `reference/mcp-protocol.md` and two scripts,
  `scripts/check_anatomy_drift.py` (the advisory citation-rot checker) and
  `scripts/bench_agent_session_rebuild.py`.
- `lingtai-doctor/` — read-only health diagnostics for agents and bots, with a
  bundled `scripts/doctor.py` for layered local checks that expose no secrets.
- Single-file bundles — `file-manual/`, `lingtai-manual/`, `pad-manual/`,
  `psyche-manual/`, `read-manual/`, and `soul-manual/`, each one `SKILL.md`
  documenting its namesake surface.

## Connections

`Agent._install_intrinsic_manuals` (`src/lingtai/agent.py:437-513`) is the only
consumer: it imports this package alongside `lingtai.tools`, wipes
`.library/intrinsic/`, then runs two installers into
`.library/intrinsic/capabilities/` — `install_from` (`src/lingtai/agent.py:462`)
for the per-tool `manual/` bundles and `install_skills_from`
(`src/lingtai/agent.py:489`) for the whole-directory bundles here. It never
touches `.library/custom/`, which is the agent's own territory.
`src/lingtai/tools/`
supplies the other half of the same install — the per-tool `manual/` bundles —
so a bundle lives here precisely when no tool package owns it.

After the copy, the `skills` capability re-scans `.library/` and renders the
catalog into the `skills` prompt section
(`src/lingtai/tools/skills/ANATOMY.md`); the bundles themselves are inert
Markdown until a tool's `manual` action or a skill read pulls one in.

## Composition

- **Parent:** [`src/lingtai/ANATOMY.md`](../ANATOMY.md).
- **Sibling:** `src/lingtai/tools/`, whose per-tool `manual/` directories are
  installed by the same code path and follow the same `SKILL.md` shape.
- **No children.** Individual bundles are documentation, not architectural
  components, so none earns its own anatomy; nested `reference/<name>/SKILL.md`
  sub-skills are the progressive-disclosure mechanism instead.

## State

The package itself is packaged read-only data. The durable state it produces is
per-agent and lives under the working directory:
`.library/intrinsic/capabilities/<name>/`, rebuilt from scratch on every boot.
Because the install wipes `.library/intrinsic/` first, an operator edit inside
that tree is deliberately not durable — the agent's own material belongs in
`.library/custom/`.

## Notes

- A bundle is reachable at runtime only if `pyproject.toml`'s package-data
  globs ship its files. Adding a deeper `reference/` or `assets/` level without
  checking those globs produces a bundle that works from a source checkout and
  silently loses files in a wheel.
- Skill frontmatter here is richer than the docs-governance minimum (`name`,
  `description`, `version`, often `last_changed_at`), because the `skills`
  catalog renders it; keep both the governance fields and the catalog fields
  when editing a `SKILL.md`.
