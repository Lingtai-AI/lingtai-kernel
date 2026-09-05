"""Tests for .rules signal consumption and system/rules.md persistence."""
import json
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from lingtai.tools.avatar import AvatarManager

import pytest
from tests._service_helpers import make_gemini_mock_service as make_mock_service


def _fake_launch_return(pid: int = 12345):
    """Build a (proc, stderr_path) tuple matching ``AvatarManager._launch``'s
    new signature. The proc.pid attribute is the only field consumers read."""
    proc = MagicMock()
    proc.pid = pid
    proc.poll.return_value = None  # still running
    return (proc, Path("/tmp/avatar_stderr.log"))


@contextmanager
def _patch_avatar_launch(*, boot_status: str = "ok", boot_error=None):
    """Context manager: patches both _launch and _wait_for_boot so spawn-path
    tests don't actually fork a child process. Yields the launch mock so
    assertion-based tests can inspect call counts / args."""
    with patch.object(AvatarManager, "_launch", return_value=_fake_launch_return()) as launch_mock, \
         patch.object(AvatarManager, "_wait_for_boot", return_value=(boot_status, boot_error)):
        yield launch_mock


class TestRulesHeartbeatWatch:
    """Test that the heartbeat loop consumes .rules signal and persists to system/rules.md."""

    def _make_agent(self, tmp_path):
        from lingtai.agent import Agent

        svc = MagicMock()
        svc.get_adapter.return_value = MagicMock()
        svc.provider = "gemini"
        svc.model = "gemini-test"
        wd = tmp_path / "agent"
        agent = Agent(service=svc, agent_name="test", working_dir=wd)
        return agent

    def test_rules_signal_consumed_and_persisted(self, tmp_path):
        """Writing .rules should: inject section, persist to system/rules.md, delete .rules."""
        agent = self._make_agent(tmp_path)
        wd = agent._working_dir

        # No rules section initially
        assert agent._prompt_manager.read_section("rules") is None

        # Write .rules signal file
        (wd / ".rules").write_text("No deleting files.\nAlways log actions.")

        # Simulate one heartbeat tick
        agent._check_rules_file()

        # Section injected
        assert agent._prompt_manager.read_section("rules") == "No deleting files.\nAlways log actions."
        # Persisted to system/rules.md
        assert (wd / "system" / "rules.md").read_text() == "No deleting files.\nAlways log actions."
        # Signal file consumed (deleted)
        assert not (wd / ".rules").is_file()

    def test_rules_diff_skips_identical(self, tmp_path):
        """If .rules content matches system/rules.md, no prompt refresh."""
        agent = self._make_agent(tmp_path)
        wd = agent._working_dir

        # Pre-load rules into section and canonical file
        agent._prompt_manager.write_section("rules", "No deleting files.", protected=True)
        system_dir = wd / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        (system_dir / "rules.md").write_text("No deleting files.")

        # Write identical .rules signal
        (wd / ".rules").write_text("No deleting files.")

        with patch.object(agent, "_flush_system_prompt") as mock_flush:
            agent._check_rules_file()
            mock_flush.assert_not_called()

        # Signal still consumed even if content is identical
        assert not (wd / ".rules").is_file()

    def test_rules_diff_refreshes_on_change(self, tmp_path):
        """If .rules content differs from system/rules.md, prompt is refreshed."""
        agent = self._make_agent(tmp_path)
        wd = agent._working_dir

        # Pre-load old rules
        agent._prompt_manager.write_section("rules", "Old rules.", protected=True)
        system_dir = wd / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        (system_dir / "rules.md").write_text("Old rules.")

        # Write new .rules signal
        (wd / ".rules").write_text("New rules.")

        with patch.object(agent, "_flush_system_prompt") as mock_flush:
            agent._check_rules_file()
            mock_flush.assert_called_once()

        assert agent._prompt_manager.read_section("rules") == "New rules."
        assert (system_dir / "rules.md").read_text() == "New rules."
        assert not (wd / ".rules").is_file()

    def test_rules_loaded_from_system_on_init(self, tmp_path):
        """If system/rules.md exists at agent start, rules section should be pre-loaded."""
        wd = tmp_path / "agent"
        system_dir = wd / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        (system_dir / "rules.md").write_text("Pre-existing rules.")

        from lingtai.agent import Agent
        svc = MagicMock()
        svc.get_adapter.return_value = MagicMock()
        svc.provider = "gemini"
        svc.model = "gemini-test"
        agent = Agent(service=svc, agent_name="test", working_dir=wd)

        # Rules should be loaded from system/rules.md during init
        assert agent._prompt_manager.read_section("rules") == "Pre-existing rules."

    def test_rules_unlink_failure_skips_processing(self, tmp_path, monkeypatch):
        """If .rules cannot be unlinked, the function should return WITHOUT calling flush."""
        agent = self._make_agent(tmp_path)
        wd = agent._working_dir
        (wd / ".rules").write_text("Some rules.")

        # Make Path.unlink raise OSError
        original_unlink = Path.unlink
        def failing_unlink(self, *args, **kwargs):
            if self.name == ".rules":
                raise PermissionError("simulated unlink failure")
            return original_unlink(self, *args, **kwargs)
        monkeypatch.setattr(Path, "unlink", failing_unlink)

        with patch.object(agent, "_flush_system_prompt") as mock_flush:
            agent._check_rules_file()
            mock_flush.assert_not_called()
        # File should still exist (we couldn't unlink it)
        assert (wd / ".rules").is_file()




