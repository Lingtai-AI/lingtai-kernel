"""Observable behavior for Snapshot and SourceRevision Ports."""
from __future__ import annotations

import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lingtai.adapters.posix.git_cli import PosixGitCliAdapter
from lingtai.kernel.base_agent import lifecycle
from lingtai.kernel.snapshot import SnapshotPort, SourceRevisionPort
from tests._snapshot_helpers import FakeSnapshotPort, FakeSourceRevisionPort

GITIGNORE_BASELINE = (
    "# Secrets — MCP addon credentials (bot tokens, API keys)\n"
    ".secrets/\n\n"
    "# Transient lifecycle signal files\n"
    ".sleep\n.suspend\n.agent.heartbeat\n.timemachine.pid\n"
)
SYSTEM_BASELINE = {"covenant.md": "", "principle.md": "", "pad.md": ""}


def _assert_exact_baseline(directory):
    assert (directory / ".gitignore").read_text() == GITIGNORE_BASELINE
    assert {
        name: (directory / "system" / name).read_text() for name in SYSTEM_BASELINE
    } == SYSTEM_BASELINE


def test_ports_and_implementations_are_runtime_substitutable(tmp_path):
    assert SnapshotPort.__abstractmethods__ == {
        "initialize",
        "snapshot",
        "collect_garbage",
    }
    assert SourceRevisionPort.__abstractmethods__ == {"current_revision", "is_dirty"}
    assert isinstance(FakeSnapshotPort(), SnapshotPort)
    assert isinstance(FakeSourceRevisionPort(), SourceRevisionPort)
    adapter = PosixGitCliAdapter(tmp_path)
    assert isinstance(adapter, SnapshotPort)
    assert isinstance(adapter, SourceRevisionPort)


def test_initialize_preserves_baseline_before_git_phases(tmp_path):
    def check_baseline(*_args, **_kwargs):
        _assert_exact_baseline(tmp_path)
        return SimpleNamespace(returncode=0)

    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run", side_effect=check_baseline
    ):
        PosixGitCliAdapter(tmp_path).initialize()
    _assert_exact_baseline(tmp_path)


def test_initialize_preserves_existing_git_repository_and_sentinels(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "sentinel").write_text("git-sentinel")
    (tmp_path / ".gitignore").write_text("custom-ignore\n")
    system_dir = tmp_path / "system"
    system_dir.mkdir()
    for name in SYSTEM_BASELINE:
        (system_dir / name).write_text(f"sentinel:{name}")

    with patch("lingtai.adapters.posix.git_cli.subprocess.run") as run:
        PosixGitCliAdapter(tmp_path).initialize()
    run.assert_not_called()
    assert (git_dir / "sentinel").read_text() == "git-sentinel"
    assert (tmp_path / ".gitignore").read_text() == "custom-ignore\n"
    assert {
        name: (system_dir / name).read_text() for name in SYSTEM_BASELINE
    } == {name: f"sentinel:{name}" for name in SYSTEM_BASELINE}


@pytest.mark.parametrize(
    ("failure_index", "failure"),
    [
        (0, FileNotFoundError("git executable missing")),
        (4, subprocess.CalledProcessError(1, "git commit")),
    ],
    ids=["early-executable-missing", "late-command-failure"],
)
def test_initialize_failure_retains_exact_baseline(tmp_path, failure_index, failure):
    effects = [SimpleNamespace(returncode=0) for _ in range(5)]
    effects[failure_index] = failure
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run", side_effect=effects
    ):
        PosixGitCliAdapter(tmp_path).initialize()
    _assert_exact_baseline(tmp_path)


def test_snapshot_clean_tree_is_noop(tmp_path):
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=0)],
    ):
        assert PosixGitCliAdapter(tmp_path).snapshot() is None


def test_snapshot_commits_changed_tree_and_returns_short_revision(tmp_path):
    results = [
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=1),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0, stdout="abc1234\n"),
    ]
    with patch("lingtai.adapters.posix.git_cli.subprocess.run", side_effect=results):
        assert PosixGitCliAdapter(tmp_path).snapshot() == "abc1234"


