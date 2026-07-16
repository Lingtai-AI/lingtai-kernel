---
product: kernel
release_version: "0.16.4"
release_tag: "v0.16.4"
migration: no-op
refresh_required: false
related_files:
  - RELEASING.md
  - pyproject.toml
  - src/lingtai/kernel/migrate/ANATOMY.md
maintenance: |
  Replace this document for every kernel release. Keep the stable repository
  path migration/migration.md; Git commits and release tags preserve the older
  per-release versions. Never append a second release history here or invent a
  version that disagrees with package metadata.
---
# LingTai kernel 0.16.4 migration

## Applies when

The target kernel release is `0.16.4` / tag `v0.16.4`.

## Migration

**No-op.** This release centralizes kernel-owned nudge wording and routes update
situations through `https://lingtai.ai/skill.md`. It does not change an agent
workdir, preset, `init.json`, or another kernel-owned on-disk configuration
shape.

## Validate

- Do not rewrite agent configuration for this kernel migration.
- Confirm the file was read from this repository at the exact target tag.
- If the product, version, tag, or stable path does not match, stop rather than
  borrowing a TUI migration or another release's document.

## Refresh

This migration does not itself require refresh. The surrounding authorized
kernel update procedure may still require refresh after all applicable release
migrations validate.
