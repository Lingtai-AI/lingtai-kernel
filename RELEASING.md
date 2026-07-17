# Releasing the `lingtai` Python kernel

## Phase A — prerelease build + verify

`.github/workflows/wheels.yml` runs only on `workflow_dispatch`. It has
`contents: read` permission and cannot create a release, push a mirror, or
publish assets. Two independent jobs build the artifacts:

- **`build-wheels`** — cibuildwheel matrix across cp311/cp312/cp313 for Linux
  (x86_64 + aarch64), macOS (Intel + Apple Silicon), and Windows. Every wheel
  carries the native Rust search sidecar (`lingtai/bin/lingtai-search-sidecar`)
  and is verified with `tests/test_wheel_sidecar_smoke.py --auto` before
  upload. **Never treat a plain `pip wheel` / `uv build --wheel` /
  `python -m build --wheel` output as a release artifact** — without
  `LINGTAI_REQUIRE_RUST_BUILD=1` and the cibuildwheel toolchain it silently
  produces a pure-Python `py3-none-any` wheel with no sidecar, which the
  installer would then ship as if it were a full platform build.
- **`build-sdist`** — independent source-only build (`uv build --sdist`), no
  Rust required.

Both jobs upload their outputs as GitHub Actions artifacts (`wheels-<os>`,
`sdist`) for the aggregation job. These intermediate artifacts are CI-internal
and are not the mechanical release handoff.

## Immutable release bundle

A third job, **`release-manifest`**, runs after both build jobs
(`needs: [build-wheels, build-sdist]`) so it only ever aggregates already-built
and already-verified bytes — it rebuilds nothing per platform or provider.

It downloads every `wheels-*` and the `sdist` artifact into one directory and
runs [`scripts/generate_release_manifest.py`](scripts/generate_release_manifest.py),
which:

1. Re-rejects any stray `py3-none-any` wheel outright (belt-and-braces on top
   of the build-time guard above).
2. Re-runs the sidecar validation contract
   (`tests/test_wheel_sidecar_smoke.py --auto`) against every wheel.
3. Computes a SHA256 for every artifact and writes a flat `SHA256SUMS` file.
4. Emits `lingtai-kernel-release-manifest.json`, schema `lingtai.kernel.release/v1`
   (defined in [`scripts/lib/release_manifest.py`](scripts/lib/release_manifest.py) —
   the one source of truth for this shape; the generator, the publisher, and
   the TUI installer's consumer all import or mirror it):

   ```json
   {
     "schema": "lingtai.kernel.release/v1",
     "kernel_version": "0.16.4",
     "kernel_tag": "v0.16.4",
     "commit": "<full 40-char sha>",
     "generated_at": "2026-07-15T00:00:00Z",
     "artifacts": [
       {
         "filename": "lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl",
         "sha256": "<64-char hex>",
         "kind": "wheel",
         "python_tag": "cp312",
         "abi_tag": "cp312",
         "platform_tag": "macosx_11_0_arm64"
       },
       {
         "filename": "lingtai-0.16.4.tar.gz",
         "sha256": "<64-char hex>",
         "kind": "sdist",
         "python_tag": null,
         "abi_tag": null,
         "platform_tag": null
       }
     ],
     "sdist_fallback": "lingtai-0.16.4.tar.gz"
   }
   ```

The checked wheels, sdist, manifest, and `SHA256SUMS` are uploaded together as
one `release-bundle` Actions artifact. That exact bundle is the only handoff to
Phase B. Do not download the intermediate `wheels-*` / `sdist` artifacts or run
the generator again during mechanical release.

## Phase B — mechanical publish (separate)

`wheels.yml` never enters this phase: it has no release trigger, `publish`
input, repository write permission, mirror-sync step, or publisher step.
Mechanical publish starts only after a maintainer selects one successful
prerelease run and receives explicit release authorization.

1. Download that run's exact `release-bundle` into one `release-assets`
   directory. Do not rebuild or regenerate it.
2. Run the publisher without `--execute` when a read-only plan is needed. It
   validates the manifest and every local artifact hash.
3. If Gitee is selected, run the separate non-force mirror sync for the exact
   commit and tag. A divergence or conflicting tag stops the release.
4. Run the publisher with `--execute`. It completes every selected GitHub and
   Gitee release/attachment plan before its first release create/upload call,
   then executes only those recorded mutations.

[`scripts/publish_release_assets.py`](scripts/publish_release_assets.py)
attaches the exact bundle's manifest, `SHA256SUMS`, wheels, and sdist to GitHub
Releases and, when `GITEE_ACCESS_TOKEN` is configured, Gitee Releases. Every
release create/upload requires the explicit `--execute` flag. Without it the
script prints the plan and exits without release mutation.

[`scripts/sync_gitee_mirror.py`](scripts/sync_gitee_mirror.py) remains a
separate git-level prerequisite for Gitee. Its branch and tag pushes are
non-force; the publisher independently verifies that the Gitee tag points at
the exact release commit before it plans Gitee attachments. Neither path has
a delete-and-replace or force-push mode.

Idempotency: an asset already attached under the same filename is skipped only
after the actual GitHub or Gitee download bytes match the local SHA256. A
same-name asset with different bytes, missing/ambiguous metadata, or a failed
download is a fail-loud stop before upload planning completes.

### Read-only plan from a frozen bundle

```bash
# RUN_ID is the selected successful workflow_dispatch run.
gh run download "$RUN_ID" --name release-bundle --dir release-assets

python scripts/publish_release_assets.py \
  --manifest release-assets/lingtai-kernel-release-manifest.json \
  --assets-dir release-assets \
  --skip-gitee
# No --execute: validates exact local bytes and prints the GitHub plan.

# Optional Gitee mirror plan; also no --execute.
python scripts/sync_gitee_mirror.py \
  --commit "$(git rev-parse HEAD)" --tag v0.16.4 --branch main
```

A fresh Gitee release cannot plan attachments until its tag exists on Gitee.
That tag is established only in the authorized mechanical phase below; after
sync, the publisher plans both selected providers before creating either
release or uploading any attachment.

### Authorized mechanical publish

```bash
# External release authority is required before either --execute call.
export GITEE_ACCESS_TOKEN=...  # never echo or log this
python scripts/sync_gitee_mirror.py \
  --commit "$(git rev-parse HEAD)" --tag v0.16.4 --branch main --execute
python scripts/publish_release_assets.py \
  --manifest release-assets/lingtai-kernel-release-manifest.json \
  --assets-dir release-assets \
  --execute
```

A failed guard or plan stops Phase B. Repair belongs back in Phase A; do not
add tests, rebuild artifacts, regenerate the manifest, or widen release scope
while publishing.

## Non-goals (v1)

- No PyPI publication. LingTai's own kernel package is fetched by the TUI
  installer from GitHub/Gitee release assets, by explicit local file path —
  never `pip install lingtai` against any package index. Third-party
  dependency resolution is unaffected and continues to use PyPI or a
  configured mirror.
- No offline wheelhouse / vendored third-party dependency bundle.
- No automatic Gitee mirror synchronization from this repository or workflow.
