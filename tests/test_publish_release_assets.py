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
    (assets_dir / "SHA256SUMS").write_text(
        "\n".join(f"{a.sha256}  {a.filename}" for a in manifest.artifacts) + "\n",
        encoding="utf-8",
    )
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


def test_plan_github_uploads_same_byte_collision_skips_without_upload(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)

    existing_names = {manifest.artifacts[0].filename}
    calls = []

    def fake_run(cmd, capture_output=True, text=True, check=False):
        calls.append(cmd)
        if cmd[:2] == ["gh", "release"] and cmd[2] == "view":
            return _FakeCompletedProcess(
                returncode=0,
                stdout=json.dumps({"assets": [{"name": n, "apiUrl": "https://api.github.test/assets/1"} for n in existing_names]}),
            )
        if cmd[:2] == ["gh", "api"]:
            output = Path(cmd[cmd.index("--output") + 1])
            output.write_bytes((assets_dir / manifest.artifacts[0].filename).read_bytes())
            return _FakeCompletedProcess()
        raise AssertionError(cmd)

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    to_upload = pub.plan_github_uploads(manifest, assets_dir, manifest_path, "Lingtai-AI/lingtai-kernel", "v0.16.4")
    names = {p.name for p in to_upload}
    # The already-attached wheel is skipped; the sdist and manifest still need upload.
    assert manifest.artifacts[0].filename not in names
    assert manifest.artifacts[1].filename in names
    assert manifest_path.name in names
    assert "SHA256SUMS" in names
    assert not any(cmd[2] == "upload" for cmd in calls)


def test_plan_github_uploads_mismatch_fails_before_upload(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["gh", "release"]:
            return _FakeCompletedProcess(stdout=json.dumps({"assets": [{"name": manifest.artifacts[0].filename, "apiUrl": "https://api.github.test/assets/1"}]}))
        output = Path(cmd[cmd.index("--output") + 1])
        output.write_bytes(b"different")
        return _FakeCompletedProcess()
    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    with pytest.raises(pub.AssetConflict, match="GitHub asset"):
        pub.plan_github_uploads(manifest, assets_dir, manifest_path, "o/r", "v1")


def test_plan_github_uploads_missing_url_fails_loud(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    monkeypatch.setattr(pub.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(
        stdout=json.dumps({"assets": [{"name": manifest.artifacts[0].filename}]})
    ))
    with pytest.raises(pub.PublishError, match="no usable download URL"):
        pub.plan_github_uploads(manifest, assets_dir, manifest_path, "o/r", "v1")


def test_plan_github_uploads_download_failure_fails_loud(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["gh", "release"]:
            return _FakeCompletedProcess(stdout=json.dumps({"assets": [{"name": manifest.artifacts[0].filename, "browserDownloadUrl": "https://github.test/download"}]}))
        return _FakeCompletedProcess(returncode=1)
    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    with pytest.raises(pub.PublishError, match="download failed"):
        pub.plan_github_uploads(manifest, assets_dir, manifest_path, "o/r", "v1")


@pytest.mark.parametrize("stdout", ["{not-json", json.dumps({"release": "v1"})])
def test_main_github_asset_metadata_failure_never_uploads_or_mutates(tmp_path, monkeypatch, stdout):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "release", "view"] and "--json" not in cmd:
            return _FakeCompletedProcess(returncode=0)
        if cmd[:3] == ["gh", "release", "view"] and "--json" in cmd:
            return _FakeCompletedProcess(returncode=1 if stdout == "{not-json" else 0, stdout=stdout)
        raise AssertionError(f"unexpected mutating command: {cmd}")

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    with pytest.raises(pub.PublishError, match="GitHub release asset metadata"):
        pub.main([
            "--manifest", str(manifest_path),
            "--assets-dir", str(assets_dir),
            "--github-repo", "o/r",
            "--skip-gitee",
            "--execute",
        ])
    assert not any(cmd[:3] == ["gh", "release", "upload"] for cmd in calls)
    assert not any(cmd[:3] == ["gh", "release", "create"] for cmd in calls)


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


def test_gitee_create_release_includes_target_commitish(monkeypatch):
    captured = {}

    def fake_request(method, path, token, **kwargs):
        captured.update(method=method, path=path, token=token, **kwargs)
        return {"id": 1}

    monkeypatch.setattr(pub, "_gitee_request", fake_request)
    pub.gitee_create_release("owner", "repo", "v0.16.4", "v0.16.4", "a" * 40, "tok")
    payload = json.loads(captured["data"])
    assert payload["target_commitish"] == "a" * 40
    assert payload["tag_name"] == "v0.16.4"


def test_gitee_verify_tag_synchronized_passes_on_matching_commit(monkeypatch):
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda method, path, token, **kw: [
            {"name": "v0.16.4", "commit": {"sha": "a" * 40}},
        ],
    )
    pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")  # no raise


def test_gitee_verify_tag_synchronized_follows_tag_list_pagination(monkeypatch):
    paths = []

    def fake_request(method, path, token, **kw):
        paths.append(path)
        if path.endswith("page=1"):
            return [
                {"name": f"v0.0.{index}", "commit": {"sha": "b" * 40}}
                for index in range(100)
            ]
        return [{"name": "v0.16.4", "commit": {"sha": "a" * 40}}]

    monkeypatch.setattr(pub, "_gitee_request", fake_request)
    pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")
    assert paths == [
        "/repos/owner/repo/tags?per_page=100&page=1",
        "/repos/owner/repo/tags?per_page=100&page=2",
    ]


def test_gitee_verify_tag_synchronized_fails_loud_on_mismatched_commit(monkeypatch):
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda method, path, token, **kw: [
            {"name": "v0.16.4", "commit": {"sha": "b" * 40}},
        ],
    )
    with pytest.raises(pub.PublishError, match="mismatched"):
        pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")


