# 2026-05-12 — Codex/Library rename migration note

LingTai's user-facing knowledge and skill-catalog names were aligned in one pass:

| Before | After | Meaning |
|---|---|---|
| `codex` capability / `codex(...)` tool | `library` capability / `library(...)` tool | Durable long-term knowledge across molts: entries with title, summary, content, and supplementary material. |
| `library` capability / `library({"action":"info"})` tool | `skills` capability / `skills({"action":"info"})` tool | Per-agent skill catalog, `<available_skills>` prompt section, and skill authoring/publishing manual. |

Compatibility notes:

- Existing `codex/codex.json` stores remain in place. This change is a naming migration, not a storage-v2 migration.
- `codex(...)` remains registered as a deprecated alias for the new knowledge `library(...)` tool during the migration window.
- `lingtai.core.codex` remains as an import wrapper around `lingtai.core.library`; new code should import `lingtai.core.library`.
- `manifest.capabilities.skills.paths` is the canonical extra skill path field. Old `manifest.capabilities.library.paths` is still accepted and normalized to `skills.paths` when it clearly refers to the old skill-catalog configuration.
- Skill files remain under `.library/` and shared skills remain conventionally under `.library_shared/` for storage compatibility. Those directory names are legacy storage names, not the capability name.
