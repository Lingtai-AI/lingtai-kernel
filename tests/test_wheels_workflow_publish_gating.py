"""Static assertions over .github/workflows/wheels.yml's release-manifest job:
proves the publish step is actually reachable with --execute on the real
release trigger (Blocker 3 repair), that manual dispatch defaults to dry-run,
and that the Gitee sync step never force-pushes. This is a workflow-shape
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
