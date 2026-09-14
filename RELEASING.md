---
related_files:
  - .github/workflows/wheels.yml
  - scripts/generate_release_manifest.py
  - scripts/publish_release_assets.py
  - tests/test_release_manifest.py
  - tests/test_tools_package_data.py
maintenance: |
  Keep this release runbook synchronized with the CI wheel/sdist workflow, manifest tooling, universal-wheel verification, and publication gates.
---
# Releasing the `lingtai` Python kernel

## Build + verify

`.github/workflows/wheels.yml` runs on `workflow_dispatch` or when a GitHub
Release is published. The kernel is a pure-Python distribution, so two
independent jobs build the artifacts:

- **`build-wheels`** — `uv build --wheel` on `ubuntu-latest` produces exactly
  one universal wheel (`lingtai-<version>-py3-none-any.whl`). The job then
  verifies it before upload: the filename must carry the `py3-none-any` tag,
  the archive must place `lingtai/` at its root with no `*.data/`
  install-scheme entries and no bundled binary under `lingtai/bin/`, and a
  dependency-free `pip install --no-deps` into a fresh venv must import the
  package. There is no per-platform matrix, no native toolchain, and no
  emulated architecture leg: the same wheel serves Linux, macOS, and Windows
  on every supported interpreter
  (`tests/test_tools_package_data.py::test_wheel_is_pure_python_universal`
  pins the same contract locally).
- **`build-sdist`** — independent source-only build (`uv build --sdist`), so
  a wheel-verification failure cannot suppress the source release.

Both jobs upload their outputs as GitHub Actions artifacts (`wheels-universal`,
`sdist`) — Actions artifacts are CI-internal and are not directly visible to
end users or the installer.

## Manifest

A third job, **`release-manifest`**, runs after both build jobs
(`needs: [build-wheels, build-sdist]`) so it only ever aggregates already-built
and already-verified bytes — it rebuilds nothing.

It downloads the `wheels-*` and `sdist` artifacts into one directory and runs
[`scripts/generate_release_manifest.py`](scripts/generate_release_manifest.py),
which:

1. Re-verifies every wheel against the universal-wheel contract (a
   platform-specific or interpreter-specific wheel, or one carrying a native
   payload, fails loud and is never published).
