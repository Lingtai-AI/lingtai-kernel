"""Authored conformance for hidden creation; execution is acceptance-owner gated."""
from __future__ import annotations

import copy
import io
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from lingtai.adapters.project_creation_exchange import (
    JsonCreationPrerequisiteAdapter, decode_creation_request,
)
from lingtai.adapters.project_creation_recipe import FilesystemCreationRecipeAdapter
from lingtai.adapters.project_interactive_workspace import (
    FilesystemInteractiveWorkspaceAdapter, _exclusive_rename,
)
from lingtai.kernel.project import ProjectCreationError
from lingtai.kernel.project.interactive_creation import (
    CreationAttempt, CreationPrerequisitePort,
    InteractiveCreateRequest, InteractiveCreationUseCase, InteractiveOptions,
    ProcessEvidence,
)
from lingtai.tools.psyche.settings import read_resolved_prompt_inputs, serialize_prompt_owner_document


def request(root: Path, *, recipe: str = "adaptive") -> InteractiveCreateRequest:
    return InteractiveCreateRequest(
        str(root), str(root), "Display name", "orch-dir", str(root / "selected.json"),
        {"llm": {"provider": "openai", "model": "test-model", "context_limit": 8192,
                 "api_key_env": "OPENAI_API_KEY"},
         "capabilities": {"shell": {"yolo": False}, "skills": {"paths": ["../.library_shared"]}}},
        # Non-secret path only: reader validation does not load credentials.
        InteractiveOptions("zh", "approved covenant", env_file=str(root / "task.env")),
        recipe, ProcessEvidence.CLEAN,
    )


def persist_selected(req: InteractiveCreateRequest) -> None:
    Path(req.preset_ref).write_text(json.dumps({
        "description": {"summary": "test selection"}, "manifest": req.preset_manifest,
    }), encoding="utf-8")


class Prerequisites(CreationPrerequisitePort):
    def __init__(self, callback=lambda _attempt, _ref: True):
        self.callback = callback
        self.calls = 0

    def persist_and_verify(self, attempt, preset_ref):
        self.calls += 1
        return self.callback(attempt, preset_ref)


def filesystem(root: Path, **kwargs):
    from lingtai.agent import load_preset
    return FilesystemInteractiveWorkspaceAdapter(
        root, load_preset=load_preset, serialize_psyche=serialize_prompt_owner_document, **kwargs,
    )


def test_creation_contract_hidden_until_valid_prerequisites(tmp_path):
    req = request(tmp_path)
    workspace = filesystem(tmp_path)
    recipes = FilesystemCreationRecipeAdapter(workspace)
    def prerequisite(attempt, ref):
        assert not (tmp_path / ".lingtai").exists()
        stage = workspace.owned_stage(attempt)
        assert json.loads((stage / "human" / ".agent.json").read_text())["admin"] is None
        assert json.loads((stage / "orch-dir" / ".agent.json").read_text())["admin"]["karma"] is True
        assert not Path(ref).exists()  # pending snapshot did not fabricate existence
        assert "comment_file" not in (stage / "orch-dir" / "init.json").read_text()
        persist_selected(req)
        return True
    prerequisites = Prerequisites(prerequisite)
    result = InteractiveCreationUseCase(workspace, recipes, prerequisites).create(req)
    assert result.to_payload()["committed"] is True
    assert result.directory_name == "orch-dir" and result.agent_name == "Display name"
    assert prerequisites.calls == 1
    assert not list(tmp_path.glob(".lingtai.create-*"))
    final = tmp_path / ".lingtai"
    assert (final / ".library_shared").is_dir()
    init = json.loads((final / "orch-dir" / "init.json").read_text())
    assert "context_limit" not in init["manifest"]["llm"]
    assert "context_limit" not in init["manifest"]
    assert init["manifest"]["preset"]["allowed"] == [req.preset_ref]
    assert init["env_file"] == req.options.env_file
    assert init["pad"] == (tmp_path / ".recipe" / "comment" / "zh" / "comment.md").read_text()
    assert "---\nrelated_files:" not in init["pad"]
    assert read_resolved_prompt_inputs(final / "orch-dir").covenant == "approved covenant"
    assert "{{lang}}" not in (final / "orch-dir" / ".prompt").read_text()
    assert (final / ".tui-asset" / ".recipe" / "recipe.json").is_file()


@pytest.mark.parametrize("evidence", [ProcessEvidence.BLOCKED, ProcessEvidence.UNKNOWN])
def test_unresolved_process_evidence_refuses_before_any_port_write(tmp_path, evidence):
    workspace = filesystem(tmp_path)
    prerequisites = Prerequisites()
    with pytest.raises(ProjectCreationError) as error:
        InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), prerequisites).create(
            replace(request(tmp_path), process_evidence=evidence))
    assert error.value.error.code == "project_process_state_unresolved"
    assert prerequisites.calls == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("ack", ["reject", "missing", "mismatch"])
