#!/usr/bin/env python3
"""Non-force-synchronize the exact release commit/tag to the Gitee kernel
mirror before a Gitee release is created/attached to.

This is deliberately a SEPARATE, git-level step from publish_release_assets.py
(which only speaks the Gitee v5 REST API for release/attachment management).
Gitee's release/tag lookup requires the tag to already exist on the Gitee git
repository — publish_release_assets.py's gitee_verify_tag_synchronized() only
checks that precondition; this script is what can actually establish it,
safely.

Safety:
  - Every push is NON-FORCE. `git push` (not `--force`) on the branch ref
    only succeeds if it is a fast-forward; a diverged/rewritten history on
    Gitee's side fails loud rather than being silently overwritten.
  - The tag push targets exactly one tag name and never overwrites an
    existing tag that points somewhere else (`git push` refuses non-fast-
    -forward tag updates by default; this script does not add `--force`).
  - The token is passed via a short-lived git credential (an askpass helper
    script written to a private temp file, chmod 600, deleted after use) and
    is never included in argv, logged, or embedded in the remote URL string
    that could appear in error output.
  - `--execute` gates every mutating action; without it, prints the plan only.

Usage:
    export GITEE_ACCESS_TOKEN=...  # never echo or log this
    python scripts/sync_gitee_mirror.py \\
        --owner huangzesen1997 --repo lingtai-kernel \\
        --commit <full-sha> --tag v0.16.4 --branch main \\
        --execute
"""
from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


class SyncError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _write_askpass_helper(token: str) -> Path:
    """Write a private (0600) askpass helper script that prints the token.

    git invokes this as `<helper> <prompt>`; it must print the credential to
    stdout and nothing else. Using GIT_ASKPASS instead of embedding the token
    in the remote URL keeps the token out of `git remote -v`, process argv
    visible via `ps`, and any URL that might appear in a git error message.
    """
    fd, path_str = tempfile.mkstemp(prefix="gitee-askpass-", suffix=".sh")
    path = Path(path_str)
    with os.fdopen(fd, "w") as f:
        f.write("#!/bin/sh\n")
        f.write(f'printf "%s\\n" "{token}"\n')
    path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)  # 0600 + exec, owner-only
    return path


def sync_mirror(
    owner: str,
    repo: str,
    commit: str,
    tag: str,
    branch: str,
    token: str,
    execute: bool,
) -> None:
    gitee_url = f"https://gitee.com/{owner}/{repo}.git"
    askpass_path = _write_askpass_helper(token)
    try:
        env = dict(os.environ)
        env["GIT_ASKPASS"] = str(askpass_path)
        env["GIT_TERMINAL_PROMPT"] = "0"  # never fall back to an interactive prompt

        # Verify the local checkout actually has the commit we're about to sync.
        verify = _run(["git", "cat-file", "-e", f"{commit}^{{commit}}"])
        if verify.returncode != 0:
            raise SyncError(f"local checkout does not have commit {commit}: {verify.stderr.strip()}")

        branch_refspec = f"{commit}:refs/heads/{branch}"
        tag_refspec = f"{commit}:refs/tags/{tag}"

        if not execute:
            print(f"DRY RUN: would non-force push {commit[:12]} -> {owner}/{repo}#{branch} (fast-forward only)")
            print(f"DRY RUN: would push tag {tag} -> {owner}/{repo} (create-only, no overwrite)")
            return

        print(f"Pushing {commit[:12]} to {owner}/{repo}#{branch} (non-force, fast-forward only)...")
        push_branch = _run(["git", "push", gitee_url, branch_refspec], env=env)
        if push_branch.returncode != 0:
            raise SyncError(
                f"non-force push to {owner}/{repo}#{branch} failed (not a fast-forward, or auth "
                f"failed): {push_branch.stderr.strip()}\n"
                "This script never force-pushes. Investigate and resolve the divergence "
                "out of band, then retry."
            )
        print(f"  OK: {branch} fast-forwarded to {commit[:12]}")

        print(f"Pushing tag {tag} to {owner}/{repo} (create-only)...")
        push_tag = _run(["git", "push", gitee_url, tag_refspec], env=env)
        if push_tag.returncode != 0:
            raise SyncError(
                f"tag push for {tag} failed (tag may already exist pointing elsewhere, or auth "
                f"failed): {push_tag.stderr.strip()}\n"
                "This script never overwrites an existing tag. Investigate and resolve out of "
                "band, then retry."
            )
        print(f"  OK: tag {tag} created at {commit[:12]}")
    finally:
        askpass_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--owner", default="huangzesen1997")
    parser.add_argument("--repo", default="lingtai-kernel")
    parser.add_argument("--commit", required=True, help="full commit SHA to synchronize")
    parser.add_argument("--tag", required=True, help="release tag to synchronize, e.g. v0.16.4")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--token-env", default="GITEE_ACCESS_TOKEN", help="env var holding the Gitee token; never printed")
    parser.add_argument("--execute", action="store_true", help="actually push; default is dry-run/plan-only")
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env, "")
    if not token:
        print(f"{args.token_env} is not set; skipping Gitee mirror sync.")
        return 0

    try:
        sync_mirror(args.owner, args.repo, args.commit, args.tag, args.branch, token, args.execute)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
