"""Filesystem Project configuration smoke and partial-write boundaries."""
from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

from lingtai.adapters.project_configuration import FilesystemProjectConfigurationAdapter
from lingtai.cli_project_configuration import execute, read_request
from lingtai.kernel.project import ProjectCreationError
from lingtai.kernel.project.configuration import (
    ConfigurationOptions, ConfigurationWriteReceipt, ConfigureMemberRequest,
    ProjectConfigurationUseCase,
)

MATRIX = json.loads((Path(__file__).parent / "fixtures/project_configuration.json").read_text())
PUBLIC_MANIFEST = {"llm": {"provider": "openai", "model": "selected-model"},
                   "capabilities": {"shell": {"yolo": False}}}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


def filesystem(root):
    from lingtai.agent import load_preset
    return FilesystemProjectConfigurationAdapter(root, load_preset=load_preset)


def fixture_workspace(root):
    references = {name: str(root / name) for name in ("old.json", "active.json", "selected.json")}
    for name in references:
        write(root / name, {"description": {"summary": "fixture"}, "manifest": PUBLIC_MANIFEST})
    previous = copy.deepcopy(MATRIX["existing_init"])
    policy = previous["manifest"]["preset"]
    for key in ("default", "active"):
        policy[key] = references[policy[key]]
    policy["allowed"] = [references[r] for r in policy["allowed"]]
    blueprint = copy.deepcopy(MATRIX["existing_blueprint"])
    write(root / ".lingtai/orch-dir/init.json", previous)
    write(root / ".lingtai/orch-dir/.agent.json", blueprint)
    workspace = filesystem(root)
    return workspace, references


@pytest.mark.parametrize("case", MATRIX["cases"], ids=lambda c: c["id"])
def test_shared_setup_matrix(tmp_path, case):
    workspace, refs = fixture_workspace(tmp_path)
    request = ConfigureMemberRequest("orch-dir", "New Display", "" if case["keep_current"] else refs["selected.json"],
        case["keep_current"], PUBLIC_MANIFEST,
        ConfigurationOptions(preserve_active=True, allowed_presets=tuple(refs[r] for r in case["allowed"]),
                             env_file="fallback.env", venv_path="fallback/venv",
                             mcp={"imap": {"command": "must-not-replace-custom"}}))
    result = ProjectConfigurationUseCase(workspace).configure(request)
    assert not result.code
    saved = workspace.observe_member("orch-dir")
    policy = saved.init_document["manifest"]["preset"]
    assert policy["active"] == refs[case["expected_active"]]
    assert policy["default"] == refs[case["expected_default"]]
    assert "keep_current" not in json.dumps(policy)
    for key in ("custom_root", "pad", "env_file", "venv_path", "mcp", "addons"):
        assert saved.init_document[key] == MATRIX["existing_init"][key]
    assert saved.init_document["manifest"]["custom_manifest"] == 9007199254740993123456789
    for key in ("agent_id", "created_at", "molt_count", "nickname", "state", "custom_blueprint"):
        assert saved.blueprint[key] == MATRIX["existing_blueprint"][key]
    assert saved.blueprint["address"] == "orch-dir"
    from lingtai.init_reader import read_init, reader_callbacks
    from lingtai.agent import load_preset
    path = tmp_path / ".lingtai/orch-dir"
    materialize, prepare = reader_callbacks(path, load_preset=load_preset)
    actual = read_init(path, materialize=materialize, prepare=prepare)
    assert actual.ok and result.source.reader_status == actual.status.value


def test_shared_policy_and_config_propagation(tmp_path):
    workspace, refs = fixture_workspace(tmp_path)
    worker = copy.deepcopy(MATRIX["existing_init"])
    worker["manifest"]["preset"] = {"active": refs["active.json"], "default": refs["old.json"],
                                    "allowed": [refs["old.json"], refs["active.json"]], "custom_policy": 42}
    worker["manifest"]["admin"] = {"unknown_flag": True}
    write(tmp_path / ".lingtai/worker/init.json", worker)
    usecase = ProjectConfigurationUseCase(workspace)
    assert usecase.propagate_policy("orch-dir", refs["selected.json"], (refs["selected.json"],)).updated == 1
    result = usecase.propagate_config("orch-dir")
    assert result.updated == 1 and not result.code
    saved = workspace.observe_member("worker").init_document
    assert saved["manifest"]["preset"]["active"] == refs["selected.json"]
    assert saved["manifest"]["preset"]["custom_policy"] == 42
    assert saved["manifest"]["admin"] == {"karma": False, "nirvana": False}
    assert "addons" not in saved
    assert saved["pad"] == "durable Pad" and saved["mcp"] == worker["mcp"]


