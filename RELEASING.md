# Releasing the `lingtai` Python kernel

## Build + verify (existing, unchanged)

`.github/workflows/wheels.yml` runs on `workflow_dispatch` or when a GitHub
Release is published. Two independent jobs build the artifacts:

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
`sdist`) — Actions artifacts are CI-internal and are not directly visible to
end users or the installer.

## Manifest (new)

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

The manifest and `SHA256SUMS` are uploaded as their own `release-manifest`
Actions artifact so any run (including a manual `workflow_dispatch` shape
check) produces inspectable output without publishing anything.

## Publish

`wheels.yml`'s `release-manifest` job actually publishes on the real trigger:

- **`release: {types: [published]}`** — a real kernel GitHub Release always
  publishes (syncs Gitee, then uploads to GitHub + Gitee).
- **`workflow_dispatch`** — dry-run by default (the `publish` boolean input
  defaults `false`); pass `publish: true` to deliberately publish from a
  manual run too (for example to republish after a partial failure).
- Every other shape (default manual dispatch) stays dry-run, so re-running
  this workflow to sanity-check the manifest/wheel matrix has no side
  effects.

### Step 1 — non-force Gitee mirror sync (only when publishing)

[`scripts/sync_gitee_mirror.py`](scripts/sync_gitee_mirror.py) pushes the
exact release commit to `main` and creates the exact release tag on
`huangzesen1997/lingtai-kernel`, using a **non-force** `git push` for both:
the branch push only succeeds as a fast-forward, and the tag push only
succeeds if that tag name doesn't already point somewhere else. Either
condition failing is a **fail-loud stop** for the whole publish (the workflow
does not proceed to upload against an unsynchronized mirror). The token
travels via a short-lived, owner-only-permission `GIT_ASKPASS` helper file
(deleted after use) — never in argv, a URL, or a log line. Skips cleanly
(prints why, exits 0) when `GITEE_ACCESS_TOKEN` is unset.

### Step 2 — publish manifest/wheels/sdist

[`scripts/publish_release_assets.py`](scripts/publish_release_assets.py)
uploads the exact manifest + asset bytes to:

- **GitHub Releases**, via the `gh` CLI (`gh release create` / `gh release
  upload`), attaching the manifest and `SHA256SUMS` alongside the wheels/sdist.
- **Gitee Releases** (`huangzesen1997/lingtai-kernel`), via the Gitee v5 REST
  API, gated entirely on the `GITEE_ACCESS_TOKEN` secret. When that secret is
  unset the Gitee leg is skipped with a printed reason — it is never a hard
  failure, since Gitee credentials/configuration are a separate authorization
  step from having the workflow code in place.

Every mutating action requires the explicit `--execute` flag; the workflow
passes it only when the trigger is a real release (or an explicit
`publish: true` dispatch) — see "Determine publish mode" in the job. Without
it the script only prints its plan and exits 0.

Before publishing to Gitee, the script re-verifies (independent of Step 1's
push) that the Gitee tag ref already points at the exact release commit
(`gitee_verify_tag_synchronized`) — belt-and-braces after Step 1's sync.
**Neither script ever force-pushes or otherwise mutates history** on the
Gitee git repository.

Idempotency: an asset already attached under the same filename is skipped
(GitHub: by name; Gitee: by name, since Gitee's attachment listing exposes no
checksum). A same-name Gitee attachment already present with different bytes
always triggers `AssetConflict` for a human to investigate — see
`plan_gitee_uploads`. **There is no delete-and-replace path.** If a bad asset
was ever uploaded, remove it by hand through the Gitee UI/API and re-run.

### Manual dry run (safe, no token required)

```bash
# after a wheels.yml run, download its `wheels-*` + `sdist` artifacts into
# ./release-assets, then:
python scripts/generate_release_manifest.py \
  --assets-dir release-assets \
  --kernel-version 0.16.4 --kernel-tag v0.16.4 \
  --commit "$(git rev-parse HEAD)" \
  --generated-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --out-manifest release-assets/lingtai-kernel-release-manifest.json \
  --out-sha256sums release-assets/SHA256SUMS

python scripts/publish_release_assets.py \
  --manifest release-assets/lingtai-kernel-release-manifest.json \
  --assets-dir release-assets
# (no --execute: prints the GitHub/Gitee plan only)
```

### Manual authorized publish (maintainer-run, outside this task's authorization)

```bash
export GITEE_ACCESS_TOKEN=...  # never echo or log this
python scripts/sync_gitee_mirror.py \
  --commit "$(git rev-parse HEAD)" --tag v0.16.4 --branch main --execute
python scripts/publish_release_assets.py \
  --manifest release-assets/lingtai-kernel-release-manifest.json \
  --assets-dir release-assets \
  --execute
```

As of this writing the Gitee kernel mirror's `main` lags GitHub's `main` and
has no releases — the first real publish run is also the first time the sync
step has anything to fast-forward from empty, which is the expected/supported
case (see `test_sync_fast_forwards_empty_remote`).

## Non-goals (v1)

- No PyPI publication. LingTai's own kernel package is fetched by the TUI
  installer from GitHub/Gitee release assets, by explicit local file path —
  never `pip install lingtai` against any package index. Third-party
  dependency resolution is unaffected and continues to use PyPI or a
  configured mirror.
- No offline wheelhouse / vendored third-party dependency bundle.
- No automatic Gitee mirror synchronization from this repository or workflow.