def test_prerequisites_rejection_missing_or_changed_preset_never_publish(tmp_path, ack):
    req = request(tmp_path)
    def callback(*_):
        if ack == "reject":
            return False
        if ack == "mismatch":
            persist_selected(replace(req, preset_manifest={"llm": {"provider": "openai", "model": "other"}}))
        return True
    workspace = filesystem(tmp_path)
    with pytest.raises(ProjectCreationError):
        InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), Prerequisites(callback)).create(req)
    assert not (tmp_path / ".lingtai").exists()
    assert not list(tmp_path.glob(".lingtai.create-*"))


@pytest.mark.parametrize("target_kind", ["directory", "file", "symlink"])
def test_existing_final_is_never_touched(tmp_path, target_kind):
    final = tmp_path / ".lingtai"
    if target_kind == "directory":
        final.mkdir()
    elif target_kind == "file":
        final.write_text("foreign")
    else:
        final.symlink_to(tmp_path / "missing")
    workspace = filesystem(tmp_path)
    prerequisites = Prerequisites()
    with pytest.raises(ProjectCreationError) as error:
        InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), prerequisites).create(request(tmp_path))
    assert error.value.error.code == "already_initialized"
    assert not list(tmp_path.glob(".lingtai.create-*"))
    assert prerequisites.calls == 0


def test_concurrent_empty_final_is_not_replaced_and_dependencies_survive(tmp_path):
    req = request(tmp_path)
    def prerequisite(*_):
        persist_selected(req)
        (tmp_path / ".lingtai").mkdir()
        return True
    workspace = filesystem(tmp_path)
    with pytest.raises(ProjectCreationError):
        InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), Prerequisites(prerequisite)).create(req)
    assert list((tmp_path / ".lingtai").iterdir()) == []
    assert Path(req.preset_ref).exists()
    assert not list(tmp_path.glob(".lingtai.create-*"))


def test_cleanup_requires_actual_owned_marker_and_does_not_sweep_foreign_staging(tmp_path):
    req = request(tmp_path)
    foreign = tmp_path / ".lingtai.create-foreign"
    foreign.mkdir()
    (foreign / "valuable").write_text("retain")
    workspace = filesystem(tmp_path)
    def prerequisite(attempt, _ref):
        (workspace.owned_stage(attempt) / ".lingtai-launcher-staging").write_text("foreign nonce")
        return False
    with pytest.raises(ProjectCreationError) as error:
        InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), Prerequisites(prerequisite)).create(req)
    assert error.value.error.code == "owned_staging_cleanup_unresolved"
    assert (foreign / "valuable").read_text() == "retain"
    assert len(list(tmp_path.glob(".lingtai.create-*"))) == 2
    assert not (tmp_path / ".lingtai").exists()


def test_valid_local_recipe_locale_library_and_optional_layer_precedence(tmp_path):
    req = request(tmp_path)
    recipe = tmp_path / ".recipe"
    (recipe / "comment").mkdir(parents=True)
    (recipe / "greet").mkdir()
    (recipe / "covenant" / "zh").mkdir(parents=True)
    (recipe / "recipe.json").write_text('{"id":"local","name":"Local","library_name":"library"}')
    (recipe / "comment" / "comment.md").write_text("root-local Pad")
    (recipe / "greet" / "greet.md").write_text("{{addr}} {{lang}} {{location}} {{commands}} {{soul_delay}}")
    (recipe / "covenant" / "zh" / "covenant.md").write_text("localized covenant")
    before = {str(p.relative_to(recipe)): p.read_bytes() for p in recipe.rglob("*") if p.is_file()}
    workspace = filesystem(tmp_path)
    InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace),
                              Prerequisites(lambda *_: (persist_selected(req), True)[1])).create(req)
    after = {str(p.relative_to(recipe)): p.read_bytes() for p in recipe.rglob("*") if p.is_file()}
    assert before == after
    agent = tmp_path / ".lingtai" / "orch-dir"
    init = json.loads((agent / "init.json").read_text())
    assert init["pad"] == "root-local Pad"
    assert init["manifest"]["capabilities"]["skills"]["paths"] == ["../.library_shared", "../../library"]
    assert read_resolved_prompt_inputs(agent).covenant == "localized covenant"
    assert (agent / ".prompt").read_text() == "human zh unknown  {{soul_delay}}"


