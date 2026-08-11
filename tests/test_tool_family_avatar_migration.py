"""Avatar's LTP v2 action-separated ToolFamily migration — focused evidence.

Covers the acceptance surface for the `avatar` family specifically: the composed
root/child schema and its wire correlation, dispatch of each action, the spawn
dry-run and its guard/error envelopes, the rules authorization/distribution
gate, manual's no-side-effect promise, cross-action input rejection *before* any
handler I/O, and the fact that the family wires one public tool without double
wrapping a child result.

Every test here builds its own isolated agent network under `tmp_path` and
monkeypatches the launcher Port, so no test creates a live avatar process and no
test writes a `.rules` signal outside its own temporary tree.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from lingtai.tools import avatar as avatar_tool
from lingtai.tools.avatar import AvatarManager
from lingtai.tools.tool_family import ToolFamily, ToolFamilyError
from lingtai.tools.tool_family.manual import MANUAL_INPUT_SCHEMA


# ---------------------------------------------------------------------------
# Isolated fixtures — no live avatar, no live network rules
# ---------------------------------------------------------------------------


class _FakeReceipt:
    def __init__(self) -> None:
        self.pid = 424242
        self.handle = object()


class _FakeLauncher:
    """Launcher Port double: records launches, never creates a process.

    ``launch`` writes the ``.agent.heartbeat`` handshake file the real child
    would write, so ``_wait_for_boot`` observes a clean ``ok`` boot without any
    sleeping or real process.
    """

    def __init__(self) -> None:
        self.launched: list[Any] = []

    def launch(self, request):
        self.launched.append(request)
        working_dir = Path(request.argv[-1])
        (working_dir / ".agent.heartbeat").write_text("{}", encoding="utf-8")
        return _FakeReceipt()

    def poll(self, _handle):
        return None

    def release(self, _handle):
        return None


class _StubAgent:
    """Minimal parent agent: a working dir, an admin block, and a name."""

    def __init__(self, working_dir: Path, *, admin: dict | None = None) -> None:
        self._working_dir = working_dir
        self._admin = admin or {}
        self.agent_name = working_dir.name
        self._venv_path = None
        self.added: dict[str, dict] = {}

    def add_tool(self, name: str, **kwargs) -> None:
        self.added[name] = kwargs


@pytest.fixture
def network(tmp_path: Path):
    """One isolated `.lingtai`-shaped network root with a parent agent in it."""
    root = tmp_path / "network"
    parent_dir = root / "parent"
    parent_dir.mkdir(parents=True)
    (parent_dir / "init.json").write_text(
        json.dumps({"manifest": {"agent_name": "parent", "language": "en"}, "lingtai": ""}),
        encoding="utf-8",
    )
    return parent_dir


@pytest.fixture
def launcher():
    return _FakeLauncher()


def _manager(parent_dir: Path, launcher: _FakeLauncher, *, admin: dict | None = None):
    agent = _StubAgent(parent_dir, admin=admin)
    return AvatarManager(agent, launcher=launcher), agent


# ---------------------------------------------------------------------------
# Schema: root envelope, per-action children, wire correlation
# ---------------------------------------------------------------------------


class TestAvatarFamilySchema:
    def test_root_is_exactly_action_input_reasoning_summarize(self):
        schema = avatar_tool.get_schema()
        assert schema["type"] == "object"
        assert set(schema["properties"]) == {"action", "input", "reasoning", "summarize"}
        assert schema["required"] == ["action", "input", "reasoning"]
        assert schema["additionalProperties"] is False
        # ``reasoning`` is required by the family itself, not left to Agent
        # schema composition (which only ever merges ``properties``).
        assert schema["properties"]["reasoning"]["type"] == "string"
        assert schema["properties"]["summarize"]["type"] == "boolean"

    def test_public_action_values_are_unchanged(self):
        schema = avatar_tool.get_schema()
        assert schema["properties"]["action"]["enum"] == ["spawn", "rules", "manual"]

    def test_each_action_owns_exactly_its_own_canonical_input(self):
        branches = {
            branch["title"]: branch
            for branch in avatar_tool.get_schema()["properties"]["input"]["oneOf"]
        }
        assert set(branches) == {"spawn input", "rules input", "manual input"}
        assert set(branches["spawn input"]["properties"]) == {
            "name", "type", "comment", "dry_run", "confirm",
        }
        assert set(branches["rules input"]["properties"]) == {"rules_content"}
        assert branches["manual input"]["properties"] == {}
        for branch in branches.values():
            assert branch["additionalProperties"] is False

    def test_no_child_input_leaks_reasoning_or_summarize(self):
        for branch in avatar_tool.get_schema()["properties"]["input"]["oneOf"]:
            assert not (
                {"reasoning", "_reasoning", "summarize"} & set(branch["properties"])
            ), branch["title"]

    def test_root_allof_correlates_each_action_const_with_its_exact_input(self):
        schema = avatar_tool.get_schema()
        conditions = schema["allOf"]
        assert len(conditions) == 3
        by_action = {c["if"]["properties"]["action"]["const"]: c for c in conditions}
        assert set(by_action) == {"spawn", "rules", "manual"}
        expected = dict(avatar_tool._CHILD_SPECS)
        for action, condition in by_action.items():
            assert condition["if"]["required"] == ["action"]
            assert condition["then"]["properties"]["input"] == expected[action]

    def test_spawn_optionals_are_nullable_but_name_is_a_plain_string(self):
        spawn = avatar_tool._SPAWN_INPUT_SCHEMA
        assert spawn["properties"]["name"]["type"] == "string"
        for optional in ("type", "comment", "dry_run", "confirm"):
            assert None in spawn["properties"][optional]["type"] or "null" in (
                spawn["properties"][optional]["type"]
            ), optional
        # Closed object: every declared field is required, optionality is
        # carried by the nullable type, per the strict-provider convention.
        assert spawn["required"] == ["name", "type", "comment", "dry_run", "confirm"]

    def test_schema_only_and_runtime_families_derive_from_one_spec(self, network, launcher):
        # Both listings are built by ``_build_family`` from ``_CHILD_SPECS``,
        # so they cannot drift in name, order, or input schema.
        mgr, _ = _manager(network, launcher)
        assert avatar_tool._FAMILY.child_names == mgr._family.child_names
        assert avatar_tool._FAMILY.build_schema() == mgr._family.build_schema()
        assert avatar_tool._FAMILY.build_schema() == avatar_tool.get_schema()

    def test_manual_child_uses_the_shared_generic_input_schema(self):
        # The strict-empty manual input is the one exported generic constant,
        # not a local restatement that could drift from it.
        assert dict(avatar_tool._CHILD_SPECS)["manual"] is MANUAL_INPUT_SCHEMA

class TestAvatarSchemaSurvivesBothWires:
    """The composed schema must correlate action→input on Chat and Responses.

    Chat Completions carries the schema through verbatim. The Responses wire
    rewrites a *nested* ``oneOf`` to ``anyOf`` while preserving the root
    ``allOf``, so the per-action correlation must survive on both wires even
    though the disclosure combinator is spelled differently.
    """

    def test_chat_completions_carries_the_schema_verbatim(self):
        schema = avatar_tool.get_schema()
        assert len(schema["properties"]["input"]["oneOf"]) == 3
        assert len(schema["allOf"]) == 3

    def test_responses_wire_preserves_root_allof_correlation(self):
        from lingtai.llm.openai.adapter import _scrub_responses_schema

        scrubbed = _scrub_responses_schema(avatar_tool.get_schema(), is_root=True)
        # Root ``allOf`` survives — this is what correlates action to input.
        assert len(scrubbed["allOf"]) == 3
        correlated = {
            condition["if"]["properties"]["action"]["const"]: condition["then"]["properties"]["input"]
            for condition in scrubbed["allOf"]
        }
        assert set(correlated) == {"spawn", "rules", "manual"}
        assert set(correlated["spawn"]["properties"]) == {
            "name", "type", "comment", "dry_run", "confirm",
        }
        assert set(correlated["rules"]["properties"]) == {"rules_content"}
        assert correlated["manual"]["properties"] == {}

    def test_responses_wire_rewrites_the_nested_disclosure_oneof_to_anyof(self):
        from lingtai.llm.openai.adapter import _scrub_responses_schema

        scrubbed = _scrub_responses_schema(avatar_tool.get_schema(), is_root=True)
        nested = scrubbed["properties"]["input"]
        assert "oneOf" not in nested
        assert len(nested["anyOf"]) == 3
        assert {branch["title"] for branch in nested["anyOf"]} == {
            "spawn input", "rules input", "manual input",
        }


# ---------------------------------------------------------------------------
# Dispatch: one action, one child, no double wrap
# ---------------------------------------------------------------------------


class TestAvatarDispatch:
    def test_setup_wires_exactly_one_public_tool_with_the_composed_schema(self):
        agent = MagicMock()
        avatar_tool.setup(agent)
        assert agent.add_tool.call_count == 1
        (name,), kwargs = agent.add_tool.call_args
        assert name == "avatar"
        assert kwargs["schema"] == avatar_tool.get_schema()
        assert kwargs["schema"]["required"] == ["action", "input", "reasoning"]

    def test_manual_result_is_not_double_wrapped(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({"action": "manual", "input": {}})
        # Avatar's own canonical flat result — no generic envelope nested
        # inside another action result.
        assert result["status"] == "ok"
        assert result["action"] == "manual"
        assert "content" not in result
        assert "structuredContent" not in result
        assert not isinstance(result.get("manual"), dict)

    def test_unknown_action_preserves_the_exact_legacy_error_envelope(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({"action": "bogus", "input": {}})
        assert result == {
            "error": "unknown action: 'bogus', only 'spawn', 'rules', or "
                     "'manual' is supported",
        }

    def test_omitted_action_preserves_the_exact_legacy_error_envelope(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        assert mgr.handle({})["error"] == (
            "unknown action: '', only 'spawn', 'rules', or 'manual' is supported"
        )

    def test_non_boolean_summarize_fails_before_any_action_runs(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "summarize": "yes",
        })
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_ARGUMENT"
        assert not (network.parent / "helper").exists()
        assert launcher.launched == []

    def test_summarize_never_reaches_a_child_handler(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        seen: list[dict] = []
        mgr._spawn = lambda args, reasoning=None: (seen.append(dict(args)), {"status": "ok"})[1]
        mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "summarize": True,
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert seen == [{"name": "helper", "confirm": True}]
        assert "summarize" not in seen[0]
        assert "_reasoning" not in seen[0]

    def test_avatar_is_on_the_kernel_summarize_allowlist(self):
        # A family advertising root ``summarize`` must actually be honored by
        # the single central summarizer, or the control is a lie. ``bash`` is
        # the legacy input alias for the migrated ``shell`` family and is
        # never itself on the allowlist, so it stands in for "unmigrated".
        from lingtai.kernel.tool_result_summary import summary_requested

        assert summary_requested({"summarize": True}, tool_name="avatar") is True
        assert summary_requested({"summarize": True}, tool_name="bash") is False


class TestCrossActionInputRejectedBeforeIO:
    """A key from another action's branch must fail before any handler I/O."""

    @pytest.mark.parametrize(
        "action,bad_input",
        [
            ("spawn", {"name": "helper", "rules_content": "No deleting."}),
            ("rules", {"rules_content": "No deleting.", "name": "helper"}),
            ("rules", {"rules_content": "No deleting.", "dry_run": True}),
            ("manual", {"name": "helper"}),
            ("manual", {"rules_content": "No deleting."}),
        ],
    )
    def test_cross_action_key_is_rejected_with_no_side_effects(
        self, network, launcher, action, bad_input,
    ):
        mgr, _ = _manager(network, launcher, admin={"karma": True})
        result = mgr.handle({
            "action": action,
            "input": bad_input,
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_ARGUMENT"
        assert result["message"] == "unsupported avatar input field"
        # No spawn, no rules, no ledger — the failure precedes all I/O.
        assert launcher.launched == []
        assert not (network / ".rules").exists()
        assert not (network / "delegates" / "ledger.jsonl").exists()
        assert not (network.parent / "helper").exists()

    def test_unknown_root_field_is_rejected_before_io(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "dir": "somewhere",
        })
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_ARGUMENT"
        assert launcher.launched == []
        assert not (network.parent / "helper").exists()

    def test_missing_input_object_is_rejected_before_io(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({"action": "spawn"})
        assert result["status"] == "failed"
        assert result["error_code"] == "INVALID_ARGUMENT"
        assert result["message"] == "input must be an object"
        assert launcher.launched == []


# ---------------------------------------------------------------------------
# Spawn: dry-run, mission guard, identity/path validation, receipts
# ---------------------------------------------------------------------------


class TestSpawnThroughTheEnvelope:
    def test_dry_run_previews_without_launching_or_writing(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "dry_run": True},
            "_reasoning": "Investigate the heartbeat regression and report back",
        })
        assert result["status"] == "dry_run"
        assert result["preview"]["name"] == "helper"
        assert result["preview"]["type"] == "shallow"
        assert "Investigate the heartbeat regression" in result["preview"]["mission"]
        assert launcher.launched == []
        assert not (network.parent / "helper").exists()
        assert not (network / "delegates" / "ledger.jsonl").exists()

    def test_dry_run_is_exempt_from_the_confirm_guard(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "dry_run": True},
            "_reasoning": "test",
        })
        assert result["status"] == "dry_run"
        assert result["preview"]["mission_unsafe"] is True

    def test_short_mission_trips_the_confirm_guard_without_launching(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper"},
            "_reasoning": "test",
        })
        assert result["status"] == "confirmation_needed"
        assert "confirm=true" in result["warning"]
        assert launcher.launched == []
        assert not (network.parent / "helper").exists()

    def test_confirm_overrides_the_mission_guard(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "_reasoning": "test",
        })
        assert result["status"] == "ok"
        assert len(launcher.launched) == 1

    def test_root_reasoning_becomes_the_avatar_first_prompt(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        mission = "Investigate the heartbeat regression and report back via mail"
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper"},
            "_reasoning": mission,
        })
        assert result["status"] == "ok"
        assert mission in (network.parent / "helper" / ".prompt").read_text(encoding="utf-8")

    def test_reasoning_does_not_leak_across_calls(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        mission = "Investigate the heartbeat regression and report back via mail"
        mgr.handle({"action": "spawn", "input": {"name": "first"}, "_reasoning": mission})
        # A second call with no reasoning must trip the mission guard rather
        # than silently inheriting the previous call's mission.
        second = mgr.handle({"action": "spawn", "input": {"name": "second"}})
        assert second["status"] == "confirmation_needed"
        assert not (network.parent / "second").exists()

    @pytest.mark.parametrize(
        "bad_name", ["../evil", "foo/bar", "/etc/hacked", ".hidden", "foo.bar", "", "a" * 65],
    )
    def test_identity_and_path_validation_survive_the_envelope(
        self, network, launcher, bad_name,
    ):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": bad_name, "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert "error" in result
        assert launcher.launched == []

    def test_deep_type_reaches_the_spawn_implementation(self, network, launcher):
        (network / "system").mkdir()
        (network / "system" / "character.md").write_text("who I am", encoding="utf-8")
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "clone", "type": "deep", "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert result["status"] == "ok"
        assert result["type"] == "deep"
        assert (network.parent / "clone" / "system" / "character.md").is_file()

    def test_shallow_default_does_not_copy_identity(self, network, launcher):
        (network / "system").mkdir()
        (network / "system" / "character.md").write_text("who I am", encoding="utf-8")
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert result["status"] == "ok"
        assert result["type"] == "shallow"
        assert not (network.parent / "helper" / "system").exists()

    def test_comment_reaches_the_newborn_init(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "comment": "never forget this", "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        init = json.loads((network.parent / "helper" / "init.json").read_text(encoding="utf-8"))
        assert init["comment"] == "never forget this"

    def test_spawn_receipt_and_ledger_are_preserved(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert result["status"] == "ok"
        assert result["address"] == "helper"
        assert result["agent_name"] == "helper"
        assert result["pid"] == 424242
        ledger = (network / "delegates" / "ledger.jsonl").read_text(encoding="utf-8")
        record = json.loads(ledger.strip())
        assert record["event"] == "avatar"
        assert record["name"] == "helper"
        assert record["boot_status"] == "ok"

    def test_spawn_never_requires_admin(self, network, launcher):
        mgr, _ = _manager(network, launcher, admin={})
        result = mgr.handle({
            "action": "spawn",
            "input": {"name": "helper", "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# Rules: authorization + distribution, on an isolated tree
# ---------------------------------------------------------------------------


class TestRulesThroughTheEnvelope:
    def test_rules_requires_karma(self, network, launcher):
        mgr, _ = _manager(network, launcher, admin={})
        result = mgr.handle({"action": "rules", "input": {"rules_content": "No deleting."}})
        assert "error" in result
        assert "admin privilege required" in result["error"]
        assert not (network / ".rules").exists()

    def test_rules_requires_content(self, network, launcher):
        mgr, _ = _manager(network, launcher, admin={"karma": True})
        result = mgr.handle({"action": "rules", "input": {"rules_content": "   "}})
        assert "error" in result
        assert not (network / ".rules").exists()

    def test_authorized_rules_write_self_signal(self, network, launcher):
        mgr, _ = _manager(network, launcher, admin={"karma": True})
        result = mgr.handle({"action": "rules", "input": {"rules_content": "Be concise."}})
        assert result["status"] == "ok"
        assert (network / ".rules").read_text() == "Be concise."
        assert result["distributed_to"] == ["parent"]

    def test_rules_distribute_to_descendants_in_the_isolated_tree(self, network, launcher):
        # Spawn a real (faked-launch) child so the ledger is genuine, then
        # distribute — both inside this test's own temp network only.
        mgr, _ = _manager(network, launcher, admin={"karma": True})
        spawned = mgr.handle({
            "action": "spawn",
            "input": {"name": "child", "confirm": True},
            "_reasoning": "a genuine mission brief, long enough to pass",
        })
        assert spawned["status"] == "ok"

        result = mgr.handle({"action": "rules", "input": {"rules_content": "Be kind."}})
        assert result["status"] == "ok"
        assert set(result["distributed_to"]) == {"parent", "child"}
        assert (network.parent / "child" / ".rules").read_text() == "Be kind."


# ---------------------------------------------------------------------------
# Manual: no spawn/rules I/O, exact body and path
# ---------------------------------------------------------------------------


class TestManualThroughTheEnvelope:
    def test_manual_returns_the_exact_packaged_body_and_path(self, network, launcher):
        mgr, _ = _manager(network, launcher)
        result = mgr.handle({"action": "manual", "input": {}})

        source = (
            Path(avatar_tool.__file__).resolve().parent / "manual" / "SKILL.md"
        )
        assert result["status"] == "ok"
        assert result["action"] == "manual"
        assert result["manual"] == source.read_text(encoding="utf-8")
        assert Path(result["manual_path"]) == source

    def test_manual_performs_no_spawn_or_rules_io(self, network, launcher):
        mgr, _ = _manager(network, launcher, admin={"karma": True})
        before = sorted(p.name for p in network.parent.iterdir())

        result = mgr.handle({"action": "manual", "input": {}})
        assert result["status"] == "ok"

        assert launcher.launched == []
        assert not (network / ".rules").exists()
        assert not (network / "delegates").exists()
        assert not (network / "logs").exists()
        assert sorted(p.name for p in network.parent.iterdir()) == before

    def test_missing_packaged_manual_degrades_truthfully(self, network, launcher, monkeypatch):
        mgr, _ = _manager(network, launcher)

        class _Missing:
            def joinpath(self, _name):
                return self

            def read_text(self, **_kwargs):
                raise FileNotFoundError("gone")

            def __str__(self) -> str:
                return "<missing avatar manual>"

        monkeypatch.setattr(
            avatar_tool.resources, "files", lambda _pkg: _Missing(),
        )
        result = mgr.handle({"action": "manual", "input": {}})
        assert result["status"] == "degraded"
        assert result["manual"] == ""
        assert result["error"] == "avatar manual missing"
        assert result["manual_path"] == "<missing avatar manual>"