def test_snapshot_cached_diff_failure_does_not_commit(tmp_path):
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=2)],
    ) as run:
        assert PosixGitCliAdapter(tmp_path).snapshot() is None
    assert run.call_count == 2


def test_snapshot_final_revision_failure_returns_none(tmp_path):
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=1),
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=0, stdout=""),
        ],
    ):
        assert PosixGitCliAdapter(tmp_path).snapshot() is None


def test_real_git_snapshot_handles_deletion_and_secret_exclusion(tmp_path):
    if shutil.which("git") is None:
        pytest.skip("real Git behavior lock requires the git executable")
    adapter = PosixGitCliAdapter(tmp_path)
    adapter.initialize()
    assert (tmp_path / ".git").is_dir()
    assert adapter.snapshot() is None

    tracked = tmp_path / "tracked.txt"
    tracked.write_text("first version\n")
    untracked_revision = adapter.snapshot()
    assert untracked_revision
    committed = subprocess.run(
        ["git", "show", "HEAD:tracked.txt"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert committed.stdout == "first version\n"

    tracked.unlink()
    deletion_revision = adapter.snapshot()
    assert deletion_revision and deletion_revision != untracked_revision
    assert subprocess.run(
        ["git", "ls-files", "--error-unmatch", "tracked.txt"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    ).returncode != 0

    secrets = tmp_path / ".secrets"
    secrets.mkdir()
    (secrets / "token").write_text("never commit me")
    assert adapter.snapshot() is None
    assert subprocess.run(
        ["git", "check-ignore", "--quiet", ".secrets/token"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
    ).returncode == 0
    assert subprocess.run(
        ["git", "ls-files", ".secrets/token"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""


def test_snapshot_swallows_git_absence_and_failure(tmp_path):
    adapter = PosixGitCliAdapter(tmp_path)
    for failure in (FileNotFoundError(), subprocess.CalledProcessError(1, "git")):
        with patch("lingtai.adapters.posix.git_cli.subprocess.run", side_effect=failure):
            assert adapter.snapshot() is None


def test_collect_garbage_is_bounded_and_best_effort(tmp_path):
    with patch("lingtai.adapters.posix.git_cli.subprocess.run") as run:
        PosixGitCliAdapter(tmp_path).collect_garbage()
    run.assert_called_once()
    assert run.call_args.kwargs["timeout"] == 60

    for failure in (
        FileNotFoundError("git executable missing"),
        subprocess.CalledProcessError(1, "git gc"),
        subprocess.TimeoutExpired("git gc", 60),
    ):
        with patch(
            "lingtai.adapters.posix.git_cli.subprocess.run", side_effect=failure
        ):
            PosixGitCliAdapter(tmp_path).collect_garbage()


def test_source_revision_preserves_length_and_deadline_policy(tmp_path):
    adapter = PosixGitCliAdapter(tmp_path)
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="abcdef\n"),
    ) as run:
        assert adapter.current_revision(None, 2.0) == "abcdef"
        assert "--short" in run.call_args.args[0]
        assert run.call_args.kwargs["timeout"] == 2.0
        assert adapter.current_revision(12, 0.5) == "abcdef"
        assert "--short=12" in run.call_args.args[0]
        assert run.call_args.kwargs["timeout"] == 0.5


def test_source_revision_and_dirty_failures_return_none(tmp_path):
    adapter = PosixGitCliAdapter(tmp_path)
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="ignored"),
    ):
        assert adapter.current_revision(12, 0.5) is None
        assert adapter.is_dirty(0.5) is None
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=subprocess.TimeoutExpired("git", 0.5),
    ):
        assert adapter.current_revision(12, 0.5) is None
        assert adapter.is_dirty(0.5) is None


def test_dirty_query_is_tracked_only_and_tri_state(tmp_path):
    adapter = PosixGitCliAdapter(tmp_path)
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[
            SimpleNamespace(returncode=0, stdout=""),
            SimpleNamespace(returncode=0, stdout=" M tracked.py\n"),
        ],
    ) as run:
        assert adapter.is_dirty(0.5) is False
        assert adapter.is_dirty(0.5) is True
    assert "--untracked-files=no" in run.call_args.args[0]