def test_postpublication_marker_warning_never_discards_known_project(tmp_path):
    req = request(tmp_path, recipe="")
    def publish(source, target):
        _exclusive_rename(source, target)
        marker = target / ".lingtai-launcher-staging"
        marker.unlink()
        marker.mkdir()  # test-only removal failure after the known rename
    workspace = filesystem(tmp_path, publish_directory=publish)
    result = InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace),
                                       Prerequisites(lambda *_: (persist_selected(req), True)[1])).create(req)
    assert result.warnings == ("staging_marker_retained",)
    assert result.to_payload()["committed"] is True
    assert (tmp_path / ".lingtai" / "orch-dir" / "init.json").is_file()


def test_operation_specific_serialized_ready_ack_and_reject_unknown_fields():
    attempt = CreationAttempt("bound-attempt")
    ref = "/presets/selected.json"
    outgoing = io.StringIO()
    incoming = io.StringIO(json.dumps({"status": "prerequisites_acknowledged", "attempt_id": attempt.attempt_id,
                                      "preset_ref": ref, "accepted": True}) + "\n")
    assert JsonCreationPrerequisiteAdapter(incoming, outgoing).persist_and_verify(attempt, ref)
    assert json.loads(outgoing.getvalue()) == {"status": "ready_for_prerequisites", "committed": False,
                                               "attempt_id": attempt.attempt_id, "preset_ref": ref}
    for bad in ["", "{}\n", '{"status":1,"status":2}\n', '{"x":NaN}\n', '[]\n']:
        with pytest.raises(ProjectCreationError):
            JsonCreationPrerequisiteAdapter(io.StringIO(bad), io.StringIO()).persist_and_verify(attempt, ref)


def test_root_cli_real_filesystem_exchange_does_not_start_agent(tmp_path, monkeypatch):
    from lingtai import cli
    from lingtai.agent import Agent
    req = request(tmp_path)
    payload = {"project_dir": req.project_dir, "approved_root": req.approved_root,
               "agent_name": req.agent_name, "directory_name": req.directory_name,
               "preset_ref": req.preset_ref, "preset_manifest": req.preset_manifest,
               "options": {"language": "zh", "covenant": "approved covenant",
                           "env_file": req.options.env_file},
               "recipe": "adaptive", "process_evidence": "clean"}
    output = io.StringIO()
    class Incoming:
        first = True
        def readline(self, _limit):
            if self.first:
                self.first = False
                return json.dumps(payload) + "\n"
            ready = json.loads(output.getvalue())
            assert ready["status"] == "ready_for_prerequisites"
            assert not (tmp_path / ".lingtai").exists()
            persist_selected(req)
            return json.dumps({"status": "prerequisites_acknowledged", "attempt_id": ready["attempt_id"],
                               "preset_ref": ready["preset_ref"], "accepted": True}) + "\n"
    monkeypatch.setattr(Agent, "start", lambda *_args, **_kwargs: pytest.fail("no runtime launch"))
    monkeypatch.setattr(sys, "stdin", Incoming())
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "project", "create-interactive"])
    cli.main()
    ready, created = [json.loads(line) for line in output.getvalue().splitlines()]
    assert ready["attempt_id"] == created["attempt_id"]
    assert created["status"] == "created" and created["committed"] is True
    assert created["agent_name"] == "Display name" and created["directory_name"] == "orch-dir"
    # Raw secrets rejected; only public credential environment references are accepted.
    payload["preset_manifest"] = copy.deepcopy(payload["preset_manifest"])
    payload["preset_manifest"]["llm"]["api_key"] = "DO-NOT-EMIT"
    with pytest.raises(ProjectCreationError) as error:
        decode_creation_request(payload)
    assert error.value.error.code == "credentials_must_remain_outer"

