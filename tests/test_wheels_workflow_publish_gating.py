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

import json
import os
from pathlib import Path
import subprocess
import sys

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


def _recovery_job(data: dict) -> dict:
    return data["jobs"]["gitee-only-recovery"]


def test_gitee_only_recovery_input_has_boolean_default_and_safety_description():
    data = _load_workflow()
    on = data.get("on") or data.get(True)
    recovery = on["workflow_dispatch"]["inputs"]["gitee_only_recovery"]
    assert recovery["type"] == "boolean"
    assert recovery["default"] is False
    assert "Gitee-only recovery" in recovery["description"]
    assert "publish=true" in recovery["description"]
    assert "No builds" in recovery["description"]


def test_gitee_only_gate_is_on_exactly_the_three_existing_jobs():
    data = _load_workflow()
    existing = ("build-wheels", "build-sdist", "release-manifest")
    assert all(name in data["jobs"] for name in existing)
    assert all(data["jobs"][name].get("if") == "inputs.gitee_only_recovery != true" for name in existing)
    assert [name for name, job in data["jobs"].items() if job.get("if") == "inputs.gitee_only_recovery != true"] == list(existing)


def test_gitee_only_recovery_job_is_ubuntu_bounded_and_read_only():
    job = _recovery_job(_load_workflow())
    assert job["if"] == "github.event_name == 'workflow_dispatch' && inputs.gitee_only_recovery == true"
    assert job["runs-on"] == "ubuntu-latest"
    assert job["timeout-minutes"] == 120
    assert job["permissions"] == {"contents": "read"}


def test_gitee_only_recovery_checks_out_exact_tag_with_no_persisted_credentials():
    job = _recovery_job(_load_workflow())
    checkout = _step(job, "Check out exact release tag")
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"] == {
        "ref": "refs/tags/${{ inputs.release_tag }}",
        "persist-credentials": False,
        "fetch-depth": 0,
    }
    validation = _step(job, "Validate Gitee-only recovery inputs and checkout")
    assert validation["shell"] == "bash"
    assert validation["env"] == {
        "RECOVERY_REQUESTED": "${{ inputs.gitee_only_recovery }}",
        "PUBLISH_REQUESTED": "${{ inputs.publish }}",
        "RELEASE_TAG": "${{ inputs.release_tag }}",
    }
    script = validation["run"]
    assert '"$RECOVERY_REQUESTED" != "true"' in script
    assert '"$PUBLISH_REQUESTED" != "true"' in script
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in script
    assert 'refs/tags/${RELEASE_TAG}^{commit}' in script
    assert "git rev-parse HEAD" in script
    assert '"$tag_commit" != "$head_commit"' in script


def test_gitee_only_download_scopes_gh_token_and_uses_exact_release_tag():
    job = _recovery_job(_load_workflow())
    download = _step(job, "Download exact GitHub release assets")
    assert download["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "RELEASE_TAG": "${{ inputs.release_tag }}",
    }
    script = download["run"]
    assert 'if [[ -e release-assets ]]' in script
    assert "mkdir release-assets" in script
    assert 'gh release download "$RELEASE_TAG"' in script
    assert '--repo "$GITHUB_REPOSITORY"' in script
    assert "--dir release-assets" in script
    token_steps = [step for step in job["steps"] if "GH_TOKEN" in step.get("env", {})]
    assert token_steps == [download]


def test_gitee_only_publisher_requires_token_without_echoing_it():
    job = _recovery_job(_load_workflow())
    publisher = _step(job, "Resume Gitee publication")
    assert publisher["env"] == {"GITEE_ACCESS_TOKEN": "${{ secrets.GITEE_ACCESS_TOKEN }}"}
    script = publisher["run"]
    assert '[[ -z "${GITEE_ACCESS_TOKEN:-}" ]]' in script
    assert "must be configured" in script
    token_steps = [step for step in job["steps"] if "GITEE_ACCESS_TOKEN" in step.get("env", {})]
    assert token_steps == [publisher]
    for line in script.splitlines():
        stripped = line.strip()
        if "$GITEE_ACCESS_TOKEN" in stripped or "${GITEE_ACCESS_TOKEN" in stripped:
            assert not stripped.startswith(("echo", "print", "cat")), line