class TestAvatarRulesActionRemoved:
    """Avatar no longer owns a dedicated rules-distribution action.

    Option B (see avatar CONTRACT.md contract_version 9): the admin-gated
    `action="rules"` child and its fan-out were removed entirely rather than
    replaced by a new guard. `.rules` remains a real, unchanged heartbeat
    signal (see ``TestRulesHeartbeatWatch`` above and ``psyche-manual``); any
    agent may still write one to an explicitly targeted path itself (e.g. via
    `shell`), but Avatar performs no such write and enforces no privilege
    check for it.
    """

    def test_rules_is_an_unknown_action(self, tmp_path):
        """action='rules' now fails the same way as any other unknown action."""
        from lingtai.agent import Agent

        agent = Agent(
            service=make_mock_service(),
            agent_name="admin",
            working_dir=tmp_path / "admin",
            capabilities=["avatar"],
            admin={"karma": True},
        )
        mgr = agent.get_capability("avatar")
        result = mgr.handle({
            "action": "rules",
            "input": {"rules_content": "Always log actions."},
        })
        assert result == {
            "error": (
                "unknown action: 'rules', only 'spawn', 'settings', "
                "or 'manual' is supported"
            ),
        }
        assert not (agent._working_dir / ".rules").exists()

    def test_explicit_spawn_action_required(self, tmp_path):
        """action='spawn' must be explicit; omitting action must NOT spawn.

        NOTE: Real spawning launches a subprocess. We patch _launch to avoid
        that, and pre-create init.json so _spawn reaches the launch path.

        Historical intent: this test formerly proved that an omitted
        'action' defaulted to spawn (a back-compat shorthand from the
        avatar_spawn/avatar_rules two-tool era). That shorthand was removed
        so the unified 'avatar' tool matches the schema-and-runtime-required
        'action' contract every other canonical action tool (knowledge, mcp,
        skills, notification, system, soul, daemon) already follows — see
        avatar CONTRACT.md contract_version 4.
        """
        from lingtai.agent import Agent

        parent_dir = tmp_path / "parent"
        agent = Agent(
            service=make_mock_service(),
            agent_name="parent",
            working_dir=parent_dir,
            capabilities=["avatar"],
        )

        # _spawn requires parent to have init.json
        (parent_dir / "init.json").write_text(
            json.dumps({"manifest": {"agent_name": "parent", "admin": {}}})
        )

        mgr = agent.get_capability("avatar")
        with _patch_avatar_launch() as launch:
            # Omitted action must fail deterministically and spawn nothing.
            omitted = mgr.handle({"name": "child", "confirm": True})
            assert "error" in omitted
            assert omitted["error"] == (
                "unknown action: '', only 'spawn', 'settings', "
                "or 'manual' is supported"
            )
            launch.assert_not_called()
            assert not (parent_dir.parent / "child").exists()

            # Explicit action='spawn' behaves exactly as before.
            result = mgr.handle({"action": "spawn", "input": {"name": "child", "confirm": True}})
        assert result["status"] == "ok"
        assert result["agent_name"] == "child"
        assert result["address"] == "child"  # relative name (current convention)

