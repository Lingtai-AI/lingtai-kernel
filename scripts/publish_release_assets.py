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

Idempotency: for each asset, an existing same-name attachment is downloaded
and hashed before it is accepted as an idempotent skip. A different digest,
missing/ambiguous metadata, or download failure is fail-loud. Comparison files
are retained under unique runner temp paths. There is no delete-and-replace
cleanup path by design.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import uuid
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


def release_files(manifest, assets_dir: Path, manifest_path: Path) -> list[Path]:
    checksum_path = assets_dir / "SHA256SUMS"
    if not checksum_path.is_file():
        raise PublishError(f"release checksum file is missing on disk: {checksum_path}")
    return [assets_dir / a.filename for a in manifest.artifacts] + [manifest_path, checksum_path]


def _comparison_path(provider: str, filename: str) -> Path:
    root = Path(os.environ.get("RELEASE_ASSET_COMPARISON_DIR", tempfile.gettempdir()))
    directory = root / "lingtai-release-comparisons" / provider
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}-{filename}"
    if path.exists():
        raise PublishError(f"refusing comparison-path collision: {path}")
    return path


# ---------------------------------------------------------------------------
# GitHub (gh CLI)
# ---------------------------------------------------------------------------


def gh_release_exists(tag: str, repo_slug: str) -> bool:
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo_slug],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def gh_release_asset_shas(tag: str, repo_slug: str) -> dict[str, dict]:
    """Return GitHub release asset metadata, including download URLs."""
    result = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo_slug, "--json", "assets"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise PublishError("GitHub release asset metadata lookup failed; refusing to plan uploads")
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise PublishError("GitHub release asset metadata was not valid JSON; refusing to plan uploads") from exc
    if not isinstance(data, dict):
        raise PublishError("GitHub release asset metadata has an invalid top-level shape")
    if "assets" not in data:
        raise PublishError("GitHub release asset metadata is missing assets")
    assets = data["assets"]
    if not isinstance(assets, list):
        raise PublishError("GitHub release asset metadata is ambiguous: assets is not a list")
    result = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise PublishError("GitHub release asset metadata is ambiguous: asset is not an object")
        name = asset.get("name")
        if not isinstance(name, str) or not name:
            raise PublishError("GitHub release asset metadata is ambiguous: asset has no name")
        if name in result:
            raise PublishError(f"GitHub release has ambiguous duplicate asset name {name!r}")
        url = _github_asset_url(asset)
        if not isinstance(url, str) or not url:
            raise PublishError(f"GitHub asset {name!r} has no usable download URL")
        result[name] = asset
    return result


def _github_asset_url(asset: dict) -> str | None:
    return (
        asset.get("apiUrl")
        or asset.get("api_url")
        or asset.get("url")
        or asset.get("browserDownloadUrl")
        or asset.get("browser_download_url")
    )


def _download_github_asset(asset: dict, filename: str) -> Path:
    url = _github_asset_url(asset)
    if not isinstance(url, str) or not url:
        raise PublishError(f"GitHub asset {filename!r} has no usable download URL")
    destination = _comparison_path("github", filename)
    result = subprocess.run(
        ["gh", "api", url, "--header", "Accept: application/octet-stream", "--output", str(destination)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not destination.is_file():
        raise PublishError(f"GitHub asset {filename!r} download failed; refusing to trust a name collision")
    return destination


def plan_github_uploads(manifest, assets_dir: Path, manifest_path: Path, repo_slug: str, tag: str) -> list[Path]:
    """Return the list of local file paths that still need uploading to GitHub.

    Existing names are accepted only after downloading and hashing the actual
    GitHub asset bytes. Reported size or digest metadata is not sufficient.
    """
    existing = gh_release_asset_shas(tag, repo_slug)
    to_upload = []
    all_files = release_files(manifest, assets_dir, manifest_path)
    for path in all_files:
        asset = existing.get(path.name)
        if asset is not None:
            remote_sha = sha256_file(_download_github_asset(asset, path.name))
            local_sha = sha256_file(path)
            if remote_sha != local_sha:
                raise AssetConflict(
                    f"GitHub asset {path.name!r} differs: local sha256={local_sha}, remote sha256={remote_sha}"
                )
            print(f"  [github] {path.name}: existing bytes sha256={local_sha}; skipping (idempotent)")
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
    attachments = release.get("attach_files", [])
    if not isinstance(attachments, list):
        raise PublishError("Gitee attachment metadata is ambiguous: attach_files is not a list")
    result = {}
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise PublishError("Gitee attachment metadata is ambiguous: attachment is not an object")
        name = attachment.get("name")
        if not isinstance(name, str) or not name:
            raise PublishError("Gitee attachment metadata is ambiguous: attachment has no name")
        if name in result:
            raise PublishError(f"Gitee release has ambiguous duplicate attachment name {name!r}")
        result[name] = attachment
    return result


def _gitee_download_sha256(attachment: dict, filename: str, token: str) -> str:
    url = (
        attachment.get("browserDownloadUrl")
        or attachment.get("browser_download_url")
        or attachment.get("download_url")
        or attachment.get("url")
    )
    if not isinstance(url, str) or not url:
        raise PublishError(f"Gitee attachment {filename!r} has no usable download URL")
    destination = _comparison_path("gitee", filename)
    separator = "&" if "?" in url else "?"
    request = urllib.request.Request(url + f"{separator}access_token={token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            with destination.open("xb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        raise PublishError(
            f"Gitee attachment {filename!r} download/hash failed; refusing to trust a name collision"
        ) from exc
    return sha256_file(destination)


def plan_gitee_uploads(manifest, assets_dir: Path, manifest_path: Path, owner: str, repo: str, release_id: int, token: str) -> list[Path]:
    existing = gitee_existing_attachments(owner, repo, release_id, token)
    to_upload = []
    all_files = release_files(manifest, assets_dir, manifest_path)
    for path in all_files:
        att = existing.get(path.name)
        if att is None:
            to_upload.append(path)
            continue
        remote_sha = _gitee_download_sha256(att, path.name, token)
        local_sha = sha256_file(path)
        if remote_sha != local_sha:
            raise AssetConflict(
                f"Gitee attachment {path.name!r} differs: local sha256={local_sha}, remote sha256={remote_sha}"
            )
        print(f"  [gitee] {path.name}: existing bytes sha256={local_sha}; skipping (idempotent)")
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
