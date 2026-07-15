"""Tests for scripts/publish_release_assets.py.

No real network or `gh` calls: subprocess and the Gitee HTTP seam
(_gitee_request) are monkeypatched with deterministic fakes. Covers dry-run
default safety, idempotent same-name skip, fail-loud on sha mismatch /
unsynchronized Gitee tag / conflicting attachment, and that --execute is
required for any mutating call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

import publish_release_assets as pub  # noqa: E402
from release_manifest import ReleaseManifest, Artifact, sha256_file  # noqa: E402


def _sample_manifest_and_assets(tmp_path: Path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    wheel = assets_dir / "lingtai-0.16.4-cp312-cp312-macosx_11_0_arm64.whl"
    wheel.write_bytes(b"wheel-bytes")
    sdist = assets_dir / "lingtai-0.16.4.tar.gz"
    sdist.write_bytes(b"sdist-bytes")

    manifest = ReleaseManifest(
        kernel_version="0.16.4",
        kernel_tag="v0.16.4",
        commit="a" * 40,
        generated_at="2026-07-15T00:00:00Z",
        artifacts=(
            Artifact(wheel.name, sha256_file(wheel), "wheel", "cp312", "cp312", "macosx_11_0_arm64"),
            Artifact(sdist.name, sha256_file(sdist), "sdist", None, None, None),
        ),
        sdist_fallback=sdist.name,
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest.to_dict()), encoding="utf-8")
    return manifest, assets_dir, manifest_path


# ---------------------------------------------------------------------------
# verify_local_assets
# ---------------------------------------------------------------------------


def test_verify_local_assets_passes_for_matching_bytes(tmp_path: Path):
    manifest, assets_dir, _ = _sample_manifest_and_assets(tmp_path)
    pub.verify_local_assets(manifest, assets_dir)  # no raise


def test_verify_local_assets_fails_loud_on_tampered_bytes(tmp_path: Path):
    manifest, assets_dir, _ = _sample_manifest_and_assets(tmp_path)
    (assets_dir / manifest.artifacts[0].filename).write_bytes(b"tampered")
    with pytest.raises(pub.PublishError, match="sha256 mismatch"):
        pub.verify_local_assets(manifest, assets_dir)


def test_verify_local_assets_fails_loud_on_missing_file(tmp_path: Path):
    manifest, assets_dir, _ = _sample_manifest_and_assets(tmp_path)
    (assets_dir / manifest.artifacts[0].filename).unlink()
    with pytest.raises(pub.PublishError, match="missing on disk"):
        pub.verify_local_assets(manifest, assets_dir)


# ---------------------------------------------------------------------------
# GitHub planning (subprocess mocked)
# ---------------------------------------------------------------------------


class _FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_plan_github_uploads_skips_existing_assets(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)

    existing_names = {manifest.artifacts[0].filename}

    def fake_run(cmd, capture_output=True, text=True, check=False):
        assert cmd[:2] == ["gh", "release"]
        return _FakeCompletedProcess(
            returncode=0,
            stdout=json.dumps({"assets": [{"name": n} for n in existing_names]}),
        )

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    to_upload = pub.plan_github_uploads(manifest, assets_dir, manifest_path, "Lingtai-AI/lingtai-kernel", "v0.16.4")
    names = {p.name for p in to_upload}
    # The already-attached wheel is skipped; the sdist and manifest still need upload.
    assert manifest.artifacts[0].filename not in names
    assert manifest.artifacts[1].filename in names
    assert manifest_path.name in names


def test_gh_release_exists_true_false(monkeypatch):
    monkeypatch.setattr(pub.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=0))
    assert pub.gh_release_exists("v1", "o/r") is True
    monkeypatch.setattr(pub.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1))
    assert pub.gh_release_exists("v1", "o/r") is False


def test_execute_github_upload_requires_no_files_is_noop(monkeypatch):
    calls = []
    monkeypatch.setattr(pub.subprocess, "run", lambda *a, **k: calls.append(a) or _FakeCompletedProcess())
    pub.execute_github_upload("v1", "o/r", [])
    assert calls == []  # never shells out when there is nothing to upload


# ---------------------------------------------------------------------------
# Gitee planning (network mocked via _gitee_request)
# ---------------------------------------------------------------------------


def test_gitee_verify_tag_synchronized_passes_on_matching_commit(monkeypatch):
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda method, path, token, **kw: {"commit": {"sha": "a" * 40}},
    )
    pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")  # no raise


def test_gitee_verify_tag_synchronized_fails_loud_on_mismatched_commit(monkeypatch):
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda method, path, token, **kw: {"commit": {"sha": "b" * 40}},
    )
    with pytest.raises(pub.PublishError, match="mismatched"):
        pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")


def test_gitee_verify_tag_synchronized_fails_loud_when_tag_missing(monkeypatch):
    def raise_404(method, path, token, **kw):
        raise pub.PublishError("Gitee API GET /x failed: HTTP 404: b''")

    monkeypatch.setattr(pub, "_gitee_request", raise_404)
    with pytest.raises(pub.PublishError, match="synchronize"):
        pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")


def test_plan_gitee_uploads_skips_nothing_new(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    monkeypatch.setattr(pub, "_gitee_request", lambda *a, **k: {"attach_files": []})
    to_upload = pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")
    names = {p.name for p in to_upload}
    assert names == {manifest.artifacts[0].filename, manifest.artifacts[1].filename, manifest_path.name}


def test_plan_gitee_uploads_fails_loud_on_name_conflict(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    conflicting_name = manifest.artifacts[0].filename
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda *a, **k: {"attach_files": [{"id": 42, "name": conflicting_name, "browser_download_url": "https://x"}]},
    )
    with pytest.raises(pub.AssetConflict, match="does not delete-and-replace"):
        pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")


# ---------------------------------------------------------------------------
# CLI: dry-run is the default and never calls the mutating fakes
# ---------------------------------------------------------------------------


def test_main_dry_run_default_never_executes_github_upload(tmp_path, monkeypatch, capsys):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)

    def fake_run(cmd, capture_output=True, text=True, check=False):
        if cmd[:3] == ["gh", "release", "view"] and "--json" in cmd:
            return _FakeCompletedProcess(returncode=0, stdout=json.dumps({"assets": []}))
        if cmd[:3] == ["gh", "release", "view"]:
            return _FakeCompletedProcess(returncode=0)
        raise AssertionError(f"unexpected mutating subprocess call in dry-run: {cmd}")

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    rc = pub.main([
        "--manifest", str(manifest_path),
        "--assets-dir", str(assets_dir),
        "--github-repo", "Lingtai-AI/lingtai-kernel",
        "--skip-gitee",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out


def test_main_skips_gitee_when_token_env_unset(tmp_path, monkeypatch, capsys):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    monkeypatch.delenv("GITEE_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(pub.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1))
    rc = pub.main([
        "--manifest", str(manifest_path),
        "--assets-dir", str(assets_dir),
        "--skip-github",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[gitee] skipped" in out
    assert "GITEE_ACCESS_TOKEN is not set" in out


def test_main_never_echoes_gitee_token(tmp_path, monkeypatch, capsys):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    secret = "super-secret-token-value-12345"
    monkeypatch.setenv("GITEE_ACCESS_TOKEN", secret)
    monkeypatch.setattr(pub.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1))
    monkeypatch.setattr(pub, "_gitee_request", lambda *a, **k: {"commit": {"sha": "a" * 40}})
    monkeypatch.setattr(pub, "gitee_find_release_by_tag", lambda *a, **k: None)
    rc = pub.main([
        "--manifest", str(manifest_path),
        "--assets-dir", str(assets_dir),
        "--skip-github",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert secret not in out


def test_main_rejects_tampered_asset_before_any_upload(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    (assets_dir / manifest.artifacts[0].filename).write_bytes(b"tampered")

    def fail_if_called(*a, **k):
        raise AssertionError("must not shell out before local asset verification")

    monkeypatch.setattr(pub.subprocess, "run", fail_if_called)
    with pytest.raises(pub.PublishError, match="sha256 mismatch"):
        pub.main([
            "--manifest", str(manifest_path),
            "--assets-dir", str(assets_dir),
            "--skip-gitee",
        ])
