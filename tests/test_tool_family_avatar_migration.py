"""Focused declared-host-plugin coverage for Avatar's existing public family."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.kernel.tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES, ToolPluginHost
from lingtai.tools import avatar
from lingtai.tools.avatar import AvatarManager
from lingtai.tools.avatar._launcher import AvatarLaunchReceipt
from lingtai.tools.avatar.settings import (
    AVATAR_NAME_MAX_CHARACTERS,
    AVATAR_NAME_MIN_CHARACTERS,
    BOOT_POLL_INTERVAL_SECONDS,
    BOOT_STDERR_TAIL_BYTES,
    BOOT_WAIT_SECONDS,
    MISSION_MIN_CHARACTERS,
    MISSION_PLACEHOLDER_PREFIXES,
    SPAWN_COMMENT_DEFAULT,
    SPAWN_CONFIRM_DEFAULT,
    SPAWN_DRY_RUN_DEFAULT,
    SPAWN_TYPE_DEFAULT,
    SPAWN_TYPES,
)
from lingtai.tools.psyche import settings as psyche_settings


class _Workdir:
    def __init__(self, path: Path) -> None:
        self.path = path


class _AvatarParent:
    def __init__(
        self, *, name: str = "parent", venv_path: str | None = None, rules: bool = False
    ) -> None:
        self.parent_name = name
        self.venv_path = venv_path
        self._rules = rules

    def has_rule_privilege(self) -> bool:
        return self._rules

    def authorize_derived_launch(self, _capability):
        from lingtai.kernel.provider_admission import (
            DerivedLaunchDecision,
            ProviderAdmissionState,
        )

        return DerivedLaunchDecision(ProviderAdmissionState.GRANTED, "test_grant")


class _Launcher:
    def release(self, _handle) -> None:
        return None


def _host(parent_dir: Path, *, rules: bool = False, venv_path: str | None = None):
    return ToolPluginHost.grant(
        avatar.DECLARATION,
        {
            "workdir": _Workdir(parent_dir),
            "avatar_parent": _AvatarParent(venv_path=venv_path, rules=rules),
        },
    )


def _parent_dir(tmp_path: Path) -> Path:
    parent_dir = tmp_path / "network" / "parent"
    parent_dir.mkdir(parents=True)
    (parent_dir / "init.json").write_text(
        json.dumps({"manifest": {"agent_name": "parent", "language": "en"}}),
        encoding="utf-8",
    )
    return parent_dir


def test_avatar_declaration_is_static_and_matches_its_composed_public_surface():
    declaration = avatar.DECLARATION

    assert declaration.name == "avatar"
    assert declaration.actions == ("spawn", "rules")
    assert declaration.public_actions == ("spawn", "rules", "settings", "manual")
    assert declaration.settings is True
    assert declaration.requires == ("workdir", "avatar_parent")
    assert declaration.name in OFFICIAL_TOOL_PLUGIN_NAMES
    assert avatar.get_schema()["properties"]["action"]["enum"] == list(
        declaration.public_actions
    )
    assert dict(avatar._CHILD_SPECS)["manual"] is declaration.manual_input_schema


def test_avatar_settings_inventory_is_exact_fresh_and_excludes_private_state(
    tmp_path,
):
    parent_dir = _parent_dir(tmp_path)
    private_parent = "private-parent-identity"
    private_runtime = "/private/runtime/location"
    host = ToolPluginHost.grant(
        avatar.DECLARATION,
        {
            "workdir": _Workdir(parent_dir),
            "avatar_parent": _AvatarParent(
                name=private_parent,
                venv_path=private_runtime,
                rules=True,
            ),
        },
    )
    manager = AvatarManager(host, launcher=_Launcher())

    result = manager(
        {"action": "settings", "input": {}, "reasoning": "inventory policy"}
    )

    def row(key, current, default, comment):
        return {
            "key": key,
            "current": current,
            "default": default,
            "configurable": False,
            "comment": comment,
        }

    call_defaults = "avatar-manual#spawn-call-defaults"
    validation = "avatar-manual#spawn-validation-policy"
    lifecycle = "avatar-manual#spawn-lifecycle-policy"
    expected_rows = [
        row(
            "spawn.type.default",
            SPAWN_TYPE_DEFAULT,
            SPAWN_TYPE_DEFAULT,
            call_defaults,
        ),
        row("spawn.type.allowed", list(SPAWN_TYPES), list(SPAWN_TYPES), validation),
        row(
            "spawn.comment.default",
            SPAWN_COMMENT_DEFAULT,
            SPAWN_COMMENT_DEFAULT,
            call_defaults,
        ),
        row(
            "spawn.dry_run.default",
            SPAWN_DRY_RUN_DEFAULT,
            SPAWN_DRY_RUN_DEFAULT,
            call_defaults,
        ),
        row(
            "spawn.confirm.default",
            SPAWN_CONFIRM_DEFAULT,
            SPAWN_CONFIRM_DEFAULT,
            call_defaults,
        ),
        row(
            "spawn.name.minimum_characters",
            AVATAR_NAME_MIN_CHARACTERS,
            AVATAR_NAME_MIN_CHARACTERS,
            validation,
        ),
        row(
            "spawn.name.maximum_characters",
            AVATAR_NAME_MAX_CHARACTERS,
            AVATAR_NAME_MAX_CHARACTERS,
            validation,
        ),
        row(
            "spawn.mission.minimum_characters",
            MISSION_MIN_CHARACTERS,
            MISSION_MIN_CHARACTERS,
            validation,
        ),
        row(
            "spawn.mission.placeholder_prefixes",
            sorted(MISSION_PLACEHOLDER_PREFIXES),
            sorted(MISSION_PLACEHOLDER_PREFIXES),
            validation,
        ),
        row(
            "spawn.boot.wait_seconds",
            BOOT_WAIT_SECONDS,
            BOOT_WAIT_SECONDS,
            lifecycle,
        ),
        row(
            "spawn.boot.poll_interval_seconds",
            BOOT_POLL_INTERVAL_SECONDS,
            BOOT_POLL_INTERVAL_SECONDS,
            lifecycle,
        ),
        row(
            "spawn.boot.stderr_tail_bytes",
            BOOT_STDERR_TAIL_BYTES,
            BOOT_STDERR_TAIL_BYTES,
            lifecycle,
        ),
        row("spawn.preset_policy", "parent-default", "parent-default", lifecycle),
        row(
            "spawn.environment_policy",
            "inherit-launcher-process",
            "inherit-launcher-process",
            lifecycle,
        ),
        row(
            "spawn.lifecycle_policy",
            "detached-independent",
            "detached-independent",
            lifecycle,
        ),
        row("spawn.admin_inheritance", "none", "none", lifecycle),
    ]

    assert result == {"settings": expected_rows}
    assert all(
        list(item) == ["key", "current", "default", "configurable", "comment"]
        for item in result["settings"]
    )
    assert len({item["key"] for item in result["settings"]}) == len(expected_rows)
    forbidden_keys = {
        "rules.authorization_policy",
        "rules.authorized",
        "spawn.parent_identity",
        "runtime.venv_path",
        "configuration.capability_arguments",
    }
    assert forbidden_keys.isdisjoint(item["key"] for item in result["settings"])
    assert private_parent not in repr(result)
    assert private_runtime not in repr(result)

    result["settings"][1]["current"].append("mutated-display-copy")
    fresh = manager({"action": "settings", "input": {}})
    assert fresh == {"settings": expected_rows}

    manual = (Path(avatar.__file__).parent / "manual" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    headings = {
        call_defaults: "Spawn call defaults",
        validation: "Spawn validation policy",
        lifecycle: "Spawn lifecycle policy",
    }
    for comment in {item["comment"] for item in fresh["settings"]}:
        assert f"### {headings[comment]}" in manual


def test_avatar_settings_is_show_only_and_has_no_environment_peer(
    tmp_path,
    monkeypatch,
):
    parent_dir = _parent_dir(tmp_path)
    manager = AvatarManager(_host(parent_dir), launcher=_Launcher())
    monkeypatch.setenv("LINGTAI_AVATAR_BOOT_WAIT_SECONDS", "99")
    before = {
        path.relative_to(parent_dir): path.read_bytes()
        for path in parent_dir.rglob("*")
        if path.is_file()
    }

    shown = manager({"action": "settings", "input": {}})
    rejected = [
        manager({"action": "settings", "input": value})
        for value in (
            {"set": "spawn.type.default", "value": "deep"},
            {"reset": "spawn.boot.wait_seconds"},
            {"extra": True},
        )
    ]

    rows = {item["key"]: item for item in shown["settings"]}
    assert rows["spawn.boot.wait_seconds"]["current"] == BOOT_WAIT_SECONDS
    assert all(
        result
        == {
            "status": "failed",
            "error_code": "INVALID_ARGUMENT",
            "message": "unsupported avatar input field",
        }
        for result in rejected
    )
    after = {
        path.relative_to(parent_dir): path.read_bytes()
        for path in parent_dir.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (parent_dir / "settings").exists()


def test_avatar_settings_provider_failure_is_one_bounded_result():
    def unavailable():
        raise RuntimeError("private provider detail")

    family = avatar._build_family(
        {"spawn": lambda _input: {}, "rules": lambda _input: {}},
        settings_provider=unavailable,
    )

    assert family.handle({"action": "settings", "input": {}}) == {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }


def test_avatar_manager_uses_only_granted_ports_for_local_manual_and_rules(tmp_path):
    parent_dir = _parent_dir(tmp_path)
    host = _host(parent_dir)
    manager = AvatarManager(host, launcher=_Launcher())

    with pytest.raises(AttributeError, match="did not require host port"):
        host.prompt_section

    before = sorted(path.name for path in parent_dir.iterdir())
    manual = manager({"action": "manual", "input": {}})
    source = Path(avatar.__file__).resolve().parent / "manual" / "SKILL.md"
    assert manual == {
        "status": "ok",
        "action": "manual",
        "manual": source.read_text(encoding="utf-8"),
        "manual_path": str(source),
    }
    assert sorted(path.name for path in parent_dir.iterdir()) == before

    denied = manager({"action": "rules", "input": {"rules_content": "No deleting."}})
    assert denied == {"error": "Not authorized — admin privilege required to set rules"}
    assert not (parent_dir / ".rules").exists()


def test_avatar_spawn_preserves_workdir_identity_venv_and_rules_control(tmp_path, monkeypatch):
    parent_dir = _parent_dir(tmp_path)
    host = _host(parent_dir, rules=True, venv_path="/parent/runtime")
    manager = AvatarManager(host, launcher=_Launcher())
    receipt = AvatarLaunchReceipt(pid=4242, handle=object())
    monkeypatch.setattr(manager, "_launch", lambda working_dir, **_kwargs: (receipt, working_dir / "stderr"))
    monkeypatch.setattr(manager, "_wait_for_boot", lambda *_args: ("ok", None))

    dry_run = manager(
        {
            "action": "spawn",
            "input": {"name": "preview", "dry_run": True},
            "_reasoning": "Inspect the regression and summarize the evidence.",
        }
    )
    assert dry_run["status"] == "dry_run"
    assert not (parent_dir.parent / "preview").exists()

    spawned = manager(
        {
            "action": "spawn",
            "input": {"name": "child", "confirm": True},
            "_reasoning": "Inspect the regression and summarize the evidence.",
        }
    )
    child_dir = parent_dir.parent / "child"
    assert spawned["status"] == "ok"
    assert "parent" in (child_dir / ".prompt").read_text(encoding="utf-8")
    assert json.loads((child_dir / "init.json").read_text(encoding="utf-8"))["venv_path"] == "/parent/runtime"

    rules = manager({"action": "rules", "input": {"rules_content": "Be concise."}})
    assert rules["distributed_to"] == ["parent", "child"]
    assert (parent_dir / ".rules").read_text(encoding="utf-8") == "Be concise."
    assert (child_dir / ".rules").read_text(encoding="utf-8") == "Be concise."


@pytest.mark.parametrize("avatar_type", ["shallow", "deep"])
def test_avatar_spawn_carries_only_psyche_prompt_owner_for_both_modes(
    tmp_path, monkeypatch, avatar_type,
):
    parent_dir = _parent_dir(tmp_path)
    base_source = parent_dir / "relative-base.md"
    base_source.write_text("PARENT BASE", encoding="utf-8")
    psyche = parent_dir / "settings" / "psyche.json"
    psyche.parent.mkdir()
    psyche.write_text(json.dumps({
        "schema_version": 1,
        "base_prompt": "base fallback",
        "base_prompt_file": base_source.name,
        "covenant": "PARENT COVENANT",
        "comment": "PARENT COMMENT",
        "comment_file": "parent-comment.md",
    }), encoding="utf-8")
    system_policy = parent_dir / "settings" / "system.json"
    system_policy.write_text(json.dumps({"schema_version": 2, "max_rpm": 3}), encoding="utf-8")

    manager = AvatarManager(_host(parent_dir), launcher=_Launcher())
    receipt = AvatarLaunchReceipt(pid=4242, handle=object())
    monkeypatch.setattr(
        manager,
        "_launch",
        lambda working_dir, **_kwargs: (receipt, working_dir / "stderr"),
    )
    monkeypatch.setattr(manager, "_wait_for_boot", lambda *_args: ("ok", None))
    result = manager({
        "action": "spawn",
        "input": {
            "name": f"{avatar_type}-child",
            "type": avatar_type,
            "comment": "CHILD COMMENT",
            "confirm": True,
        },
        "_reasoning": "Inspect the owner-document inheritance behavior carefully.",
    })
    assert result["status"] == "ok"
    child = parent_dir.parent / f"{avatar_type}-child"
    assert json.loads((child / "settings" / "psyche.json").read_text(encoding="utf-8")) == {
        "schema_version": 1,
        "base_prompt": "base fallback",
        "base_prompt_file": str(base_source),
        "covenant": "PARENT COVENANT",
        "comment": "CHILD COMMENT",
    }
    assert (child / "settings" / "psyche.json").read_text(encoding="utf-8") == (
        psyche_settings.serialize_prompt_owner_document(
            base_prompt="base fallback",
            base_prompt_file=str(base_source),
            covenant="PARENT COVENANT",
            comment="CHILD COMMENT",
        )
    )
    assert not (child / "settings" / "system.json").exists()


def test_avatar_manual_states_the_real_spawn_comment_prompt_position() -> None:
    from lingtai.kernel.prompt import SystemPromptManager

    manual = (Path(avatar.__file__).parent / "manual" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    order = SystemPromptManager._DEFAULT_ORDER

    assert "rendered last, after memory" not in manual
    assert "after `meta_guidance` and before `rules`" in manual
    assert order.index("meta_guidance") < order.index("comment") < order.index("rules")


def test_agent_mounts_avatar_only_through_the_official_registrar(tmp_path):
    from lingtai.agent import Agent
    from tests._service_helpers import make_gemini_mock_service

    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="avatar-plugin",
        working_dir=tmp_path / "agent",
        capabilities={"avatar": {}},
    )
    try:
        assert agent.official_tool_plugins["avatar"] is avatar.DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("avatar") == 1
        assert agent.get_capability("avatar") is agent._tool_handlers["avatar"]
        assert isinstance(agent.get_capability("avatar"), AvatarManager)
        assert agent._build_system_prompt()
    finally:
        agent.stop(timeout=1.0)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"action": "spawn"}, "input must be an object"),
        ({"action": "spawn", "input": "not-an-object"}, "input must be an object"),
        (
            {
                "action": "spawn",
                "input": {"name": "child", "confirm": True},
                "dir": "somewhere-else",
            },
            "unsupported avatar argument",
        ),
        (
            {
                "action": "spawn",
                "input": {"name": "child", "rules_content": "No deleting."},
            },
            "unsupported avatar input field",
        ),
    ],
)
def test_avatar_rejects_invalid_input_object_or_root_before_any_io(tmp_path, args, message):
    """The declared family still validates its closed root/input before handlers."""
    parent_dir = _parent_dir(tmp_path)
    manager = AvatarManager(_host(parent_dir), launcher=_Launcher())

    result = manager.handle(args)

    assert result == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": message,
    }
    assert not (parent_dir.parent / "child").exists()
    assert not (parent_dir / ".rules").exists()
    assert not (parent_dir / "delegates" / "ledger.jsonl").exists()


@pytest.mark.parametrize("summarize", ["yes", 1, None])
def test_avatar_rejects_non_boolean_summarize_before_any_action_io(tmp_path, summarize):
    """The root presentation control is strict and never reaches Avatar actions."""
    parent_dir = _parent_dir(tmp_path)
    manager = AvatarManager(_host(parent_dir), launcher=_Launcher())

    result = manager.handle(
        {
            "action": "spawn",
            "input": {"name": "child", "confirm": True},
            "summarize": summarize,
        }
    )

    assert result == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "summarize must be a boolean",
    }
    assert not (parent_dir.parent / "child").exists()
    assert not (parent_dir / "delegates" / "ledger.jsonl").exists()


def test_avatar_clears_normalized_reasoning_between_dispatches(tmp_path):
    """ToolExecutor-normalized reasoning is a one-call mission, never ambient state."""
    parent_dir = _parent_dir(tmp_path)
    manager = AvatarManager(_host(parent_dir), launcher=_Launcher())
    mission = "Investigate the heartbeat regression and report the evidence."

    first = manager.handle(
        {
            "action": "spawn",
            "input": {"name": "first", "dry_run": True},
            "_reasoning": mission,
        }
    )
    second = manager.handle({"action": "spawn", "input": {"name": "second"}})

    assert first["status"] == "dry_run"
    assert first["preview"]["mission"] == mission
    assert second["status"] == "confirmation_needed"
    assert second["preview"]["mission"] == ""
    assert not (parent_dir.parent / "second").exists()


def test_avatar_missing_packaged_manual_degrades_truthfully(tmp_path, monkeypatch):
    """Avatar's declaration promises a package-local manual; no host fallback is hidden."""
    parent_dir = _parent_dir(tmp_path)
    manager = AvatarManager(_host(parent_dir), launcher=_Launcher())

    class _MissingPackageResource:
        def joinpath(self, _path):
            return self

        def read_text(self, *, encoding):
            raise FileNotFoundError("simulated missing package resource")

        def __str__(self):
            return "missing-avatar-package-resource"

    monkeypatch.setattr(avatar.resources, "files", lambda _package: _MissingPackageResource())

    result = manager.handle({"action": "manual", "input": {}})

    assert result == {
        "status": "degraded",
        "action": "manual",
        "manual": "",
        "manual_path": "missing-avatar-package-resource",
        "error": "avatar manual missing",
    }
    assert not (parent_dir / ".rules").exists()
    assert not (parent_dir / "delegates" / "ledger.jsonl").exists()