@pytest.mark.parametrize("state", ["absent", "present", "mixed"])
def test_shared_rehydration_state_gate(tmp_path, state):
    workspace, refs = fixture_workspace(tmp_path)
    imported = tmp_path / "imported"
    imported.mkdir()
    for name in ("orch-dir", "a-worker", "z-worker", "human", ".helper"):
        write(imported / ".lingtai" / name / ".agent.json",
              {"agent_name": "Display " + name, "admin": None if name == "human" else {}})
        if state == "present" or (state == "mixed" and name == "orch-dir"):
            write(imported / ".lingtai" / name / "init.json", {"manifest": PUBLIC_MANIFEST, "pad": ""})
    workspace = filesystem(imported)
    req = ConfigureMemberRequest("orch-dir", "must-not-replace-blueprint-name", refs["selected.json"], False,
                                 PUBLIC_MANIFEST, ConfigurationOptions(env_file="public.env", venv_path="venv"))
    usecase = ProjectConfigurationUseCase(workspace)
    if state == "mixed":
        with pytest.raises(ProjectCreationError) as exc:
            usecase.rehydrate(req)
        assert exc.value.error.code == "mixed_initialization_state"
    else:
        result = usecase.rehydrate(req)
        assert result.outcome == ("complete" if state == "absent" else "unchanged")
        assert result.updated == (3 if state == "absent" else 0)
        if state == "absent":
            saved = workspace.observe_member("a-worker")
            assert saved.init_document["manifest"]["agent_name"] == "Display a-worker"
            assert saved.init_document["manifest"]["admin"] == {}
            assert "addons" not in saved.init_document
            assert workspace.observe_member("human").init_present is False
            assert workspace.observe_member(".helper").init_present is False


def test_rehydration_failure_stops_and_preserves_prior_writes(tmp_path, monkeypatch):
    workspace, refs = fixture_workspace(tmp_path)
    (tmp_path / ".lingtai/orch-dir/init.json").unlink()
    for name in ("orch-dir", "a-worker", "z-worker"):
        write(tmp_path / ".lingtai" / name / ".agent.json", {"agent_name": name, "admin": {}})
    save = workspace.save_member_init
    def fail_last(name, document, **kwargs):
        if name == "z-worker":
            return ConfigurationWriteReceipt(code="init_write_failed")
        return save(name, document, **kwargs)
    monkeypatch.setattr(workspace, "save_member_init", fail_last)
    req = ConfigureMemberRequest("orch-dir", "orch-dir", refs["selected.json"], False, PUBLIC_MANIFEST,
                                 ConfigurationOptions(env_file="public.env", venv_path="venv"))
    result = ProjectConfigurationUseCase(workspace).rehydrate(req)
    assert result.outcome == "partial" and result.updated == 2 and result.failed == 1
    assert workspace.observe_member("a-worker").init_present
    assert not workspace.observe_member("z-worker").init_present
    with pytest.raises(ProjectCreationError):
        ProjectConfigurationUseCase(workspace).rehydrate(req)  # no resumptive retry


def test_initialize_early_return_does_not_repair_helpers(tmp_path):
    write(tmp_path / ".lingtai/human/.agent.json", ["malformed-owner"])
    result = ProjectConfigurationUseCase(filesystem(tmp_path)).initialize()
    assert result.outcome == "unchanged"
    assert not (tmp_path / ".lingtai/.library_shared").exists()
    assert not (tmp_path / ".lingtai/human/mailbox").exists()


@pytest.mark.parametrize("raw", ['{"project_dir":"x","project_dir":"y"}', '{"api_key":"DO-NOT-EMIT"}', '{"x":NaN}', '[]'])
def test_strict_request_has_only_static_errors(raw):
    with pytest.raises(ProjectCreationError) as exc:
        read_request(io.StringIO(raw))
    assert "DO-NOT-EMIT" not in str(exc.value)


def test_missing_required_env_file_preflight_writes_nothing(tmp_path):
    manifest = copy.deepcopy(PUBLIC_MANIFEST)
    manifest["llm"]["api_key_env"] = "PUBLIC_KEY_SLOT"
    write(tmp_path / "selected.json", {"description": {"summary": "fixture"}, "manifest": manifest})
    req = {"project_dir": str(tmp_path), "directory_name": "orch", "agent_name": "Display",
           "preset_ref": str(tmp_path / "selected.json"), "keep_current": False,
           "options": {"env_file": "", "venv_path": "venv"}}
    result = execute("configure", req)
    assert result.code == "configuration_preflight_failed" and not result.source.init_written
    assert not (tmp_path / ".lingtai").exists()