2. Computes a SHA256 for every artifact and writes a flat `SHA256SUMS` file.
3. Emits `lingtai-kernel-release-manifest.json`, schema `lingtai.kernel.release/v1`
   (defined in [`scripts/lib/release_manifest.py`](scripts/lib/release_manifest.py) —
   the one source of truth for this shape; the generator, the publisher, and
   the TUI installer's consumer all import or mirror it):

   ```json
   {
     "schema": "lingtai.kernel.release/v1",
     "kernel_version": "1.0.5",
     "kernel_tag": "v1.0.5",
     "commit": "<full 40-char sha>",
     "generated_at": "2026-09-14T00:00:00Z",
     "artifacts": [
       {
         "filename": "lingtai-1.0.5-py3-none-any.whl",
         "sha256": "<64-char hex>",
         "kind": "wheel",
         "python_tag": "py3",
         "abi_tag": "none",
         "platform_tag": "any"
       },
       {
         "filename": "lingtai-1.0.5.tar.gz",
         "sha256": "<64-char hex>",
         "kind": "sdist",
         "python_tag": null,
         "abi_tag": null,
         "platform_tag": null
       }
     ],
     "sdist_fallback": "lingtai-1.0.5.tar.gz"
   }
   ```

   The schema is unchanged from the platform-wheel era: a consumer that
   selects a wheel by tag now finds one `py3/none/any` entry that matches
   every platform, and the sdist fallback remains available.

The manifest and `SHA256SUMS` are uploaded as their own `release-manifest`
Actions artifact so any run (including a manual `workflow_dispatch` shape
check) produces inspectable output without publishing anything. The publisher
also attaches both files alongside the wheel/sdist.

## Publish

`wheels.yml`'s `release-manifest` job actually publishes on the real trigger:

- **`release: {types: [published]}`** — a real kernel GitHub Release always
  publishes (uploads the manifest + assets to that GitHub release).
- **`workflow_dispatch`** — dry-run by default (the `publish` boolean input
  defaults `false`); pass `publish: true` to deliberately publish from a
  manual run too (for example to republish after a partial failure).
- Every other shape (default manual dispatch) stays dry-run, so re-running
  this workflow to sanity-check the manifest has no side effects.

### Gitee is not part of the workflow

No path in `wheels.yml` invokes Gitee synchronization or Gitee asset
publication. The workflow never runs
[`scripts/sync_gitee_mirror.py`](scripts/sync_gitee_mirror.py), never
receives `GITEE_ACCESS_TOKEN`, and always invokes the publisher with
`--skip-gitee`: a kernel release neither synchronizes commits/tags to Gitee
nor uploads assets there, so a Gitee problem can never delay, cancel, or
fail a release. The Gitee scripts remain in the repository with their own
tests, but nothing in CI invokes them.

### Publish — manifest/wheel/sdist to the GitHub release

[`scripts/publish_release_assets.py`](scripts/publish_release_assets.py)
uploads the exact manifest + asset bytes to **GitHub Releases**, via the `gh`
CLI (`gh release create` / `gh release upload`), attaching the manifest and
`SHA256SUMS` alongside the wheel/sdist.

Every mutating action requires the explicit `--execute` flag; the workflow
passes it only when the trigger is a real release (or an explicit
`publish: true` dispatch) — see "Determine publish mode" in the job. Without
it the script only prints its plan and exits 0.

Idempotency: an asset already attached under the same filename is skipped only
after the actual GitHub download bytes match the local SHA256. A same-name
asset with different bytes, missing/ambiguous metadata, or a failed download
always triggers a fail-loud error before upload planning completes.
**There is no delete-and-replace path.**

### Download-mirror dispatch (lingtai.ai acceleration)

Immediately after the publish step above uploads assets for real (never on a
dry run), `wheels.yml`'s "Notify lingtai-web download mirror" step sends one
`repository_dispatch` (`release-asset-published`) to `Lingtai-AI/lingtai-web`
naming this release's tag and every uploaded asset's filename, sha256, and
size — artifact digests come from the already-verified manifest; the
manifest and SHA256SUMS digests are computed from their published local bytes. This exists solely so `lingtai.ai` can
mirror the exact same bytes for mainland-China download acceleration; GitHub
remains the sole official release authority, and a missing or failed dispatch
never edits, retries, or undoes the GitHub release itself.

**Deployment prerequisite:** the `LINGTAI_WEB_DISPATCH_TOKEN` repository
secret (a token with `repository_dispatch` write access on
`Lingtai-AI/lingtai-web`) must be configured for this step to do anything;
until then it prints a `::warning::` and exits 0, so its absence cannot fail
a release. See `Lingtai-AI/lingtai-web`'s `docs/release-mirror/CONTRACT.md` for the
receiving side's contract.

### Manual dry run (safe, no token required)

```bash
# after a wheels.yml run, download its `wheels-*` + `sdist` artifacts into
# ./release-assets (or build them locally with `uv build --out-dir release-assets`), then:
python scripts/generate_release_manifest.py \
  --assets-dir release-assets \
  --kernel-version 1.0.5 --kernel-tag v1.0.5 \
  --commit "$(git rev-parse HEAD)" \
  --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --out-manifest release-assets/lingtai-kernel-release-manifest.json \
  --out-sha256sums release-assets/SHA256SUMS

python scripts/publish_release_assets.py \
  --manifest release-assets/lingtai-kernel-release-manifest.json \
  --assets-dir release-assets \
  --skip-gitee
# (no --execute: prints the GitHub plan only)
```

### Manual authorized publish (maintainer-run, outside this task's authorization)

```bash
python scripts/publish_release_assets.py \
  --manifest release-assets/lingtai-kernel-release-manifest.json \
  --assets-dir release-assets \
  --skip-gitee \
  --execute
```

## Non-goals (v1)

- No PyPI publication. LingTai's own kernel package is fetched by the TUI
  installer from GitHub/Gitee release assets, by explicit local file path —
  never `pip install lingtai` against any package index. Third-party
  dependency resolution is unaffected and continues to use PyPI or a
  configured mirror.
- No offline wheelhouse / vendored third-party dependency bundle.
- No Gitee synchronization or Gitee release publication from any path in
  `wheels.yml`. The Gitee scripts stay in-repo but are not invoked by CI.
