"""Static contract tests for the prerelease-only wheel workflow."""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "wheels.yml"


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _triggers(data: dict) -> dict:
    # PyYAML resolves bare `on:` as boolean True under YAML 1.1.
    return data.get("on") or data.get(True)


def _release_manifest_job(data: dict) -> dict:
    return data["jobs"]["release-manifest"]


def test_workflow_is_manual_prerelease_only():
    data = _load_workflow()
    assert set(_triggers(data)) == {"workflow_dispatch"}
    assert data["permissions"] == {"contents": "read"}


def test_release_manifest_job_cannot_publish():
    job = _release_manifest_job(_load_workflow())
    assert set(job["needs"]) == {"build-wheels", "build-sdist"}
    assert job["permissions"] == {"contents": "read"}
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "publish_release_assets.py" not in text
    assert "sync_gitee_mirror.py" not in text
    assert "--execute" not in text


def test_release_manifest_job_uploads_one_complete_bundle():
    job = _release_manifest_job(_load_workflow())
    upload = next(
        step for step in job["steps"]
        if step.get("uses") == "actions/upload-artifact@v4"
    )
    assert upload["with"] == {
        "name": "release-bundle",
        "path": "release-assets",
        "if-no-files-found": "error",
    }
