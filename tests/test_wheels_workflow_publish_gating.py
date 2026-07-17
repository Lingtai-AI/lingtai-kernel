"""Static assertions over .github/workflows/wheels.yml's release-manifest job:
proves the publish step is actually reachable with --execute on the real
release trigger (Blocker 3 repair), that manual dispatch defaults to dry-run,
that exact-tag recovery checks out and validates the selected tag, that GitHub
publication is authenticated without persisted checkout credentials, and that the
Gitee sync step never force-pushes. This is a workflow-shape
test, not a live GitHub Actions run — it parses the committed YAML/shell
text.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "wheels.yml"


def _load_workflow() -> dict:
    # PyYAML parses the bare `on:` key as boolean True under YAML 1.1
    # resolution; this file's real key is the string "on", so load with the
    # default resolver and look it up defensively under both spellings.
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _release_manifest_job(data: dict) -> dict:
    return data["jobs"]["release-manifest"]


def _step(job: dict, name_substring: str) -> dict:
    for step in job["steps"]:
        if name_substring.lower() in step.get("name", "").lower():
            return step
    raise AssertionError(f"no step with name containing {name_substring!r} in {[s.get('name') for s in job['steps']]}")


def test_workflow_dispatch_has_publish_input_defaulting_false():
    data = _load_workflow()
    on = data.get("on") or data.get(True)  # YAML 1.1 quirk guard
    dispatch = on["workflow_dispatch"]
    assert "inputs" in dispatch, "workflow_dispatch must expose a manual-publish input"
    publish_input = dispatch["inputs"]["publish"]
    assert publish_input["type"] == "boolean"
    assert publish_input["default"] is False, "manual dispatch must default to dry-run"


def test_release_published_still_triggers_workflow():
    data = _load_workflow()
    on = data.get("on") or data.get(True)
    assert on["release"]["types"] == ["published"]


def test_publish_mode_step_executes_on_real_release_event():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "Determine publish mode")
    script = step["run"]
    assert "github.event_name" in script and "release" in script
    assert 'echo "execute=1"' in script, "the release event branch must set execute=1"


def test_publish_mode_step_executes_on_explicit_manual_publish_true():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "Determine publish mode")
    script = step["run"]
    assert "inputs.publish" in script
    # Both branches (release event, and workflow_dispatch+publish=true) must
    # set execute=1; the else branch must set execute=0 (default dry-run).
    assert script.count('echo "execute=1"') == 2, script
    assert 'echo "execute=0"' in script


def test_publish_step_conditionally_passes_execute_flag():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "Publish manifest")
    script = step["run"]
    assert "publish_mode.outputs.execute" in script
    assert "--execute" in script
    assert "publish_release_assets.py" in script
    # The flag must be conditional, not unconditionally omitted (the prior
    # defect) or unconditionally present (which would always try to publish,
    # even on ordinary workflow_dispatch shape-checks).
    assert 'execute_flag="--execute"' in script
    assert 'execute_flag=""' in script


def test_gitee_sync_step_is_gated_on_publish_mode_and_never_forces():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "synchronize the exact commit/tag to Gitee")
    assert step.get("if") == "steps.publish_mode.outputs.execute == '1'", \
        "Gitee sync must only run when actually publishing, not on every dry-run shape check"
    script = step["run"]
    assert "sync_gitee_mirror.py" in script
    assert "--execute" in script
    assert "--force" not in script
    assert "force" not in script.lower() or "non-force" in script.lower()


def test_gitee_token_never_echoed_via_echo_or_print():
    # The env var name legitimately appears in comments/docs and in the
    # `env:` mapping (sourced from the GitHub secret). What must never happen
    # is a shell `echo`/`print`/`cat` of that variable, which would leak the
    # value into the job log.
    text = WORKFLOW_PATH.read_text()
    for line in text.splitlines():
        stripped = line.strip()
        if "$GITEE_ACCESS_TOKEN" in stripped or "${GITEE_ACCESS_TOKEN" in stripped:
            assert not any(stripped.startswith(cmd) for cmd in ("echo", "print", "cat")), (
                f"found a shell command that appears to print the raw token: {line}"
            )


def test_release_manifest_job_has_contents_write_for_publish():
    job = _release_manifest_job(_load_workflow())
    assert job["permissions"]["contents"] == "write", (
        "publishing (gh release upload / git push to Gitee) requires contents:write; "
        "read-only permissions would make the publish step fail even when execute=1"
    )


def test_checkout_step_has_full_history_for_gitee_push():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "Check out repository")
    assert step.get("with", {}).get("fetch-depth") == 0, (
        "sync_gitee_mirror.py needs the release commit's full history reachable "
        "to push it; a shallow checkout would break the non-force push"
    )


def test_release_manifest_job_installs_declared_packaging_dependency():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "Install manifest runtime dependencies")
    script = step["run"]
    assert "python -m pip install" in script
    assert "packaging>=24.0" in script



def test_workflow_dispatch_exact_tag_recovery_input_defaults_empty():
    data = _load_workflow()
    on = data.get("on") or data.get(True)
    recovery = on["workflow_dispatch"]["inputs"]["release_tag"]
    assert recovery["type"] == "string"
    assert recovery["default"] == ""


def test_all_release_jobs_checkout_the_optional_exact_recovery_tag():
    data = _load_workflow()
    expected = "${{ inputs.release_tag && format('refs/tags/{0}', inputs.release_tag) || github.ref }}"
    for job_name in ("build-wheels", "build-sdist", "release-manifest"):
        step = _step(data["jobs"][job_name], "Check out repository")
        assert step.get("with", {}).get("ref") == expected, job_name
        assert step.get("with", {}).get("persist-credentials") is False, job_name


def test_manifest_and_gitee_sync_use_the_checked_out_commit_and_recovery_tag():
    job = _release_manifest_job(_load_workflow())
    manifest = _step(job, "Generate release manifest")
    sync = _step(job, "synchronize the exact commit/tag to Gitee")
    publish = _step(job, "Publish manifest")
    assert "inputs.release_tag" in manifest["env"]["KERNEL_TAG"]
    for step in (manifest, sync):
        assert "git rev-parse HEAD" in step["run"]
        assert "github.sha" not in step["run"]
    assert "inputs.release_tag" in sync["run"]
    assert "inputs.release_tag" in publish["run"]


def test_publish_step_receives_github_cli_token_without_printing_it():
    job = _release_manifest_job(_load_workflow())
    step = _step(job, "Publish manifest")
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "$GH_TOKEN" not in step["run"]


def test_manual_publish_requires_and_validates_an_exact_semver_tag():
    data = _load_workflow()
    for job_name in ("build-wheels", "build-sdist", "release-manifest"):
        step = _step(data["jobs"][job_name], "Validate manual recovery tag")
        assert step.get("if") == "github.event_name == 'workflow_dispatch'"
        assert step.get("shell") == "bash"
        assert "inputs.publish" in step["env"]["PUBLISH_REQUESTED"]
        assert "inputs.release_tag" in step["env"]["RELEASE_TAG"]
        script = step["run"]
        assert '"$PUBLISH_REQUESTED" == "true" && -z "$RELEASE_TAG"' in script
        assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in script
        assert 'refs/tags/${RELEASE_TAG}^{commit}' in script
        assert "git rev-parse HEAD" in script
        assert '"$tag_commit" != "$head_commit"' in script

    publish_mode = _step(_release_manifest_job(data), "Determine publish mode")["run"]
    assert "inputs.release_tag" in publish_mode
    assert "Manual publishing requires release_tag" in publish_mode