def test_missing_required_env_file_rejected_before_any_write_or_prerequisite(tmp_path):
    req = request(tmp_path)
    req = replace(req, options=replace(req.options, env_file=""))
    workspace = filesystem(tmp_path)
    prerequisites = Prerequisites()
    with pytest.raises(ProjectCreationError) as error:
        InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), prerequisites).create(req)
    assert error.value.error.code == "init_preflight_failed"
    assert error.value.__cause__ is None
    assert prerequisites.calls == 0
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["missing-env", "owner-value-error"])
def test_cli_init_preflight_failure_is_sanitized_before_ready(tmp_path, monkeypatch, failure):
    from lingtai import cli, init_schema
    req = request(tmp_path)
    options = {"language": req.options.language, "covenant": req.options.covenant}
    if failure == "owner-value-error":
        options["env_file"] = req.options.env_file
        def invalid_owner(_data):
            raise ValueError("DO-NOT-EMIT arbitrary owner exception")
        monkeypatch.setattr(init_schema, "validate_init", invalid_owner)
    payload = {"project_dir": req.project_dir, "approved_root": req.approved_root,
               "agent_name": req.agent_name, "directory_name": req.directory_name,
               "preset_ref": req.preset_ref, "preset_manifest": req.preset_manifest,
               "options": options, "recipe": "adaptive", "process_evidence": "clean"}
    outgoing, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload) + "\n"))
    monkeypatch.setattr(sys, "stdout", outgoing)
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "project", "create-interactive"])
    with pytest.raises(SystemExit) as terminal:
        cli.main()
    assert terminal.value.code == 1
    assert outgoing.getvalue() == ""  # no ready; outer persistence not authorized
    assert json.loads(errors.getvalue()) == {
        "status": "error", "committed": False, "code": "init_preflight_failed",
        "error": "interactive Project creation could not complete",
    }
    assert "DO-NOT-EMIT" not in errors.getvalue()
    assert "api_key_env" not in errors.getvalue()
    assert list(tmp_path.iterdir()) == []


def test_cli_bad_ack_is_known_uncommitted_error_without_raw_input_or_exception(tmp_path, monkeypatch):
    from lingtai import cli
    req = request(tmp_path)
    payload = {"project_dir": req.project_dir, "approved_root": req.approved_root,
               "agent_name": req.agent_name, "directory_name": req.directory_name,
               "preset_ref": req.preset_ref, "preset_manifest": req.preset_manifest,
               "options": {"language": "zh", "covenant": "approved covenant",
                           "env_file": req.options.env_file},
               "recipe": "adaptive", "process_evidence": "clean"}
    incoming = io.StringIO(json.dumps(payload) + '\n{"wrong":"DO-NOT-EMIT"}\n')
    outgoing, errors = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdin", incoming)
    monkeypatch.setattr(sys, "stdout", outgoing)
    monkeypatch.setattr(sys, "stderr", errors)
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "project", "create-interactive"])
    with pytest.raises(SystemExit) as terminal:
        cli.main()
    assert terminal.value.code == 1
    assert json.loads(outgoing.getvalue())["status"] == "ready_for_prerequisites"
    error = json.loads(errors.getvalue())
    assert error["committed"] is False and error["status"] == "error"
    assert error["code"] == "invalid_prerequisite_acknowledgement"
    assert "DO-NOT-EMIT" not in outgoing.getvalue() + errors.getvalue()
    assert not (tmp_path / ".lingtai").exists()
    assert not list(tmp_path.glob(".lingtai.create-*"))

@pytest.mark.parametrize("local", ["stray", "malformed", "unsupported-library"])
def test_recipe_fallback_does_not_overwrite_parsed_local_authority(tmp_path, local):
    recipe = tmp_path / ".recipe"
    recipe.mkdir()
    manifest = recipe / "recipe.json"
    if local == "malformed":
        manifest.write_text("[")
    elif local == "unsupported-library":
        manifest.write_text('{"name":"Valid local","id":null,"description":null,"library_name":"../outside"}')
    req = request(tmp_path)
    workspace = filesystem(tmp_path)
    prerequisites = Prerequisites(lambda *_: (persist_selected(req), True)[1])
    usecase = InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), prerequisites)
    if local == "unsupported-library":
        before = manifest.read_bytes()
        with pytest.raises(ProjectCreationError) as error:
            usecase.create(req)
        assert error.value.error.code == "invalid_recipe_library"
        assert manifest.read_bytes() == before
        assert prerequisites.calls == 0
        assert not (tmp_path / ".lingtai").exists()
        assert not list(tmp_path.glob(".lingtai.create-*"))
    else:
        assert usecase.create(req).to_payload()["committed"] is True
        assert json.loads(manifest.read_text())["id"] == "adaptive"


@pytest.mark.parametrize("dual", [False, True])
def test_unchanged_saved_legacy_capabilities_bind_semantically(tmp_path, dual):
    req = request(tmp_path)
    raw = copy.deepcopy(req.preset_manifest)
    raw["capabilities"]["bash"] = raw["capabilities"].pop("shell")
    if dual:
        raw["capabilities"]["shell"] = copy.deepcopy(raw["capabilities"]["bash"])
    def persist(_attempt, _ref):
        Path(req.preset_ref).write_text(json.dumps({"description": {"summary": "unchanged legacy"}, "manifest": raw}))
        return True
    workspace = filesystem(tmp_path)
    result = InteractiveCreationUseCase(workspace, FilesystemCreationRecipeAdapter(workspace), Prerequisites(persist)).create(req)
    assert result.to_payload()["committed"] is True
    assert json.loads(Path(req.preset_ref).read_text())["manifest"] == raw
