"""Tests for .rules signal consumption and system/rules.md persistence."""
import json
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from lingtai.tools.avatar import AvatarManager

import pytest
from tests._service_helpers import make_mock_llm_service as make_mock_service


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


def test_rules_files_are_inert_and_consumer_is_removed(tmp_path):
    from tests.test_psyche_prompt_settings import _agent, _write_init
    from lingtai.kernel.base_agent import lifecycle
    _write_init(tmp_path)
    (tmp_path / 'system').mkdir()
    (tmp_path / 'system/rules.md').write_text('OLD RULES')
    (tmp_path / '.rules').write_text('PENDING RULES')
    agent = _agent(tmp_path)
    try:
        agent._reconstruct_context()
        assert not hasattr(agent, '_check_rules_file')
        assert not hasattr(lifecycle, '_check_rules_file')
        assert agent._prompt_manager.read_section('rules') is None
        assert 'OLD RULES' not in agent._build_system_prompt()
        assert (tmp_path / '.rules').read_text() == 'PENDING RULES'
        assert (tmp_path / 'system/rules.md').read_text() == 'OLD RULES'
    finally:
        agent.stop(timeout=1)


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
        skills, notification, system, daemon) already follows — see
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
        for relative_path, content in (
            ("knowledge/facts.txt", "parent knowledge"),
            ("exports/summary.txt", "parent export"),
            ("logs/parent-only.log", "parent runtime"),
        ):
            source_file = parent_dir / relative_path
            source_file.parent.mkdir(exist_ok=True)
            source_file.write_text(content)

        mgr = parent.get_capability("avatar")
        with _patch_avatar_launch():
            result = mgr.handle({"action": "spawn", "input": {"name": "clone", "type": "deep", "confirm": True}})
        assert result["status"] == "ok"

        clone_dir = parent_dir.parent / "clone"
        # Ordinary deep copy of system/ is preserved.
        assert (clone_dir / "system" / "rules.md").read_text() == "Always be concise."
        # No dedicated .rules signal is written anymore.
        assert not (clone_dir / ".rules").exists()
        assert (clone_dir / "knowledge" / "facts.txt").read_text() == "parent knowledge"
        assert (clone_dir / "exports" / "summary.txt").read_text() == "parent export"
        assert not (clone_dir / "logs" / "parent-only.log").exists()


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