def test_real_root_cli_initialize_protocol(monkeypatch, tmp_path, capsys):
    from lingtai import cli
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "project", "initialize"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"project_dir": str(tmp_path)})))
    cli.main()
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["status"] == "initialized" and payload["outcome"] == "complete"
    assert captured.err == ""


def test_filesystem_post_init_failure_retains_precise_receipt(monkeypatch, tmp_path):
    workspace, refs = fixture_workspace(tmp_path)
    import lingtai.adapters.project_configuration as adapter
    original = adapter._atomic_json
    def fail_manifest(path, document, **kwargs):
        if path.name == ".agent.json":
            raise OSError("DO-NOT-EMIT exception")
        return original(path, document, **kwargs)
    monkeypatch.setattr(adapter, "_atomic_json", fail_manifest)
    request = ConfigureMemberRequest("orch-dir", "Changed", refs["selected.json"], False, PUBLIC_MANIFEST,
                                     ConfigurationOptions(env_file="public.env", venv_path="venv"))
    result = ProjectConfigurationUseCase(workspace).configure(request)
    assert result.outcome == "partial" and result.source.init_written
    assert not result.source.manifest_written and result.code == "manifest_write_failed"
    assert workspace.observe_member("orch-dir").blueprint["agent_id"] == "stable-id"
    assert "DO-NOT-EMIT" not in json.dumps(result.to_payload())


def test_prompts_use_owner_serializer_and_omission_preserves_bytes(tmp_path):
    workspace, refs = fixture_workspace(tmp_path)
    from lingtai.tools.psyche.settings import serialize_prompt_owner_document, read_resolved_prompt_inputs
    path = tmp_path / ".lingtai/orch-dir/settings/psyche.json"
    path.parent.mkdir()
    original = serialize_prompt_owner_document(covenant="durable", base_prompt="base")
    path.write_text(original)
    payload = {"project_dir": str(tmp_path), "directory_name": "orch-dir", "agent_name": "Display",
               "preset_ref": refs["selected.json"], "keep_current": False,
               "options": {"env_file": "fallback.env", "venv_path": "fallback-venv"}}
    assert not execute("configure", payload).code
    assert path.read_text() == original
    payload["prompts"] = {"covenant": "explicit edit"}
    assert not execute("configure", payload).code
    actual = read_resolved_prompt_inputs(path.parent.parent)
    assert actual.covenant == "explicit edit" and actual.base_prompt == "base"


@pytest.mark.parametrize("dual", [False, True])
def test_actual_saved_legacy_alias_and_large_context_metadata_are_exact(tmp_path, dual):
    huge = 9007199254740993123456789
    raw = {"context_limit": huge,
           "llm": {"provider": "openai", "model": "fixture-model"},
           "capabilities": {"bash": {"yolo": False}}}
    if dual:
        raw["capabilities"]["shell"] = {"yolo": False}
    selected = tmp_path / "legacy.json"
    write(selected, {"description": {"summary": "legacy metadata"}, "manifest": raw})
    before = selected.read_bytes()
    result = execute("configure", {
        "project_dir": str(tmp_path), "directory_name": "orch", "agent_name": "Display",
        "preset_ref": str(selected), "keep_current": False,
        "options": {"env_file": "", "venv_path": "fixture-venv", "context_limit": huge},
    })
    assert not result.code
    saved = filesystem(tmp_path).observe_member("orch").init_document["manifest"]
    assert saved["context_limit"] == huge and saved["llm"]["context_limit"] == huge
    assert saved["capabilities"] == {"shell": {"yolo": False}}
    assert selected.read_bytes() == before  # owner normalization only in memory


def test_shared_policy_empty_skip_preserves_all_member_mode(tmp_path):
    workspace, refs = fixture_workspace(tmp_path)
    result = ProjectConfigurationUseCase(workspace).propagate_policy(
        "", refs["selected.json"], (refs["selected.json"],))
    assert result.updated == 1 and result.failed == 0
    assert workspace.observe_member("orch-dir").init_document["manifest"]["preset"]["default"] == refs["selected.json"]