class TestSpawnNoAutoRulesDistribution:
    """Avatar's post-spawn automatic rules fan-out is removed (Option B).

    Ordinary deep-copy behavior is unchanged: a deep clone still gets
    `system/rules.md` because `_prepare_deep` copies the whole `system/`
    tree, exactly as before. What is gone is the dedicated read-canonical-
    then-write-`.rules`-signal step that used to run after every successful
    spawn — shallow or deep — regardless of copy mode.
    """

    def _setup_spawnable_parent(self, tmp_path, with_rules: bool):
        """Build a parent agent with init.json, optionally with system/rules.md."""
        from lingtai.agent import Agent

        parent_dir = tmp_path / "parent"
        parent = Agent(
            service=make_mock_service(),
            agent_name="parent",
            working_dir=parent_dir,
            capabilities=["avatar"],
            admin={"karma": True},
        )
        (parent_dir / "init.json").write_text(
            json.dumps({"manifest": {"agent_name": "parent", "admin": {"karma": True}}})
        )
        if with_rules:
            system_dir = parent_dir / "system"
            system_dir.mkdir(parents=True, exist_ok=True)
            (system_dir / "rules.md").write_text("Always be concise.")
        return parent, parent_dir

    def test_shallow_spawn_no_longer_writes_a_rules_signal(self, tmp_path):
        """Even with a canonical system/rules.md, a shallow spawn writes no .rules."""
        parent, parent_dir = self._setup_spawnable_parent(tmp_path, with_rules=True)

        mgr = parent.get_capability("avatar")
        with _patch_avatar_launch():
            result = mgr.handle({"action": "spawn", "input": {"name": "child", "confirm": True}})
        assert result["status"] == "ok"

        child_dir = parent_dir.parent / "child"
        assert not (child_dir / ".rules").exists()
        # Shallow spawn never copies system/ at all — unaffected by this change.
        assert not (child_dir / "system" / "rules.md").exists()

    def test_deep_spawn_still_copies_rules_md_but_writes_no_signal(self, tmp_path):
        """Deep copy of system/ (unchanged) supplies rules.md; no .rules signal is added."""
        parent, parent_dir = self._setup_spawnable_parent(tmp_path, with_rules=True)

        mgr = parent.get_capability("avatar")
        with _patch_avatar_launch():
            result = mgr.handle({"action": "spawn", "input": {"name": "clone", "type": "deep", "confirm": True}})
        assert result["status"] == "ok"

        clone_dir = parent_dir.parent / "clone"
        # Ordinary deep copy of system/ is preserved.
        assert (clone_dir / "system" / "rules.md").read_text() == "Always be concise."
        # No dedicated .rules signal is written anymore.
        assert not (clone_dir / ".rules").exists()


