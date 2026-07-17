---
product: kernel
release_version: "0.17.0"
release_tag: "v0.17.0"
migration: no-op
refresh_required: true
related_files:
  - RELEASING.md
  - pyproject.toml
  - src/lingtai/kernel/nudge/kernel_version.py
  - src/lingtai/intrinsic_skills/system-manual/reference/runtime-update-checks/SKILL.md
maintenance: |
  Replace this document for every kernel release. Keep the stable repository
  path migration/migration.md; Git commits and release tags preserve the older
  per-release versions. Never append a second release history here or invent a
  version that disagrees with package metadata.
---
# LingTai kernel 0.17.0 migration

## Applies when

The target kernel release is `0.17.0` / tag `v0.17.0` and that tag lies in the
open update interval `(current, target]`.

## Migration

**No configuration rewrite.** This release replaces package-index latest-version
discovery with official GitHub/Gitee release-manifest comparison and routes
normal install/update work through `https://lingtai.ai/install.sh`. It does not
change an agent workdir, preset, `init.json`, or another kernel-owned on-disk
configuration shape.

Before mutation, confirm that the installer selected the intended
`LINGTAI_RUNTIME_PYTHON`. If more than one LingTai runtime is present, rerun with
the explicit interpreter shown by the installer rather than guessing. The update
must use a prebuilt wheel named and hashed by the exact
`lingtai.kernel.release/v1` manifest. Do not build or install the sdist on the
user machine, and do not use PyPI metadata to choose the release version.

## Validate

- Do not rewrite agent configuration for this kernel migration.
- Confirm this file was read from the kernel repository at exact tag `v0.17.0`.
- Verify `lingtai.__version__`, `lingtai.__file__`, and
  `lingtai.kernel.__file__` from the selected runtime interpreter after install.
- If the product, version, tag, stable path, mirror content, or artifact hash does
  not match, stop rather than borrowing a TUI migration or another release's
  document.

## Refresh

The verified wheel changes bytes on disk but a running agent still has the old
code loaded. After active work is checkpointed and refresh is authorized, call
`system(action='refresh')` and verify the new process uses the selected
interpreter and reports `0.17.0`.