def test_gitee_verify_tag_synchronized_fails_loud_when_tag_missing(monkeypatch):
    monkeypatch.setattr(pub, "_gitee_request", lambda *args, **kwargs: [])
    with pytest.raises(pub.PublishError, match="not found"):
        pub.gitee_verify_tag_synchronized("owner", "repo", "v0.16.4", "a" * 40, "tok")


def test_plan_gitee_uploads_missing_assets_are_planned(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    monkeypatch.setattr(pub, "_gitee_request", lambda *a, **k: {"attach_files": []})
    to_upload = pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")
    names = {p.name for p in to_upload}
    assert names == {manifest.artifacts[0].filename, manifest.artifacts[1].filename, manifest_path.name, "SHA256SUMS"}


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, size=-1):
        body, self.body = self.body, b""
        return body


def test_plan_gitee_uploads_same_byte_collision_skips_without_upload(tmp_path, monkeypatch, capsys):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    wheel = manifest.artifacts[0].filename
    timeouts = []
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda *a, **k: {"attach_files": [{"id": 42, "name": wheel, "browserDownloadUrl": "https://gitee.test/wheel"}]},
    )

    def fake_urlopen(request, timeout):
        timeouts.append(timeout)
        return _FakeResponse((assets_dir / wheel).read_bytes())

    monkeypatch.setattr(pub.urllib.request, "urlopen", fake_urlopen)
    to_upload = pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "secret-token")
    assert wheel not in {p.name for p in to_upload}
    assert timeouts == [pub.GITEE_TRANSFER_TIMEOUT_SECONDS]
    assert "existing bytes sha256" in capsys.readouterr().out


def test_plan_gitee_uploads_reads_actual_assets_schema(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    wheel = manifest.artifacts[0].filename
    monkeypatch.setattr(
        pub,
        "_gitee_request",
        lambda *a, **k: {"assets": [{"name": wheel, "browser_download_url": "https://gitee.test/wheel"}]},
    )
    monkeypatch.setattr(pub.urllib.request, "urlopen", lambda *a, **k: _FakeResponse((assets_dir / wheel).read_bytes()))
    to_upload = pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")
    assert wheel not in {path.name for path in to_upload}


def test_plan_gitee_uploads_groups_same_url_duplicates_warns_and_hash_skips_canonical_url(
    tmp_path, monkeypatch, recwarn
):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    wheel = manifest.artifacts[0].filename
    canonical_url = "https://gitee.test/attachments/42"
    monkeypatch.setattr(
        pub,
        "_gitee_request",
        lambda *a, **k: {
            "assets": [
                {"id": 42, "name": wheel, "browserDownloadUrl": canonical_url},
                {"id": 43, "name": wheel, "browser_download_url": canonical_url},
            ]
        },
    )
    downloaded = []

    def fake_urlopen(request, timeout):
        downloaded.append(request.full_url.split("?", 1)[0])
        return _FakeResponse((assets_dir / wheel).read_bytes())

    monkeypatch.setattr(pub.urllib.request, "urlopen", fake_urlopen)
    to_upload = pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")
    assert wheel not in {path.name for path in to_upload}
    assert downloaded == [canonical_url]
    assert len(recwarn) == 1
    assert "duplicate attachment" in str(recwarn[0].message)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {"name": "TARGET", "browser_download_url": "https://gitee.test/1"},
                {"name": "TARGET", "browser_download_url": "https://gitee.test/2"},
            ],
            "divergent download URLs",
        ),
        (
            [
                {"name": "TARGET", "browser_download_url": "https://gitee.test/1"},
                {"name": "TARGET"},
            ],
            "no usable download URL",
        ),
        (
            [
                {"name": "TARGET", "browser_download_url": "https://gitee.test/1"},
                {"name": "TARGET", "browser_download_url": "http://gitee.test/1"},
            ],
            "no usable download URL",
        ),
        (
            [
                {
                    "name": "TARGET",
                    "browserDownloadUrl": "https://gitee.test/1",
                    "browser_download_url": "https://gitee.test/2",
                },
                {"name": "TARGET", "browser_download_url": "https://gitee.test/1"},
            ],
            "ambiguous download URLs",
        ),
    ],
)
def test_plan_gitee_uploads_duplicate_metadata_fails_closed(tmp_path, monkeypatch, rows, message):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    rows = [{**row, "name": manifest.artifacts[0].filename} for row in rows]
    monkeypatch.setattr(pub, "_gitee_request", lambda *a, **k: {"assets": rows})
    with pytest.raises(pub.PublishError, match=message):
        pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")