class TestSpawnNameValidation:
    """Avatar name doubles as working-dir basename. It must be a bare segment:
    path separators, parent-traversal, leading dots, absolute paths, empty
    names, or oversized names are all rejected before any filesystem mutation.
    Scripts other than ASCII (e.g. CJK) are allowed — only structural chars
    are forbidden. See kernel audit C3/C4."""

    def _spawnable_parent(self, tmp_path):
        from lingtai.agent import Agent

        parent_dir = tmp_path / "parent"
        parent = Agent(
            service=make_mock_service(),
            agent_name="parent",
            working_dir=parent_dir,
            capabilities=["avatar"],
        )
        (parent_dir / "init.json").write_text(
            json.dumps({"manifest": {"agent_name": "parent", "admin": {}}})
        )
        return parent, parent_dir

    @pytest.mark.parametrize("bad_name", [
        "avatars/scholar",      # the real-world bug from 2026-04-22
        "../evil",              # parent traversal
        "/etc/hacked",          # absolute
        "foo/bar",              # slash mid-string
        "foo\\bar",             # backslash (windows-style)
        ".hidden",              # leading dot (would shadow .tui-asset etc.)
        ".",                    # current dir
        "..",                   # parent dir
        "",                     # empty
        "foo.bar",              # dot anywhere
        "foo bar",              # space
        "a" * 65,               # over length cap
        "foo\x00bar",           # null byte
    ])
    def test_spawn_rejects_unsafe_name(self, tmp_path, bad_name):
        parent, parent_dir = self._spawnable_parent(tmp_path)
        mgr = parent.get_capability("avatar")

        with _patch_avatar_launch() as launch:
            result = mgr.handle({"action": "spawn", "input": {"name": bad_name}})

        assert "error" in result, f"name={bad_name!r} should have been rejected but got {result}"
        # No subprocess launched
        launch.assert_not_called()
        # No stray directory created outside the network root
        for entry in parent_dir.parent.iterdir():
            # Only the parent dir should exist; no sibling was created
            assert entry == parent_dir, f"stray entry created: {entry}"

    @pytest.mark.parametrize("good_name", [
        "researcher",
        "scholar-reader",
        "paper_summarizer",
        "学者",            # CJK allowed
        "研究员",          # CJK allowed
        "学者-甲",         # CJK + hyphen
        "アバター",         # kana
        "한글",            # hangul
    ])
    def test_spawn_accepts_valid_name(self, tmp_path, good_name):
        parent, parent_dir = self._spawnable_parent(tmp_path)
        mgr = parent.get_capability("avatar")

        with _patch_avatar_launch():
            result = mgr.handle({"action": "spawn", "input": {"name": good_name, "confirm": True}})

        assert result.get("status") == "ok", f"name={good_name!r} should have been accepted but got {result}"
        assert (parent_dir.parent / good_name).is_dir()

    def test_legacy_dir_argument_is_rejected_before_any_io(self, tmp_path):
        """Pre-fix callers may still pass `dir=...` inside the spawn input.

        Before the LTP v2 migration `dir` was merely absent from the schema and
        silently ignored, so a call carrying it still spawned at the
        `name`-driven location. The strict per-action input schema is now the
        dispatch-time authorization boundary: an unknown input key is rejected
        outright, before any filesystem mutation or subprocess launch. The
        malicious `dir` still cannot place anything — and now neither can the
        otherwise-valid `name`.
        """
        parent, parent_dir = self._spawnable_parent(tmp_path)
        mgr = parent.get_capability("avatar")

        with _patch_avatar_launch() as launch:
            # Pass both a safe name and a malicious legacy dir; the whole call
            # is refused because `dir` is not a declared spawn input field.
            result = mgr.handle({
                "action": "spawn",
                "input": {"name": "safe", "dir": "avatars/evil", "confirm": True},
            })

        assert result == {
            "status": "failed",
            "error_code": "INVALID_ARGUMENT",
            "message": "unsupported avatar input field",
        }
        # Rejected before any I/O: no subprocess, no directory, no ledger.
        launch.assert_not_called()
        assert not (parent_dir.parent / "safe").exists()
        # The malicious dir was NOT honored
        assert not (parent_dir.parent / "avatars").exists()
        assert not (parent_dir / "delegates" / "ledger.jsonl").exists()

    def test_prepare_deep_refuses_non_sibling_dst(self, tmp_path):
        """Defense-in-depth: even if _prepare_deep is called directly with a
        dst outside the parent network, it must refuse before any rmtree."""
        src = tmp_path / "network" / "parent"
        src.mkdir(parents=True)
        (src / "system").mkdir()
        (src / "system" / "important.md").write_text("do not delete")

        # dst lives in a totally different tree
        dst = tmp_path / "elsewhere" / "victim"

        with pytest.raises(ValueError, match="not a sibling"):
            AvatarManager._prepare_deep(src, dst)

        # src untouched
        assert (src / "system" / "important.md").read_text() == "do not delete"
