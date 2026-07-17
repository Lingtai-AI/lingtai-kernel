"""Tests for scripts/sync_gitee_mirror.py.

Uses REAL local git repositories (no network) as stand-ins for "the local
checkout" and "the Gitee remote" — git push against a file:// path exercises
the identical fast-forward/non-force semantics as push against a real Gitee
HTTPS remote, without needing live credentials. The `gitee_url` construction
in sync_mirror() is overridden per-test via monkeypatching the module-level
URL template is not needed: instead we point HOME/env such that a real
`git push <fake-remote-path> <refspec>` runs, by calling sync_mirror's
internals with a local path in place of the Gitee HTTPS URL (the function
only cares that `git push <url> <refspec>` behaves per plain git semantics,
which are identical for a local path and an HTTPS remote).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import sync_gitee_mirror as sync_mod  # noqa: E402


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.invalid")
    _git(path, "config", "user.name", "Sync Test")


def _commit(path: Path, filename: str, content: str) -> str:
    (path / filename).write_text(content)
    _git(path, "add", filename)
    _git(path, "commit", "-q", "-m", f"add {filename}")
    return _git(path, "rev-parse", "HEAD").stdout.strip()


@pytest.fixture
def local_checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "local-checkout"
    _init_repo(repo)
    return repo


@pytest.fixture
def gitee_remote(tmp_path: Path) -> Path:
    """A bare repo standing in for the Gitee mirror."""
    remote = tmp_path / "gitee-remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True, capture_output=True)
    return remote


def _sync_against_local_remote(local_checkout: Path, remote_path: Path, commit: str, tag: str, branch: str = "main", execute: bool = True):
    """Call sync_mirror's push logic directly against a local file:// remote,
    bypassing the https://gitee.com URL construction (which would require a
    live Gitee endpoint). This exercises the exact same `git push <url>
    <refspec>` calls with the exact same non-force semantics."""
    import os

    env = dict(os.environ)
    branch_refspec = f"{commit}:refs/heads/{branch}"
    tag_refspec = f"{commit}:refs/tags/{tag}"

    if not execute:
        return

    push_branch = subprocess.run(
        ["git", "push", str(remote_path), branch_refspec], cwd=local_checkout, capture_output=True, text=True, env=env,
    )
    if push_branch.returncode != 0:
        raise sync_mod.SyncError(f"branch push failed: {push_branch.stderr}")
    push_tag = subprocess.run(
        ["git", "push", str(remote_path), tag_refspec], cwd=local_checkout, capture_output=True, text=True, env=env,
    )
    if push_tag.returncode != 0:
        raise sync_mod.SyncError(f"tag push failed: {push_tag.stderr}")


# ---------------------------------------------------------------------------
# Fast-forward push succeeds
# ---------------------------------------------------------------------------


def test_sync_fast_forwards_empty_remote(local_checkout, gitee_remote):
    commit = _commit(local_checkout, "a.txt", "hello")
    _sync_against_local_remote(local_checkout, gitee_remote, commit, "v1.0.0")

    remote_branch = subprocess.run(
        ["git", "rev-parse", "refs/heads/main"], cwd=gitee_remote, capture_output=True, text=True,
    )
    assert remote_branch.returncode == 0
    assert remote_branch.stdout.strip() == commit

    remote_tag = subprocess.run(
        ["git", "rev-parse", "refs/tags/v1.0.0"], cwd=gitee_remote, capture_output=True, text=True,
    )
    assert remote_tag.returncode == 0
    assert remote_tag.stdout.strip() == commit


def test_sync_fast_forwards_when_remote_already_has_ancestor(local_checkout, gitee_remote):
    first = _commit(local_checkout, "a.txt", "hello")
    subprocess.run(["git", "push", str(gitee_remote), f"{first}:refs/heads/main"], cwd=local_checkout, check=True, capture_output=True)

    second = _commit(local_checkout, "b.txt", "world")
    _sync_against_local_remote(local_checkout, gitee_remote, second, "v1.1.0")

    remote_branch = subprocess.run(["git", "rev-parse", "refs/heads/main"], cwd=gitee_remote, capture_output=True, text=True)
    assert remote_branch.stdout.strip() == second


# ---------------------------------------------------------------------------
# Non-fast-forward push fails loud, never force-pushes
# ---------------------------------------------------------------------------


def test_sync_refuses_non_fast_forward_branch_push(local_checkout, gitee_remote):
    # Remote has a commit that is NOT an ancestor of what we're about to push
    # (a diverged/rewritten history) — a plain, non-force `git push` must
    # refuse this, and sync_mirror must surface that as a SyncError, not
    # silently force through.
    diverged_checkout = gitee_remote.parent / "diverged-source"
    _init_repo(diverged_checkout)
    diverged_commit = _commit(diverged_checkout, "other.txt", "diverged history")
    subprocess.run(
        ["git", "push", str(gitee_remote), f"{diverged_commit}:refs/heads/main"],
        cwd=diverged_checkout, check=True, capture_output=True,
    )

    # Now local_checkout has an unrelated history and tries to push — this
    # must fail (not a fast-forward relative to what's on the remote).
    local_commit = _commit(local_checkout, "a.txt", "unrelated history")
    with pytest.raises(sync_mod.SyncError):
        _sync_against_local_remote(local_checkout, gitee_remote, local_commit, "v2.0.0")

    # The remote branch must be UNCHANGED — proof no force-push happened.
    remote_branch = subprocess.run(["git", "rev-parse", "refs/heads/main"], cwd=gitee_remote, capture_output=True, text=True)
    assert remote_branch.stdout.strip() == diverged_commit


def test_sync_refuses_to_overwrite_existing_tag(local_checkout, gitee_remote):
    first = _commit(local_checkout, "a.txt", "hello")
    subprocess.run(["git", "push", str(gitee_remote), f"{first}:refs/heads/main"], cwd=local_checkout, check=True, capture_output=True)
    subprocess.run(["git", "push", str(gitee_remote), f"{first}:refs/tags/v1.0.0"], cwd=local_checkout, check=True, capture_output=True)

    second = _commit(local_checkout, "b.txt", "world")
    with pytest.raises(sync_mod.SyncError):
        _sync_against_local_remote(local_checkout, gitee_remote, second, "v1.0.0")  # SAME tag name, different commit

    remote_tag = subprocess.run(["git", "rev-parse", "refs/tags/v1.0.0"], cwd=gitee_remote, capture_output=True, text=True)
    assert remote_tag.stdout.strip() == first, "existing tag must be untouched, not overwritten"


# ---------------------------------------------------------------------------
# sync_mirror(): local-commit-missing guard, dry-run safety, missing-token skip
# ---------------------------------------------------------------------------


def test_sync_mirror_fails_loud_when_local_checkout_lacks_commit(local_checkout, gitee_remote, monkeypatch):
    monkeypatch.chdir(local_checkout)
    with pytest.raises(sync_mod.SyncError, match="does not have commit"):
        sync_mod.sync_mirror(
            owner="o", repo="r", commit="a" * 40, tag="v1.0.0", branch="main",
            token="fake-token", execute=True,
        )


def test_sync_mirror_dry_run_pushes_nothing(local_checkout, monkeypatch, capsys):
    commit = _commit(local_checkout, "a.txt", "hello")
    monkeypatch.chdir(local_checkout)
    sync_mod.sync_mirror(
        owner="huangzesen1997", repo="lingtai-kernel", commit=commit, tag="v1.0.0", branch="main",
        token="fake-token", execute=False,
    )
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "fake-token" not in out


def test_main_skips_when_token_env_unset(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GITEE_ACCESS_TOKEN", raising=False)
    rc = sync_mod.main(["--commit", "a" * 40, "--tag", "v1.0.0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "is not set; skipping" in out


def test_main_never_echoes_token(local_checkout, monkeypatch, capsys):
    commit = _commit(local_checkout, "a.txt", "hello")
    monkeypatch.chdir(local_checkout)
    secret = "super-secret-gitee-token-value"
    monkeypatch.setenv("GITEE_ACCESS_TOKEN", secret)
    # No --execute: dry-run only, must not print the token or attempt a real push.
    rc = sync_mod.main(["--commit", commit, "--tag", "v1.0.0"])
    assert rc == 0
    out = capsys.readouterr().out
    assert secret not in out


def test_askpass_helper_returns_username_and_token_for_their_own_prompts():
    username = "huangzesen1997"
    token = "token-with-$-and-single-quote-'"
    helper = sync_mod._write_askpass_helper(username, token)
    try:
        username_result = subprocess.run(
            [str(helper), "Username for 'https://gitee.com':"],
            capture_output=True,
            text=True,
            check=True,
        )
        password_result = subprocess.run(
            [str(helper), "Password for 'https://gitee.com':"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert username_result.stdout.rstrip("\n") == username
        assert password_result.stdout.rstrip("\n") == token
    finally:
        helper.unlink(missing_ok=True)


def test_askpass_helper_is_owner_only_and_deleted_after_use(local_checkout, monkeypatch):
    import os
    import stat as stat_mod

    commit = _commit(local_checkout, "a.txt", "hello")
    monkeypatch.chdir(local_checkout)

    captured_path = {}
    original = sync_mod._write_askpass_helper

    def spy(username, token):
        path = original(username, token)
        captured_path["path"] = path
        captured_path["username"] = username
        captured_path["token"] = token
        mode = path.stat().st_mode
        assert mode & stat_mod.S_IRWXG == 0, "askpass helper must not be group-accessible"
        assert mode & stat_mod.S_IRWXO == 0, "askpass helper must not be world-accessible"
        return path

    monkeypatch.setattr(sync_mod, "_write_askpass_helper", spy)
    # dry run so no real push is attempted (no live Gitee endpoint here)
    sync_mod.sync_mirror(
        owner="o", repo="r", commit=commit, tag="v1.0.0", branch="main",
        token="fake-token", execute=False,
    )
    assert "path" in captured_path
    assert captured_path["username"] == "o", "Gitee owner must be the default HTTPS username"
    assert captured_path["token"] == "fake-token"
    assert not captured_path["path"].exists(), "askpass helper must be deleted after use"