def test_plan_gitee_uploads_mismatch_fails_before_upload(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    conflicting_name = manifest.artifacts[0].filename
    monkeypatch.setattr(
        pub, "_gitee_request",
        lambda *a, **k: {"attach_files": [{"id": 42, "name": conflicting_name, "browser_download_url": "https://x"}]},
    )
    monkeypatch.setattr(pub.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"different"))
    with pytest.raises(pub.AssetConflict, match="Gitee attachment"):
        pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")


def test_plan_gitee_uploads_missing_url_fails_loud(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    monkeypatch.setattr(pub, "_gitee_request", lambda *a, **k: {"attach_files": [{"name": manifest.artifacts[0].filename}]})
    with pytest.raises(pub.PublishError, match="no usable download URL"):
        pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")


def test_plan_gitee_uploads_download_failure_fails_loud(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    monkeypatch.setattr(pub, "_gitee_request", lambda *a, **k: {"attach_files": [{"name": manifest.artifacts[0].filename, "download_url": "https://gitee.test/download"}]})
    def fail_download(*args, **kwargs):
        raise pub.urllib.error.URLError("offline")
    monkeypatch.setattr(pub.urllib.request, "urlopen", fail_download)
    with pytest.raises(pub.PublishError, match="download/hash failed"):
        pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")


def test_plan_gitee_uploads_duplicate_name_missing_url_fails_loud(tmp_path, monkeypatch):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    name = manifest.artifacts[0].filename
    monkeypatch.setattr(
        pub,
        "_gitee_request",
        lambda *a, **k: {"attach_files": [{"name": name}, {"name": name, "url": "https://gitee.test/wheel"}]},
    )
    with pytest.raises(pub.PublishError, match="no usable download URL"):
        pub.plan_gitee_uploads(manifest, assets_dir, manifest_path, "owner", "repo", 1, "tok")


def test_execute_gitee_upload_uses_transfer_timeout(tmp_path, monkeypatch):
    asset = tmp_path / "large.whl"
    asset.write_bytes(b"wheel-bytes")
    requests = []

    def fake_request(method, path, token, **kwargs):
        requests.append((method, path, token, kwargs))
        return {}

    monkeypatch.setattr(pub, "_gitee_request", fake_request)
    pub.execute_gitee_upload("owner", "repo", 42, [asset], "secret-token")

    assert len(requests) == 1
    method, path, token, kwargs = requests[0]
    assert method == "POST"
    assert path == "/repos/owner/repo/releases/42/attach_files"
    assert token == "secret-token"
    assert kwargs["timeout"] == pub.GITEE_TRANSFER_TIMEOUT_SECONDS


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


def test_main_dry_run_missing_github_release_does_not_plan_assets(tmp_path, monkeypatch, capsys):
    manifest, assets_dir, manifest_path = _sample_manifest_and_assets(tmp_path)
    calls = []

    def fake_run(cmd, capture_output=True, text=True, check=False):
        calls.append(cmd)
        if cmd[:3] == ["gh", "release", "view"] and "--json" not in cmd:
            return _FakeCompletedProcess(returncode=1)
        raise AssertionError(f"unexpected command before dry-run release exists: {cmd}")

    monkeypatch.setattr(pub.subprocess, "run", fake_run)
    rc = pub.main([
        "--manifest", str(manifest_path),
        "--assets-dir", str(assets_dir),
        "--github-repo", "Lingtai-AI/lingtai-kernel",
        "--skip-gitee",
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert "does not exist yet" in out
    assert "DRY RUN: would create release" in out
    assert "cannot plan attachment uploads without a release" in out
    assert not any(cmd[:3] == ["gh", "release", "create"] for cmd in calls)
    assert not any(cmd[:3] == ["gh", "release", "upload"] for cmd in calls)
    assert not any("--json" in cmd for cmd in calls)


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
    monkeypatch.setattr(
        pub,
        "_gitee_request",
        lambda *a, **k: [{"name": "v0.16.4", "commit": {"sha": "a" * 40}}],
    )
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


def test_publisher_has_no_delete_replace_or_github_mutation_path():
    text = Path(pub.__file__).read_text(encoding="utf-8")
    assert "gh release delete" not in text
    assert "gh release replace" not in text
    assert "gitee_delete" not in text
    assert "gitee_replace" not in text