def test_policy_propagation_accepts_legacy_partial_init(tmp_path):
    # The old caller accepts a parsed preset block, not a bootable init. No Pad,
    # env, blueprint or preset file is required to maintain its authorization.
    original = {"manifest": {"preset": {"active": "old.json", "default": "old.json",
                "allowed": ["old.json"], "custom_policy": 9007199254740993123456789},
                "user_manifest": True}, "covenant_file": "legacy.md",
                "user_config": {"keep": "exact"}}
    for name in ("alice", "carol", "selected", "human"):
        write(tmp_path / ".lingtai" / name / "init.json", original)
    (tmp_path / ".lingtai/broken").mkdir()
    (tmp_path / ".lingtai/broken/init.json").write_text("{broken")
    write(tmp_path / ".lingtai/older/init.json", {"manifest": {}})
    # Unrelated malformed owner settings cannot gate a policy-only edit.
    owner = tmp_path / ".lingtai/alice/settings/psyche.json"
    owner.parent.mkdir()
    owner.write_text("{unrelated malformed owner")
    def no_runtime_preset_load(*args, **kwargs):
        raise AssertionError("policy propagation is not reconstruction")
    workspace = FilesystemProjectConfigurationAdapter(tmp_path, load_preset=no_runtime_preset_load)
    result = ProjectConfigurationUseCase(workspace).propagate_policy("selected", "new.json", ("new.json",))
    assert (result.updated, result.skipped, result.failed, result.code) == (2, 3, 1, "member_document_unreadable")
    assert result.source.reader_status == "not_read"
    expected = copy.deepcopy(original)
    expected["manifest"]["preset"].update(active="new.json", default="new.json", allowed=["new.json"])
    for name in ("alice", "carol"):
        saved = workspace.observe_member(name)
        assert saved.init_document == expected and not saved.blueprint_present
    for name in ("selected", "human"):
        assert workspace.observe_member(name).init_document == original
    assert owner.read_text() == "{unrelated malformed owner"
    assert (tmp_path / ".lingtai/broken/init.json").read_text() == "{broken"


def test_policy_write_failure_continues_without_runtime_preflight(monkeypatch, tmp_path):
    import lingtai.adapters.project_configuration as adapter
    original = adapter._atomic_json
    document = {"manifest": {"preset": {"active": "old", "default": "old", "allowed": ["old"]}}}
    for name in ("alice", "carol"):
        write(tmp_path / ".lingtai" / name / "init.json", document)
    def fail_first(path, value, **kwargs):
        if path.parent.name == "alice":
            raise OSError("private write diagnostic")
        return original(path, value, **kwargs)
    monkeypatch.setattr(adapter, "_atomic_json", fail_first)
    workspace = filesystem(tmp_path)
    result = ProjectConfigurationUseCase(workspace).propagate_policy("", "new", ("new",))
    assert (result.updated, result.failed, result.code) == (1, 1, "init_write_failed")
    assert workspace.observe_member("alice").init_document == document
    assert workspace.observe_member("carol").init_document["manifest"]["preset"]["default"] == "new"
    assert "private write diagnostic" not in json.dumps(result.to_payload())


@pytest.mark.parametrize("case", ["fresh", "existing", "explicit"])
def test_configure_legacy_covenant_path_precedence_preserves_psyche(tmp_path, case):
    from lingtai.tools.psyche.settings import serialize_prompt_owner_document, read_resolved_prompt_inputs
    path = tmp_path / ".lingtai/orch"
    default = str(tmp_path / "global/covenant/zh/covenant.md")
    prior = str(tmp_path / "existing-covenant.md")
    explicit = tmp_path / "explicit-covenant.md"
    explicit.write_text("explicit Covenant")
    if case != "fresh":
        write(path / "init.json", {"manifest": PUBLIC_MANIFEST, "pad": "durable Pad",
              "covenant_file": prior, "user_config": {"keep": True}})
    owner = path / "settings/psyche.json"
    owner.parent.mkdir(parents=True, exist_ok=True)
    original = serialize_prompt_owner_document(covenant="durable Covenant", base_prompt="durable base")
    owner.write_text(original)
    write(tmp_path / "selected.json", {"description": {"summary": "fixture"}, "manifest": PUBLIC_MANIFEST})
    env = tmp_path / "public.env"
    env.write_text("")
    options = {"env_file": str(env), "venv_path": "venv", "language": "zh",
               "default_covenant_file": default}
    payload = {"project_dir": str(tmp_path), "directory_name": "orch", "agent_name": "Display",
               "preset_ref": str(tmp_path / "selected.json"), "keep_current": False, "options": options}
    if case == "explicit":
        options["covenant_file"] = str(explicit)
        payload["prompts"] = {"covenant_file": str(explicit)}
    result = execute("configure", payload)
    assert not result.code and result.source.psyche_written == (case == "explicit")
    saved = json.loads((path / "init.json").read_text())
    assert saved["covenant_file"] == {"fresh": default, "existing": prior, "explicit": str(explicit)}[case]
    if case != "fresh":
        assert saved["pad"] == "durable Pad" and saved["user_config"] == {"keep": True}
    if case != "explicit":
        assert owner.read_text() == original
    else:
        prompts = read_resolved_prompt_inputs(path)
        assert prompts.covenant == "explicit Covenant" and prompts.base_prompt == "durable base"