def test_gitee_only_publisher_is_unbuffered_execute_and_github_skipping():
    script = _step(_recovery_job(_load_workflow()), "Resume Gitee publication")["run"]
    assert "PYTHONUNBUFFERED=1" in script
    assert "scripts/publish_release_assets.py" in script
    assert "--skip-github" in script
    assert "--execute" in script


def test_gitee_only_retry_budget_and_timeout_codes_are_bounded():
    script = _step(_recovery_job(_load_workflow()), "Resume Gitee publication")["run"]
    assert "max_attempts=10" in script
    assert "10m" in script
    assert "--kill-after=30s" in script
    assert "status == 124 || status == 137" in script
    assert 'exit "$status"' in script
    assert "failed with non-timeout exit" in script
    assert 'exit 124' in script


def test_gitee_only_recovery_does_not_build_sync_or_publish_to_github():
    job = _recovery_job(_load_workflow())
    recovery_text = "\\n".join(
        [step.get("uses", "") + "\\n" + step.get("run", "") for step in job["steps"]]
    )
    for forbidden in (
        "cibuildwheel",
        "uv build",
        "actions/upload-artifact",
        "actions/download-artifact",
        "sync_gitee_mirror.py",
        "gh release upload",
        "gh release create",
    ):
        assert forbidden not in recovery_text
    assert "gh release download" in recovery_text


def test_gitee_only_downloaded_manifest_is_bound_to_requested_tag_and_checkout(tmp_path):
    job = _recovery_job(_load_workflow())
    steps = job["steps"]
    names = [step.get("name", "") for step in steps]
    download_index = names.index("Download exact GitHub release assets")
    verify_index = names.index("Verify downloaded manifest matches requested release")
    publish_index = names.index("Resume Gitee publication (idempotent bounded retry)")
    assert download_index < verify_index < publish_index

    verifier = steps[verify_index]
    assert verifier["shell"] == "bash"
    assert verifier["env"] == {"RELEASE_TAG": "${{ inputs.release_tag }}"}
    shell_script = verifier["run"]
    assert 'export EXPECTED_COMMIT="$(git rev-parse HEAD)"' in shell_script
    assert "data.get(\"kernel_tag\")" in shell_script
    assert "data.get(\"commit\")" in shell_script
    marker = "python - <<'PY'\n"
    assert marker in shell_script and "\nPY" in shell_script
    verifier_code = shell_script.split(marker, 1)[1].rsplit("\nPY", 1)[0]

    assets = tmp_path / "release-assets"
    assets.mkdir()
    manifest_path = assets / "lingtai-kernel-release-manifest.json"
    expected_tag = "v0.17.1"
    expected_commit = "a" * 40
    env = {
        **os.environ,
        "RELEASE_TAG": expected_tag,
        "EXPECTED_COMMIT": expected_commit,
    }

    def run_verifier(manifest: dict) -> subprocess.CompletedProcess[str]:
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return subprocess.run(
            [sys.executable, "-c", verifier_code],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    matching = run_verifier({"kernel_tag": expected_tag, "commit": expected_commit.upper()})
    assert matching.returncode == 0, matching.stderr
    assert "target verified" in matching.stdout

    wrong_tag = run_verifier({"kernel_tag": "v0.17.0", "commit": expected_commit})
    assert wrong_tag.returncode != 0
    assert "kernel_tag" in wrong_tag.stderr and "requested release tag" in wrong_tag.stderr

    wrong_commit = run_verifier({"kernel_tag": expected_tag, "commit": "b" * 40})
    assert wrong_commit.returncode != 0
    assert "manifest commit" in wrong_commit.stderr and "checked-out commit" in wrong_commit.stderr
