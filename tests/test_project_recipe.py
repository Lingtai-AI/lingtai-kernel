"""Authored, not executed: thin fake/real existing Recipe owner contracts."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from lingtai.adapters.project_recipe import FilesystemProjectRecipeAdapter
from lingtai.cli_project_recipe import apply_payload, execute
from lingtai.kernel.project import ProjectCreationError
from lingtai.kernel.project.recipe import (
    GreetLayer, ProjectRecipePort, ProjectRecipeUseCase, RecipeApplyRequest,
    RecipeInfo, RecipeRendering, RecipeSkills, RecipeSnapshotReceipt, RecipeTree,
    RecipeWrite, render_greet,
)

RENDER = RecipeRendering("2030-01-02 03:04", "human@fixture", "City, Region", "  - /help — Help")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())


@pytest.mark.parametrize("raw,name,library", [
    (b'{"name":"normal"}', "normal", None),
    (b'{"NAME":"normal"}', "normal", None),
    (b'{"name":"normal","name":null}', "normal", None),
    (b'{"name":"n\xff"}', "n\ufffd", None),
    (b'{"name":"n\xe2\x82"}', "n\ufffd\ufffd", None),
    (b'{"name":"n\xed\xa0\x80"}', "n\ufffd\ufffd\ufffd", None),
    (rb'{"name":"\ud800x\udc00"}', "\ufffdx\ufffd", None),
    (rb'{"name":"\ud83d\ude00"}', "\U0001f600", None),
    ('{"name":"\U0001f600"}'.encode(), "\U0001f600", None),
    (b'{"name":null,"NAME":"first","name":"last"}', "last", None),
    (b'{"name":"normal","library_name":"lib","LIBRARY_NAME":null}', "normal", None),
    (b'{"name":"normal","library_name":null,"LIBRARY_NAME":"lib"}', "normal", "lib"),
    (b'{"name":"normal","library_name":""}', "normal", ""),
    (b'{"name":"normal","LIBRARY_NAME":"lib\xff"}', "normal", "lib\ufffd"),
    (b'{"name":"normal","unknown":{"NAME":42},"other":[null,1e9999]}', "normal", None),
    (b'{"name":"normal","unknown":' + b'9' * 5000 + b'}', "normal", None),
])
def test_recipe_metadata_go_accepted_bytes(tmp_path, raw, name, library):
    # The first four rows reproduce the supplied baseline probe counterexamples.
    path = tmp_path / ".recipe/recipe.json"
    write(path, raw)
    port = FilesystemProjectRecipeAdapter(tmp_path, load_preset=lambda *_: {})
    info = port.load_info()
    assert (info.name, info.library_name) == (name, library)
    assert info.library_path_entry == (str(Path("..") / ".." / library) if library else "")
    assert path.read_bytes() == raw
    assert not (tmp_path / ".lingtai").exists()


@pytest.mark.parametrize("raw", [
    '{"name":"normal"}'.encode("utf-16"),
    '{"name":"normal"}'.encode("utf-16-be"),
    '{"name":"normal"}'.encode("utf-32"),
    b'\xef\xbb\xbf{"name":"normal"}',
    b'{"name":42}',
    b'{"name":42,"name":"normal"}',
    b'{"name":"normal","NAME":42}',
    b'{"name":"normal","name":""}',
    b'{"name":null}',
    b'null', b'[]', b'{}', b'{"name":"normal"} {}',
    b'{"name":"normal","ignored":NaN}',
    b'{"name":"normal","ignored":Infinity}',
    b'{"name":"normal","ignored":[1,]}',
])
def test_recipe_metadata_go_rejected_bytes(tmp_path, raw):
    from lingtai.kernel.project.recipe import RecipeOperationError
    path = tmp_path / ".recipe/recipe.json"
    write(path, raw)
    port = FilesystemProjectRecipeAdapter(tmp_path, load_preset=lambda *_: {})
    with pytest.raises(RecipeOperationError) as caught:
        port.load_info()
    assert caught.value.failure.phase == "metadata"
    assert caught.value.failure.operation == "decode"
    assert path.read_bytes() == raw
    assert not (tmp_path / ".lingtai").exists()


def test_recipe_metadata_go_simple_fold_and_string_defaults():
    from lingtai.adapters.project_recipe import _read_recipe_metadata
    value = _read_recipe_metadata(rb'{"name":"normal","de\u017fcription":"d","ver\u017fion":"v"}')
    assert value["description"] == "d" and value["version"] == "v"
    value = _read_recipe_metadata(b'{"name":"normal","description":null,"id":null,"version":null}')
    assert value == {"name": "normal", "id": "", "description": "", "version": "1.0.0", "library_name": None}


class MemoryRecipe(ProjectRecipePort):
    def __init__(self):
        self.selected = RecipeTree(False)
        self.snapshot = RecipeTree(False)
        self.info = RecipeInfo("fixture")
        self.greet = GreetLayer()
        self.names = ("human", ".hidden", "worker")
        self.prompts = {}
        self.skills = {}
        self.events = []
        self.fail_prompt = set()
        self.fail_init = set()
        self.snapshot_receipt = RecipeSnapshotReceipt(True, True, True)

    def observe_trees(self):
        return self.selected, self.snapshot

    def load_info(self):
        return self.info

    def observe_greet(self, language):
        return self.greet

    def observe_directories(self):
        return self.names

    def write_prompt(self, name, content):
        self.events.append((name, "prompt"))
        if name in self.fail_prompt:
            return RecipeWrite(code="recipe_prompt_write_failed")
        self.prompts[name] = content
        return RecipeWrite(True)

    def observe_skills(self, name):
        return self.skills.get(name, RecipeSkills())

    def write_skills(self, name, evidence, paths):
        self.events.append((name, "init"))
        if name in self.fail_init:
            return RecipeWrite(code="recipe_init_write_failed")
        self.skills[name] = RecipeSkills(evidence.owner_content, True, paths)
        return RecipeWrite(True, "FULLY_EFFECTIVE")

    def replace_snapshot(self):
        self.events.append(("snapshot", "copy"))
        if self.snapshot_receipt.copied:
            self.snapshot = self.selected
        return self.snapshot_receipt


def filesystem(root):
    from lingtai.agent import load_preset
    return FilesystemProjectRecipeAdapter(root, load_preset=load_preset)


@pytest.mark.parametrize("kind", ["memory", "filesystem"])
def test_shared_observation_absent_changed_equal_and_byte_set(tmp_path, kind):
    port = MemoryRecipe() if kind == "memory" else filesystem(tmp_path)
    usecase = ProjectRecipeUseCase(port)
    assert (usecase.observe().state.value, usecase.observe().needs_apply) == ("absent", False)
    selected = {"recipe.json": b'{"name":"fixture"}', "greet/greet.md": b"hello"}
    if kind == "memory":
        port.selected = RecipeTree(True, tuple(selected.items()))
    else:
        for name, data in selected.items():
            write(tmp_path / ".recipe" / name, data)
    assert usecase.observe().state.value == "changed"
    if kind == "memory":
        port.snapshot = port.selected
    else:
        for name, data in selected.items():
            write(tmp_path / ".lingtai/.tui-asset/.recipe" / name, data)
        (tmp_path / ".recipe/empty-dir").mkdir()
        (tmp_path / ".recipe/recipe.json").chmod(0o600)
    assert usecase.observe().state.value == "equal"
    if kind == "memory":
        port.snapshot = RecipeTree(True, port.snapshot.files + (("extra", b""),))
    else:
        write(tmp_path / ".lingtai/.tui-asset/.recipe/extra", b"")
    assert usecase.observe().needs_apply


def test_observation_read_failure_preserves_legacy_boolean():
    port = MemoryRecipe()
    port.selected = RecipeTree(False, read_failed=True)
    result = ProjectRecipeUseCase(port).observe()
    assert result.state.value == "read_failure" and not result.needs_apply
    port.selected = RecipeTree(True, read_failed=True)
    assert ProjectRecipeUseCase(port).observe().needs_apply
    assert execute("recipe-observe", {"project_dir": ""})["state"] == "absent"


def test_exact_ordered_go_substitution_unknown_retired_and_identity():
    content = b"{{time}}|{{addr}}|{{lang}}|{{location}}|{{commands}}|{{soul_delay}}|{{AGENT_NAME}}"
    assert render_greet(content, "zh", RENDER) == (
        "2030-01-02 03:04|human@fixture|zh|City, Region|  - /help — Help|{{soul_delay}}|{{AGENT_NAME}}".encode())
    assert render_greet(content, "zh", None) == content
    chained = RecipeRendering("{{addr}}", "{{lang}}", "{{commands}}", "{{time}}")
    assert render_greet(b"{{time}} {{location}}", "en", chained) == b"en {{time}}"


@pytest.mark.parametrize("kind", ["memory", "filesystem"])
def test_shared_no_skills_no_greet_and_target_policy(tmp_path, kind):
    if kind == "memory":
        port = MemoryRecipe()
        port.info = RecipeInfo("fixture", "library")
        port.selected = RecipeTree(True, (("recipe.json", b'{}'),))
    else:
        write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": "library"})
        for name in ("human", ".hidden", "worker"):
            (tmp_path / ".lingtai" / name).mkdir(parents=True)
        port = filesystem(tmp_path)
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("en", None))
    assert not result.code and result.greet_status == "absent"
    assert [m.directory_name for m in result.members] == ["worker"]
    assert not result.members[0].init_written and not result.members[0].prompt_written
    assert result.snapshot.attempted and result.snapshot.copied
    if kind == "filesystem":
        assert not (tmp_path / ".lingtai/worker/init.json").exists()


def test_real_locale_then_root_and_unreadable_selected_skips(tmp_path, monkeypatch):
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture"})
    write(tmp_path / ".recipe/greet/greet.md", b"root")
    write(tmp_path / ".recipe/greet/zh/greet.md", b"localized")
    (tmp_path / ".lingtai/worker").mkdir(parents=True)
    port = filesystem(tmp_path)
    assert port.observe_greet("fr").content == b"root"
    assert port.observe_greet("zh").content == b"localized"
    original = Path.read_bytes
    def unreadable(path):
        if path == tmp_path / ".recipe/greet/zh/greet.md":
            raise PermissionError("secret path must not escape")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", unreadable)
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("zh", RENDER))
    assert result.greet_status == "unreadable" and not result.members[0].prompt_written
    assert result.snapshot.attempted


def test_real_skills_rewrite_preserves_exact_values_and_uses_actual_readers(tmp_path):
    from lingtai.tools.psyche.settings import serialize_prompt_owner_document
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": "library"})
    member = tmp_path / ".lingtai/worker"
    preset = tmp_path / "preset.json"
    manifest = {"llm": {"provider": "openai", "model": "fixture"},
                "capabilities": {"skills": {"paths": ["old"]}, "shell": {"yolo": False}}}
    write(preset, {"description": {"summary": "fixture"}, "manifest": manifest})
    policy = {"active": str(preset), "default": str(preset), "allowed": [str(preset)], "custom": "retain"}
    document = {"manifest": {**manifest, "preset": policy}, "agent_name": "Display", "pad": "",
                "custom": 9007199254740993123456789, "principle_file": "retired", "procedures_file": "retired"}
    source = json.dumps(document)
    marker = '"custom": 9007199254740993123456789'
    assert source.count(marker) == 1
    source = source.replace(marker, marker + ', "precise_number":1.2345678901234567890123456789, "huge_number":1e999', 1)
    write(member / "init.json", source.encode())
    owner = serialize_prompt_owner_document(covenant="retain covenant").encode()
    write(member / "settings/psyche.json", owner)
    port = filesystem(tmp_path)
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("", None))
    assert not result.code and result.members[0].init_written
    assert result.members[0].reader_status in {"FULLY_EFFECTIVE", "READ_OK_WITH_IGNORED_FIELDS"}
    saved = json.loads((member / "init.json").read_bytes())
    assert saved["custom"] == document["custom"] and saved["manifest"]["preset"] == policy
    from decimal import Decimal
    exact = json.loads((member / "init.json").read_bytes(), parse_float=Decimal)
    assert exact["precise_number"] == Decimal("1.2345678901234567890123456789")
    assert exact["huge_number"] == Decimal("1e999")
    assert saved["manifest"]["capabilities"]["skills"]["paths"] == ["old", str(Path("..") / ".." / "library")]
    assert "principle_file" not in saved and "procedures_file" not in saved
    assert (member / "settings/psyche.json").read_bytes() == owner
    before = (member / "init.json").read_bytes()
    repeat = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("", None))
    assert not repeat.members[0].init_written and (member / "init.json").read_bytes() == before


def test_first_error_quirk_disclosed_and_partial_snapshot_can_be_equal():
    port = MemoryRecipe()
    port.info = RecipeInfo("fixture", "library")
    port.selected = RecipeTree(True, (("recipe.json", b'{}'),))
    port.greet = GreetLayer(True, b"hello")
    port.names = ("b", "a", "human", ".dot")
    port.fail_prompt = {"a", "b"}
    port.skills["b"] = RecipeSkills(b'{}', True)
    port.fail_init = {"b"}
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("en", None))
    payload = apply_payload(result)
    assert payload["outcome"] == "partial" and payload["applied"] == 0
    assert payload["legacy_applied"] == 1 and payload["failed"] == 2
    assert port.events == [("a", "prompt"), ("b", "prompt"), ("b", "init"), ("snapshot", "copy")]
    assert result.members[1].prompt_code and result.members[1].init_code
    assert result.snapshot.copied and ProjectRecipeUseCase(port).observe().state.value == "equal"


@pytest.mark.parametrize("phase", ["init", "reader", "snapshot_remove", "snapshot_copy"])
def test_real_phase_receipts_and_always_snapshot(tmp_path, monkeypatch, phase):
    import lingtai.adapters.project_recipe as adapter_module
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": "library"})
    write(tmp_path / ".recipe/greet/greet.md", b"hello")
    write(tmp_path / ".lingtai/worker/init.json", {"manifest": {"capabilities": {"skills": {}}}})
    write(tmp_path / ".lingtai/.tui-asset/.recipe/old", b"retain on failed remove")
    port = filesystem(tmp_path)
    if phase == "init":
        monkeypatch.setattr(adapter_module, "_legacy_init_write", lambda *_: (_ for _ in ()).throw(OSError("secret")))
    elif phase == "reader":
        monkeypatch.setattr(port, "_reader_status", lambda *_: "READ_FAILED")
    else:
        monkeypatch.setattr(port, "_reader_status", lambda *_: "FULLY_EFFECTIVE")
        if phase == "snapshot_remove":
            monkeypatch.setattr(adapter_module.shutil, "rmtree", lambda *_: (_ for _ in ()).throw(OSError("secret")))
        else:
            monkeypatch.setattr(port, "_copy_tree", lambda *_: (_ for _ in ()).throw(OSError("secret")))
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("en", RENDER))
    assert result.outcome == ("complete" if phase == "snapshot_remove" else "partial") and result.snapshot.attempted
    assert bool(result.code) == (phase in {"init", "snapshot_copy"})
    assert result.members[0].prompt_written
    assert result.members[0].init_written == (phase != "init")
    assert result.snapshot.copied == (phase != "snapshot_copy")
    if phase == "snapshot_remove":
        assert not result.snapshot.removal_succeeded
        assert ProjectRecipeUseCase(port).observe().needs_apply  # retained old file
    assert "secret" not in json.dumps(apply_payload(result))


def test_existing_library_authority_and_join_semantics(tmp_path, monkeypatch):
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": "../escape"})
    (tmp_path / ".lingtai/worker").mkdir(parents=True)
    before = (tmp_path / ".recipe/recipe.json").read_bytes()
    result = execute("recipe-apply", {"project_dir": str(tmp_path), "language": "en", "rendering": None})
    assert not result["code"] and result["legacy_applied"] == 1
    assert (tmp_path / ".recipe/recipe.json").read_bytes() == before
    assert (tmp_path / ".lingtai/.tui-asset/.recipe/recipe.json").read_bytes() == before
    for index, library in enumerate(("../escape", "nested/../library", "/absolute-looking", "..", "literal\\name", "a:b", "null\x00name")):
        fixture = tmp_path / str(index)
        fixture.mkdir()
        _assert_existing_library_join_clean_semantics(fixture, monkeypatch, library)


def test_actual_root_cli_static_envelope_and_no_agent_start(tmp_path, monkeypatch, capsys):
    from lingtai import cli
    from lingtai.agent import Agent
    monkeypatch.setattr(Agent, "start", lambda *_: pytest.fail("no runtime"))
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "project", "recipe-observe"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"project_dir": str(tmp_path)})))
    cli.main()
    output = capsys.readouterr()
    assert not output.err and json.loads(output.out) == {
        "status": "recipe_observed", "state": "absent", "needs_apply": False, "code": "", "error": ""}
    monkeypatch.setattr(sys, "argv", ["lingtai-agent", "project", "recipe-apply"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"project_dir":"PRIVATE","language":true,"rendering":null}'))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert not output.out and "PRIVATE" not in output.err
    assert json.loads(output.err)["outcome"] == "rejected"


def test_real_malformed_init_retained_and_no_skills_existing_bytes_untouched(tmp_path):
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": "library"})
    malformed = tmp_path / ".lingtai/a/init.json"
    no_skills = tmp_path / ".lingtai/b/init.json"
    write(malformed, b"{malformed PRIVATE")
    write(no_skills, {"custom": 9007199254740993123456789, "manifest": {"capabilities": {"shell": {"yolo": False}}}})
    before = no_skills.read_bytes()
    result = ProjectRecipeUseCase(filesystem(tmp_path)).apply(RecipeApplyRequest("", None))
    assert result.members[0].code == "recipe_skills_read_failed"
    assert malformed.read_bytes() == b"{malformed PRIVATE" and no_skills.read_bytes() == before
    assert not result.members[1].code and not result.members[1].init_written
    assert result.snapshot.copied and "PRIVATE" not in json.dumps(apply_payload(result))


def test_existing_language_and_natural_nonregular_io_errors(tmp_path, monkeypatch):
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture"})
    write(tmp_path / ".recipe/greet/greet.md", b"hello")
    (tmp_path / ".lingtai/worker/.prompt").mkdir(parents=True)
    write(tmp_path / ".lingtai/.tui-asset", b"protected non-directory")
    port = filesystem(tmp_path)
    # Accepted selected language resolves lexical parents, then root fallback.
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("../escape", None))
    assert result.greet_status == "selected"
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("", None))
    assert result.members[0].code == "recipe_prompt_write_failed"
    assert result.snapshot.attempted and not result.snapshot.copied
    assert (tmp_path / ".lingtai/.tui-asset").read_bytes() == b"protected non-directory"
    assert (tmp_path / ".lingtai/worker/.prompt").is_dir()
    fixture = tmp_path / "symlink-fixture"
    fixture.mkdir()
    _assert_existing_symlink_target_behavior(fixture, monkeypatch)


def _assert_existing_library_join_clean_semantics(tmp_path, monkeypatch, library):
    import os
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": library})
    write(tmp_path / ".lingtai/worker/init.json", {"manifest": {"capabilities": {"skills": {}}}})
    port = filesystem(tmp_path)
    monkeypatch.setattr(port, "_reader_status", lambda *_: "FULLY_EFFECTIVE")
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("../locale", None))
    assert not result.code and result.members[0].init_written
    saved = json.loads((tmp_path / ".lingtai/worker/init.json").read_bytes())
    assert saved["manifest"]["capabilities"]["skills"]["paths"] == [os.path.normpath(os.sep.join(["..", "..", library]))]


def _assert_existing_symlink_target_behavior(tmp_path, monkeypatch):
    write(tmp_path / ".recipe/recipe.json", {"name": "fixture", "library_name": "library"})
    write(tmp_path / ".recipe/greet/greet.md", b"hello")
    member = tmp_path / ".lingtai/worker"
    member.mkdir(parents=True)
    prompt_target = tmp_path / "prompt-target"
    init_target = tmp_path / "init-target"
    asset_target = tmp_path / "asset-target"
    write(prompt_target, b"old prompt")
    write(init_target, {"manifest": {"capabilities": {"skills": {}}}})
    init_before = init_target.read_bytes()
    asset_target.mkdir()
    try:
        (member / ".prompt").symlink_to(prompt_target)
        (member / "init.json").symlink_to(init_target)
        (tmp_path / ".lingtai/.tui-asset").symlink_to(asset_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip("platform fixture symlink privilege unavailable: " + type(exc).__name__)
    port = filesystem(tmp_path)
    monkeypatch.setattr(port, "_reader_status", lambda *_: "FULLY_EFFECTIVE")
    result = ProjectRecipeUseCase(port).apply(RecipeApplyRequest("", None))
    assert not result.code and result.snapshot.copied
    assert (member / ".prompt").is_symlink() and prompt_target.read_bytes() == b"hello"
    # AppendSkillsPath reads followed link, then replaces link via .tmp rename.
    assert not (member / "init.json").is_symlink() and init_target.read_bytes() == init_before
    assert (asset_target / ".recipe/recipe.json").is_file()


# Synchronous arbitrary-member rendering extension. Source only; parent executes.
from lingtai.adapters.project_recipe_exchange import (
    JsonRecipeRenderingAdapter, encode_bytes,
)
from lingtai.cli_project_recipe import read_recipe_request, stopped_payload
from lingtai.kernel.project.recipe import (
    RecipeExchangeStopped, RecipeGreetRenderingPort, RecipeRenderingStopped,
    RecipeMemberGreet, recipe_error,
)


class MemberRenderer(RecipeGreetRenderingPort):
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def render(self, member):
        self.calls.append(member)
        return self.callback(member)


def exchange_fixture(tmp_path, monkeypatch, *, template=b"\xff\x00original"):
    names = ("a", "b", "c", "human", ".dot")
    bundle = tmp_path / "bundle"
    destination = tmp_path / "separate-staging"
    write(bundle / ".recipe/recipe.json", {"name": "fixture", "library_name": "library"})
    write(bundle / ".recipe/greet/greet.md", template)
    for name in names:
        write(destination / name / ".prompt", b"old prompt")
        write(destination / name / "init.json", {"manifest": {"capabilities": {"skills": {}}}})
    write(destination / "file", b"not a directory")
    try:
        (destination / "symlink-dir").symlink_to(destination / "a", target_is_directory=True)
    except OSError:
        pass  # Windows privilege may be unavailable; separately report acceptance.
    port = FilesystemProjectRecipeAdapter(bundle, lingtai_dir=destination, load_preset=lambda *_: {})
    monkeypatch.setattr(port, "_reader_status", lambda *_: "FULLY_EFFECTIVE")
    return port, bundle, destination


def prompt_bytes(destination, name):
    return (destination / name / ".prompt").read_bytes()


def skills_paths(destination, name):
    return tuple(json.loads((destination / name / "init.json").read_bytes())["manifest"]["capabilities"]["skills"]["paths"])


def test_exchange_sequential_binary_empty_truncate_and_independent_destination(tmp_path, monkeypatch):
    import os
    port, bundle, destination = exchange_fixture(tmp_path, monkeypatch)
    raw = (bundle / ".recipe/greet/greet.md").read_bytes()
    outputs = (b"", b"\x00\xff\xfe", b"\x80\x00" * (1024 * 1024))
    original_enumeration = port.observe_directories
    enumeration_calls = []
    def enumerate_once():
        enumeration_calls.append(True)
        return original_enumeration()
    monkeypatch.setattr(port, "observe_directories", enumerate_once)
    def callback(member):
        index = member.sequence - 1
        name = ("a", "b", "c")[index]
        assert member.directory_name == os.fsencode(name) and member.template == raw
        assert prompt_bytes(destination, name) == b"old prompt"
        if index:
            preceding = ("a", "b")[index - 1]
            assert prompt_bytes(destination, preceding) == outputs[index - 1]
            assert skills_paths(destination, preceding)
        if index == 0:
            # Arbitrary callback changes future init/tree/Recipe. Enumeration
            # and raw greet stay captured; future skills are read sequentially.
            write(bundle / ".recipe/greet/greet.md", b"mutated")
            write(destination / "later/.prompt", b"foreign")
            write(destination / "b/init.json", {"manifest": {"capabilities": {"skills": {"paths": ["callback-path"]}}}})
        return outputs[index]
    renderer = MemberRenderer(callback)
    result = ProjectRecipeUseCase(port, renderer).apply(RecipeApplyRequest("", None))
    assert len(renderer.calls) == 3 and enumeration_calls == [True]
    assert [m.directory_name for m in result.members] == ["a", "b", "c"]
    assert all(m.prompt_written and m.init_written for m in result.members)
    assert skills_paths(destination, "b")[0] == "callback-path"
    for name, content in zip(("a", "b", "c"), outputs):
        assert prompt_bytes(destination, name) == content
    assert result.snapshot.copied and not result.code
    assert not (bundle / ".lingtai").exists()
    assert (destination / "later/.prompt").read_bytes() == b"foreign"


@pytest.mark.parametrize("state", ["identity", "empty", "unreadable", "missing", "no_members"])
def test_exchange_identity_and_original_empty_unreadable_missing_zero_calls(tmp_path, monkeypatch, state):
    port, bundle, destination = exchange_fixture(tmp_path, monkeypatch)
    original = b"\xff\x00original"
    if state in {"empty", "unreadable", "missing"}:
        monkeypatch.setattr(port, "observe_greet", lambda *_: {
            "empty": GreetLayer(True), "unreadable": GreetLayer(True, read_failed=True),
            "missing": GreetLayer(),
        }[state])
    if state == "no_members":
        monkeypatch.setattr(port, "observe_directories", lambda: ("human", ".dot"))
    renderer = MemberRenderer(lambda _: pytest.fail("unexpected rendering exchange"))
    result = ProjectRecipeUseCase(port, None if state == "identity" else renderer).apply(RecipeApplyRequest("", None))
    assert not renderer.calls and result.snapshot.attempted
    if state == "identity":
        assert all(prompt_bytes(destination, name) == original for name in ("a", "b", "c"))
    else:
        assert all(prompt_bytes(destination, name) == b"old prompt" for name in ("a", "b", "c"))


def test_exchange_second_metadata_race_nonfatal_empty_library(tmp_path, monkeypatch):
    port, bundle, destination = exchange_fixture(tmp_path, monkeypatch)
    original_load, original_greet = port.load_info, port.observe_greet
    events = []
    def load():
        events.append("metadata")
        if events.count("metadata") == 2:
            write(bundle / ".recipe/recipe.json", b"{changed-invalid")
            return original_load()
        return original_load()
    def greet(language):
        events.append("greet")
        return original_greet(language)
    monkeypatch.setattr(port, "load_info", load)
    monkeypatch.setattr(port, "observe_greet", greet)
    renderer = MemberRenderer(lambda member: member.template)
    result = ProjectRecipeUseCase(port, renderer).apply(RecipeApplyRequest("", None))
    assert events == ["metadata", "greet", "metadata"]
    assert not result.code and len(renderer.calls) == 3
    assert all(not m.init_written and m.prompt_written for m in result.members)


@pytest.mark.parametrize("phase", ["metadata", "enumeration"])
def test_exchange_pretraversal_failures_no_callback_snapshot(tmp_path, monkeypatch, phase):
    port, _, destination = exchange_fixture(tmp_path, monkeypatch)
    snapshot_calls = []
    monkeypatch.setattr(port, "replace_snapshot", lambda: snapshot_calls.append(True))
    method = "load_info" if phase == "metadata" else "observe_directories"
    monkeypatch.setattr(port, method, lambda: (_ for _ in ()).throw(recipe_error("invalid_recipe")))
    renderer = MemberRenderer(lambda _: pytest.fail("pretraversal callback"))
    with pytest.raises(ProjectCreationError):
        ProjectRecipeUseCase(port, renderer).apply(RecipeApplyRequest("", None))
    assert not renderer.calls and not snapshot_calls
    assert prompt_bytes(destination, "a") == b"old prompt"


@pytest.mark.parametrize("bad_ack", ["", "{bad PRIVATE}\n", '{"status":"recipe_member_rendered","sequence":9,"rendered_b64":"","rendered_bytes":0}\n'])
def test_exchange_eof_malformed_pending_stops_without_snapshot_prior_writes_survive(tmp_path, monkeypatch, bad_ack):
    port, _, destination = exchange_fixture(tmp_path, monkeypatch)
    ack = {"status": "recipe_member_rendered", "sequence": 1, "rendered_b64": "", "rendered_bytes": 0}
    output = io.StringIO()
    exchange = JsonRecipeRenderingAdapter(io.StringIO(json.dumps(ack) + "\n" + bad_ack), output)
    with pytest.raises(RecipeExchangeStopped) as failure:
        ProjectRecipeUseCase(port, exchange).apply(RecipeApplyRequest("", None))
    receipt = stopped_payload(failure.value)
    assert receipt["status"] == "recipe_exchange_stopped" and receipt["outcome"] == "partial"
    assert receipt["pending_sequence"] == 2 and receipt["pending_directory_name_b64"] == encode_bytes(b"b")
    assert receipt["legacy_applied"] == 1 and not receipt["snapshot"]["attempted"]
    assert prompt_bytes(destination, "a") == b"" and skills_paths(destination, "a")
    assert prompt_bytes(destination, "b") == b"old prompt"
    assert "PRIVATE" not in json.dumps(receipt)
    assert len(output.getvalue().splitlines()) == 2
    assert not port.snapshot.exists()


def test_exchange_growing_frames_ack_binary_and_source_bound_immutable_member():
    from dataclasses import FrozenInstanceError
    raw = b"\xff\x00" * (1024 * 1024)
    ack = {"status": "recipe_member_rendered", "sequence": 1,
           "rendered_b64": encode_bytes(raw), "rendered_bytes": len(raw)}
    out = io.StringIO()
    adapter = JsonRecipeRenderingAdapter(io.StringIO(json.dumps(ack) + "\n"), out)
    member = RecipeMemberGreet(1, b"\xffname", raw)
    with pytest.raises(FrozenInstanceError):
        member.template = b"changed"
    assert adapter.render(member) == raw
    frame = json.loads(out.getvalue())
    assert frame["template_bytes"] == len(raw) and frame["directory_name_b64"] == encode_bytes(b"\xffname")
    assert len(out.getvalue()) > 1024 * 1024
    # Sequence cannot be reused or skipped, including after a failed ack.
    with pytest.raises(RecipeRenderingStopped):
        adapter.render(member)


@pytest.mark.parametrize("change", [
    {"sequence": True}, {"rendered_bytes": True}, {"rendered_bytes": 1},
    {"rendered_b64": "Zg==", "rendered_bytes": 0},
    {"rendered_b64": "Zh==", "rendered_bytes": 1}, {"extra": "PRIVATE"},
])
def test_exchange_ack_closed_canonical_and_lengths(change):
    ack = {"status": "recipe_member_rendered", "sequence": 1, "rendered_b64": "", "rendered_bytes": 0}
    ack.update(change)
    adapter = JsonRecipeRenderingAdapter(io.StringIO(json.dumps(ack) + "\n"), io.StringIO())
    with pytest.raises(RecipeRenderingStopped):
        adapter.render(RecipeMemberGreet(1, b"a", b"raw"))
    assert adapter.stopped and adapter.pending.sequence == 1


def test_same_command_new_line_and_legacy_eof_multiline_caps():
    legacy = {"project_dir": "ROOT", "language": "", "rendering": None}
    for text in (json.dumps(legacy), json.dumps(legacy, indent=2) + "\n"):
        assert read_recipe_request(io.StringIO(text)) == (legacy, False)
    value = {"project_dir": "ROOT", "lingtai_dir": "STAGING", "language": "x" * (1024 * 1024),
             "rendering_mode": "identity"}
    # No EOF consumption for the new form; preserves following ack untouched.
    stream = io.StringIO(json.dumps(value) + "\nNEXT\n")
    assert read_recipe_request(stream) == (value, True)
    assert stream.readline() == "NEXT\n"
    with pytest.raises(ProjectCreationError):
        read_recipe_request(io.StringIO(json.dumps({**legacy, "language": "x" * (1024 * 1024)})))


def test_exchange_first_error_quirk_keeps_first_failure_reader_not_gate(tmp_path, monkeypatch):
    port, _, _ = exchange_fixture(tmp_path, monkeypatch)
    original_prompt = port.write_prompt
    from lingtai.kernel.project.recipe import RecipeFailure
    factual = RecipeFailure("prompt", "write", encode_bytes(b"native/a/.prompt"), 13)
    def failed_prompt(name, content):
        if name in ("a", "b"):
            return RecipeWrite(code="recipe_prompt_write_failed", failure=factual)
        return original_prompt(name, content)
    monkeypatch.setattr(port, "write_prompt", failed_prompt)
    monkeypatch.setattr(port, "write_skills", lambda *_: RecipeWrite(True, "READ_FAILED", "recipe_reader_failed"))
    monkeypatch.setattr(port, "replace_snapshot", lambda: RecipeSnapshotReceipt(True, False, True))
    renderer = MemberRenderer(lambda _: b"")
    result = ProjectRecipeUseCase(port, renderer).apply(RecipeApplyRequest("", None))
    payload = apply_payload(result, interactive=True)
    assert payload["legacy_applied"] == 2 and result.code == "recipe_prompt_write_failed"
    assert result.legacy_failure is factual and payload["legacy_failure"]["errno"] == 13
    assert not result.members[0].init_written and result.members[1].init_written
    assert len(renderer.calls) == 3 and result.snapshot.copied
    # Static ABI remains closed, including the exact old nested receipt fields.
    old = apply_payload(result)
    assert "legacy_failure" not in old and "directory_name_b64" not in old["members"][0]
    assert "failure" not in old["snapshot"]


def test_interactive_terminal_native_name_identity_and_failure_evidence(tmp_path, monkeypatch):
    import os
    if os.name == "nt":
        pytest.skip("POSIX arbitrary byte-name fixture; native Windows owner acceptance remains required")
    bundle = tmp_path / "bundle"
    destination = tmp_path / "destination"
    write(bundle / ".recipe/recipe.json", {"name": "fixture"})
    write(bundle / ".recipe/greet/greet.md", b"\xff\x00")
    destination.mkdir()
    names = (b"name\xff", b"name\xfe")
    for name in names:
        try:
            os.mkdir(os.fsencode(destination) + b"/" + name)
        except OSError as exc:
            import errno
            if exc.errno == errno.EILSEQ:
                pytest.skip("host filesystem rejects malformed byte names; Linux/native name acceptance remains required")
            raise
    value = {"project_dir": str(bundle), "lingtai_dir": str(destination), "language": "", "rendering_mode": "identity"}
    payload = execute("recipe-apply", value)
    assert payload["legacy_applied"] == 2
    assert {m["directory_name_b64"] for m in payload["members"]} == {encode_bytes(n) for n in names}
    assert json.loads(json.dumps(payload, ensure_ascii=True))["legacy_applied"] == 2
    # Known natural I/O evidence, not stringified exceptions or secret content.
    (destination / os.fsdecode(names[0]) / ".prompt").unlink()
    (destination / os.fsdecode(names[0]) / ".prompt").mkdir()
    failed = execute("recipe-apply", value)
    assert failed["legacy_failure"]["phase"] == "prompt" and failed["legacy_failure"]["errno"] is not None
    write(bundle / ".recipe/recipe.json", b'{"name": PRIVATE')
    failed = execute("recipe-apply", value)
    assert failed["legacy_failure"]["phase"] == "metadata"
    assert failed["legacy_failure"]["parse_offset"] is not None
    assert "PRIVATE" not in json.dumps(failed)


@pytest.mark.parametrize("stop_second", [False, True])
def test_actual_recipe_handler_synchronous_frames_flush_and_known_terminal(tmp_path, monkeypatch, stop_second):
    import base64
    from lingtai.cli_project_recipe import handle_recipe_command
    from lingtai.agent import Agent
    monkeypatch.setattr(Agent, "start", lambda *_: pytest.fail("no runtime"))
    bundle = tmp_path / "bundle"
    destination = tmp_path / "external"
    write(bundle / ".recipe/recipe.json", {"name": "fixture"})
    write(bundle / ".recipe/greet/greet.md", b"\xff\x00original")
    for name in ("a", "b"):
        write(destination / name / ".prompt", b"old")
    request = {"project_dir": str(bundle), "lingtai_dir": str(destination), "language": "",
               "rendering_mode": "callback"}
    class FlushedOutput(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flushes = 0
        def flush(self):
            self.flushes += 1
    output = FlushedOutput()
    errors = io.StringIO()
    class LiveIncoming:
        def __init__(self):
            self.lines = 0
        def read(self, *_):
            pytest.fail("new line form must not wait for EOF/read capped whole request")
        def readline(self):
            self.lines += 1
            if self.lines == 1:
                return json.dumps(request) + "\n"
            frame = json.loads(output.getvalue().splitlines()[-1])
            sequence = self.lines - 1
            assert frame["sequence"] == sequence and output.flushes == sequence
            assert base64.b64decode(frame["template_b64"]) == b"\xff\x00original"
            name = "a" if sequence == 1 else "b"
            assert (destination / name / ".prompt").read_bytes() == b"old"
            if sequence == 2:
                assert (destination / "a/.prompt").read_bytes() == b""
                if stop_second:
                    return ""  # models Go callback unwinding closing stdin
            rendered = b"" if sequence == 1 else b"\xff\x00" * (1024 * 1024)
            return json.dumps({"status": "recipe_member_rendered", "sequence": sequence,
                               "rendered_b64": encode_bytes(rendered), "rendered_bytes": len(rendered)}) + "\n"
    monkeypatch.setattr(sys, "stdin", LiveIncoming())
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(sys, "stderr", errors)
    if stop_second:
        with pytest.raises(SystemExit) as failure:
            handle_recipe_command("recipe-apply")
        assert failure.value.code == 1
        receipt = json.loads(errors.getvalue())
        assert receipt["status"] == "recipe_exchange_stopped" and receipt["pending_sequence"] == 2
        assert receipt["legacy_applied"] == 1 and not receipt["snapshot"]["attempted"]
        assert not (destination / ".tui-asset").exists()
        assert (destination / "b/.prompt").read_bytes() == b"old"
    else:
        handle_recipe_command("recipe-apply")
        receipt = json.loads(output.getvalue().splitlines()[-1])
        assert receipt["status"] == "recipe_applied" and receipt["legacy_applied"] == 2
        assert receipt["legacy_failure"] is None and receipt["snapshot"]["copied"]
        assert not errors.getvalue()
        assert (destination / "b/.prompt").read_bytes() == b"\xff\x00" * (1024 * 1024)
    assert (destination / "a/.prompt").read_bytes() == b""
    assert not (bundle / ".lingtai").exists()
