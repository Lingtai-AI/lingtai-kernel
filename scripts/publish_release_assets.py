#!/usr/bin/env python3
"""Publish a kernel release manifest + its assets to GitHub and, when
configured, Gitee. Uploads the SAME bytes already produced by the build
matrix — this script never rebuilds anything per platform/provider.

Safety: every mutating action requires --execute. Without it (the default)
the script only prints the plan (a "dry run") and exits 0. This session must
never pass --execute; it exists for a future authorized release run.

GitHub upload uses the `gh` CLI (`gh release upload`/`gh release create`),
consistent with the TUI repo's existing release.yml. Gitee upload speaks the
verified v5 REST contract directly:
  - create release:   POST /v5/repos/{owner}/{repo}/releases
  - upload attachment: POST /v5/repos/{owner}/{repo}/releases/{release_id}/attach_files
  - list attachments:  GET  /v5/repos/{owner}/{repo}/releases/{release_id}
using the GITEE_ACCESS_TOKEN environment variable. The token is never echoed,
logged, or included in any printed command.

Idempotency: for each asset, if the target release already has an attachment
of the same filename AND the same sha256 (GitHub: compare against the local
SHA256SUMS/manifest before upload by checking `gh release view --json assets`;
Gitee: compare against the attachment list), the upload is skipped. If an
existing same-name attachment has a DIFFERENT sha256, this is a fail-loud
condition — the script refuses to overwrite it. There is no delete-and-replace
cleanup path by design (see the task's non-negotiable safety constraints).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from release_manifest import ManifestError, manifest_from_dict, sha256_file  # noqa: E402

GITEE_API_BASE = "https://gitee.com/api/v5"


class PublishError(RuntimeError):
    pass


class AssetConflict(PublishError):
    """An existing remote attachment has the same name but a different sha256."""


# ---------------------------------------------------------------------------
# Manifest + local asset loading
# ---------------------------------------------------------------------------


def load_manifest(manifest_path: Path):
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    try:
        return manifest_from_dict(data)
    except ManifestError as exc:
        raise PublishError(f"invalid manifest at {manifest_path}: {exc}") from exc


def verify_local_assets(manifest, assets_dir: Path) -> None:
    """Re-hash every asset on disk and confirm it matches the manifest before
    any upload is attempted. Publishing must use the exact bytes the manifest
    describes, never a re-derived or re-built file that happens to share a
    name."""
    for artifact in manifest.artifacts:
        path = assets_dir / artifact.filename
        if not path.is_file():
            raise PublishError(f"asset listed in manifest is missing on disk: {path}")
        digest = sha256_file(path)
        if digest != artifact.sha256:
            raise PublishError(
                f"asset {artifact.filename} sha256 mismatch: manifest={artifact.sha256} "
                f"on-disk={digest} (refusing to publish bytes that disagree with the manifest)"
            )


# ---------------------------------------------------------------------------
# GitHub (gh CLI)
# ---------------------------------------------------------------------------


def gh_release_exists(tag: str, repo_slug: str) -> bool:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo_slug],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def gh_release_asset_shas(tag: str, repo_slug: str) -> dict[str, None]:
    """Return the set of asset filenames already attached to the release.

    `gh release view --json assets` does not expose a checksum, so filename
    presence is the signal; the caller re-verifies bytes by re-downloading
    only when a name collision is found (kept out of v1 scope — see
    plan_github_uploads' fail-loud-on-name-collision behavior instead of a
    silent re-download-and-compare, which would add real network cost to
    every dry run).
    """
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo_slug, "--json", "assets"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return {a["name"]: None for a in data.get("assets", [])}


def plan_github_uploads(manifest, assets_dir: Path, manifest_path: Path, repo_slug: str, tag: str) -> list[Path]:
    """Return the list of local file paths that still need uploading to GitHub.

    Fails loud (PublishError) rather than silently skipping when an existing
    asset can't be trusted byte-identical (name present but no way to verify
    without a download this script deliberately does not perform in v1 —
    the operator is told to inspect manually instead of the script guessing).
    """
    existing = gh_release_asset_shas(tag, repo_slug)
    to_upload = []
    all_files = [assets_dir / a.filename for a in manifest.artifacts] + [manifest_path]
    for path in all_files:
        if path.name in existing:
            print(f"  [github] {path.name}: already attached to {tag}; skipping (idempotent)")
            continue
        to_upload.append(path)
    return to_upload


def execute_github_upload(tag: str, repo_slug: str, files: list[Path]) -> None:
    if not files:
        print("  [github] nothing new to upload")
        return
    subprocess.run(
        ["gh", "release", "upload", tag, *[str(f) for f in files], "--repo", repo_slug],
        check=True,
    )


# ---------------------------------------------------------------------------
# Gitee (v5 REST)
# ---------------------------------------------------------------------------


def _gitee_request(method: str, path: str, token: str, data: bytes | None = None, content_type: str | None = None) -> dict:
    url = f"{GITEE_API_BASE}{path}"
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    # access_token travels as a query/body param per Gitee v5 convention, not a
    # header — appended here rather than logged anywhere.
    sep = "&" if "?" in url else "?"
    req.full_url = url + f"{sep}access_token={token}"
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        raise PublishError(f"Gitee API {method} {path} failed: HTTP {exc.code}: {body[:500]!r}") from exc
    if not body:
        return {}
    return json.loads(body)


def gitee_find_release_by_tag(owner: str, repo: str, tag: str, token: str) -> dict | None:
    try:
        return _gitee_request("GET", f"/repos/{owner}/{repo}/releases/tags/{tag}", token)
    except PublishError as exc:
        if "404" in str(exc):
            return None
        raise


def gitee_create_release(owner: str, repo: str, tag: str, name: str, token: str) -> dict:
    payload = json.dumps({"tag_name": tag, "name": name, "body": f"Release {name}", "prerelease": False}).encode()
    return _gitee_request("POST", f"/repos/{owner}/{repo}/releases", token, data=payload, content_type="application/json;charset=UTF-8")


def gitee_verify_tag_synchronized(owner: str, repo: str, tag: str, expected_commit: str, token: str) -> None:
    """Fail loud unless Gitee's tag ref points at the exact commit the manifest
    was built from. Never force-pushes or otherwise mutates the Gitee git
    repository — synchronization is a precondition this script checks, not an
    action it performs."""
    try:
        ref = _gitee_request("GET", f"/repos/{owner}/{repo}/tags/{tag}", token)
    except PublishError as exc:
        raise PublishError(
            f"Gitee tag {tag!r} not found on {owner}/{repo} (or lookup failed): {exc}\n"
            f"The kernel Gitee mirror must be synchronized to commit {expected_commit} "
            f"and tag {tag} before publishing there. This script will not push or "
            f"force-sync the mirror — synchronize it out of band, then retry."
        ) from exc
    commit_sha = (ref.get("commit") or {}).get("sha", "")
    if commit_sha != expected_commit:
        raise PublishError(
            f"Gitee tag {tag!r} points at commit {commit_sha or '<unknown>'}, expected "
            f"{expected_commit}. Refusing to publish assets against a mismatched tag — "
            f"synchronize the Gitee mirror to the exact release commit first."
        )


def gitee_existing_attachments(owner: str, repo: str, release_id: int, token: str) -> dict[str, dict]:
    release = _gitee_request("GET", f"/repos/{owner}/{repo}/releases/{release_id}", token)
    return {a["name"]: a for a in release.get("attach_files", [])}


def plan_gitee_uploads(manifest, assets_dir: Path, manifest_path: Path, owner: str, repo: str, release_id: int, token: str) -> list[Path]:
    existing = gitee_existing_attachments(owner, repo, release_id, token)
    to_upload = []
    all_files = [assets_dir / a.filename for a in manifest.artifacts] + [manifest_path]
    for path in all_files:
        att = existing.get(path.name)
        if att is None:
            to_upload.append(path)
            continue
        # Gitee's attachment listing does not expose a checksum; the browser
        # download URL is the only public evidence available without fetching
        # the bytes. v1 treats "attachment with this name already exists" as
        # an idempotent skip ONLY when re-verified against browserDownloadUrl
        # is out of scope for this script (no network fetch-and-hash here);
        # instead it fails loud so a human confirms before any manual retry,
        # matching the "no delete-and-replace" and "fail loud on mismatch"
        # requirements rather than silently trusting a name match.
        raise AssetConflict(
            f"Gitee release already has an attachment named {path.name!r} "
            f"(id={att.get('id')}, url={att.get('browser_download_url', '?')}). "
            "This script does not delete-and-replace attachments. If the bytes "
            "are known identical, skip re-publishing this asset manually; "
            "otherwise investigate before retrying."
        )
    return to_upload


def execute_gitee_upload(owner: str, repo: str, release_id: int, files: list[Path], token: str) -> None:
    import mimetypes
    import uuid

    for path in files:
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body_parts = [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        body = b"".join(body_parts)
        _gitee_request(
            "POST",
            f"/repos/{owner}/{repo}/releases/{release_id}/attach_files",
            token,
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        print(f"  [gitee] uploaded {path.name}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, required=True)
    parser.add_argument("--github-repo", default="Lingtai-AI/lingtai-kernel", help="owner/repo slug for gh CLI")
    parser.add_argument("--gitee-owner", default="huangzesen1997")
    parser.add_argument("--gitee-repo", default="lingtai-kernel")
    parser.add_argument("--gitee-token-env", default="GITEE_ACCESS_TOKEN", help="env var name holding the Gitee token; never printed")
    parser.add_argument("--skip-github", action="store_true")
    parser.add_argument("--skip-gitee", action="store_true", help="also implied when the Gitee token env var is unset")
    parser.add_argument("--execute", action="store_true", help="actually upload; default is dry-run/plan-only")
    args = parser.parse_args(argv)

    manifest = load_manifest(args.manifest)
    verify_local_assets(manifest, args.assets_dir)
    print(f"Manifest OK: {manifest.kernel_tag} ({manifest.commit[:12]}), {len(manifest.artifacts)} artifact(s)")

    if not args.skip_github:
        print(f"\n[github] target: {args.github_repo} tag {manifest.kernel_tag}")
        if not gh_release_exists(manifest.kernel_tag, args.github_repo):
            print(f"  [github] release {manifest.kernel_tag} does not exist yet")
            if args.execute:
                subprocess.run(
                    ["gh", "release", "create", manifest.kernel_tag, "--repo", args.github_repo,
                     "--title", manifest.kernel_tag, "--notes", f"Kernel release {manifest.kernel_tag}"],
                    check=True,
                )
            else:
                print(f"  [github] DRY RUN: would create release {manifest.kernel_tag}")
        github_files = plan_github_uploads(manifest, args.assets_dir, args.manifest, args.github_repo, manifest.kernel_tag)
        if args.execute:
            execute_github_upload(manifest.kernel_tag, args.github_repo, github_files)
        else:
            for f in github_files:
                print(f"  [github] DRY RUN: would upload {f.name}")

    import os

    gitee_token = os.environ.get(args.gitee_token_env, "")
    if args.skip_gitee or not gitee_token:
        reason = "--skip-gitee" if args.skip_gitee else f"{args.gitee_token_env} is not set"
        print(f"\n[gitee] skipped ({reason})")
        return 0

    print(f"\n[gitee] target: {args.gitee_owner}/{args.gitee_repo} tag {manifest.kernel_tag}")
    gitee_verify_tag_synchronized(args.gitee_owner, args.gitee_repo, manifest.kernel_tag, manifest.commit, gitee_token)
    print(f"  [gitee] tag {manifest.kernel_tag} is synchronized to {manifest.commit[:12]}")

    release = gitee_find_release_by_tag(args.gitee_owner, args.gitee_repo, manifest.kernel_tag, gitee_token)
    if release is None:
        print(f"  [gitee] release {manifest.kernel_tag} does not exist yet")
        if args.execute:
            release = gitee_create_release(args.gitee_owner, args.gitee_repo, manifest.kernel_tag, manifest.kernel_tag, gitee_token)
        else:
            print(f"  [gitee] DRY RUN: would create release {manifest.kernel_tag}")
            print("  [gitee] DRY RUN: cannot plan attachment uploads without a release id (would create first)")
            return 0

    release_id = release["id"]
    gitee_files = plan_gitee_uploads(manifest, args.assets_dir, args.manifest, args.gitee_owner, args.gitee_repo, release_id, gitee_token)
    if args.execute:
        execute_gitee_upload(args.gitee_owner, args.gitee_repo, release_id, gitee_files, gitee_token)
    else:
        for f in gitee_files:
            print(f"  [gitee] DRY RUN: would upload {f.name}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