def _heartbeat_agent(snapshot_port, interval, working_dir):
    shutdown = Mock()
    shutdown.is_set.return_value = False
    lifecycle_clock = SimpleNamespace(
        monotonic_seconds=Mock(return_value=0.0),
        wall_seconds=Mock(return_value=0.0),
    )
    return SimpleNamespace(
        agent_name="snapshot-test",
        _heartbeat_thread=object(),
        _heartbeat_stop=Mock(),
        _shutdown=shutdown,
        _heartbeat_runtime_ready=True,
        _config=SimpleNamespace(snapshot_interval=interval, aed_timeout=999),
        _snapshot_port=snapshot_port,
        _lifecycle_clock=lifecycle_clock,
        _last_snapshot=0.0,
        _last_gc=0.0,
        _working_dir=working_dir,
        _state=object(),
        _aed_start=None,
        _active_stuck_logged=True,
        _sync_notifications=Mock(),
        _setup_telegram_task_card=Mock(),
        _write_status_snapshot=Mock(),
        _log=Mock(),
    )


def test_start_initializes_only_when_snapshots_enabled():
    class StopAfterInitialization(Exception):
        pass

    for interval, expected in ((None, 0), (60, 1)):
        snapshot = FakeSnapshotPort()
        agent = SimpleNamespace(
            _sealed=False,
            _thread=None,
            _shutdown=Mock(),
            _config=SimpleNamespace(snapshot_interval=interval),
            _snapshot_port=snapshot,
            _lifecycle_clock=SimpleNamespace(monotonic_seconds=Mock(return_value=0.0)),
            _flush_system_prompt=Mock(side_effect=StopAfterInitialization),
        )
        with pytest.raises(StopAfterInitialization):
            lifecycle._start(agent)
        assert snapshot.initialize_calls == expected


def test_heartbeat_snapshot_and_gc_share_first_eligible_tick(tmp_path):
    snapshot = FakeSnapshotPort()
    agent = _heartbeat_agent(snapshot, 10, tmp_path)

    def stop_after_tick(_seconds):
        agent._heartbeat_thread = None

    agent._heartbeat_stop.wait.side_effect = stop_after_tick
    agent._lifecycle_clock.monotonic_seconds.return_value = 90000
    with patch.object(lifecycle, "_write_heartbeat_tick"), patch.object(
        lifecycle, "_check_rules_file"
    ), patch.object(lifecycle, "_maybe_sleep_after_idle_timeout"), patch(
        "lingtai.kernel.nudge.run_checks"
    ):
        lifecycle._heartbeat_loop(agent)
    assert agent._lifecycle_clock.monotonic_seconds.call_count == 1
    assert snapshot.snapshot_calls == snapshot.collect_garbage_calls == 1
    assert agent._last_snapshot == agent._last_gc == 90000


def test_heartbeat_gc_runs_at_daily_boundaries(tmp_path):
    snapshot = FakeSnapshotPort()
    agent = _heartbeat_agent(snapshot, 1_000_000, tmp_path)
    clock_ticks = [86399, 86400, 86401, 172799, 172800]
    sleep_calls = []
    gc_counts = []

    def stop_after_tick(seconds):
        sleep_calls.append(seconds)
        gc_counts.append(snapshot.collect_garbage_calls)
        if len(sleep_calls) == len(clock_ticks):
            agent._heartbeat_thread = None

    agent._heartbeat_stop.wait.side_effect = stop_after_tick
    agent._lifecycle_clock.monotonic_seconds.side_effect = clock_ticks
    with patch.object(lifecycle, "_write_heartbeat_tick"), patch.object(
        lifecycle, "_check_rules_file"
    ), patch.object(lifecycle, "_maybe_sleep_after_idle_timeout"), patch(
        "lingtai.kernel.nudge.run_checks"
    ):
        lifecycle._heartbeat_loop(agent)
    assert sleep_calls == [1.0] * 5
    assert gc_counts == [0, 1, 1, 1, 2]
    assert snapshot.collect_garbage_calls == 2
    assert agent._last_gc == 172800
