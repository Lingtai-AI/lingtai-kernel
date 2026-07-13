"""Behavior and conformance locks for snapshot/revision Ports and adapter."""
from __future__ import annotations

import inspect
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest

from lingtai.adapters.posix.git_cli import PosixGitCliAdapter
from lingtai.kernel.base_agent import lifecycle
from lingtai.kernel.snapshot import SnapshotPort, SourceRevisionPort
from tests._snapshot_helpers import FakeSnapshotPort, FakeSourceRevisionPort


SNAPSHOT_METHODS = frozenset({"initialize", "snapshot", "collect_garbage"})
SOURCE_REVISION_METHODS = frozenset({"current_revision", "is_dirty"})
GITIGNORE_BASELINE = (
    "# Secrets — MCP addon credentials (bot tokens, API keys)\n"
    ".secrets/\n"
    "\n"
    "# Transient lifecycle signal files\n"
    ".sleep\n"
    ".suspend\n"
    ".agent.heartbeat\n"
    ".timemachine.pid\n"
)
SYSTEM_BASELINE = {
    "covenant.md": "",
    "principle.md": "",
    "pad.md": "",
}


def _public_method_names(implementation):
    return {
        name
        for name, member in inspect.getmembers(
            implementation, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    }


def _assert_signatures_match(port, implementation, method_names):
    for name in method_names:
        assert inspect.signature(getattr(implementation, name)) == inspect.signature(
            getattr(port, name)
        )


def test_ports_fakes_and_adapter_have_exact_conforming_method_sets(tmp_path):
    assert SnapshotPort.__abstractmethods__ == SNAPSHOT_METHODS
    assert SourceRevisionPort.__abstractmethods__ == SOURCE_REVISION_METHODS
    assert _public_method_names(SnapshotPort) == SNAPSHOT_METHODS
    assert _public_method_names(SourceRevisionPort) == SOURCE_REVISION_METHODS
    assert _public_method_names(FakeSnapshotPort) == SNAPSHOT_METHODS
    assert _public_method_names(FakeSourceRevisionPort) == SOURCE_REVISION_METHODS

    fake_snapshot = FakeSnapshotPort()
    fake_revision = FakeSourceRevisionPort()
    adapter = PosixGitCliAdapter(tmp_path)
    assert isinstance(fake_snapshot, SnapshotPort)
    assert isinstance(fake_revision, SourceRevisionPort)
    assert isinstance(adapter, SnapshotPort)
    assert isinstance(adapter, SourceRevisionPort)

    _assert_signatures_match(SnapshotPort, FakeSnapshotPort, SNAPSHOT_METHODS)
    _assert_signatures_match(SnapshotPort, PosixGitCliAdapter, SNAPSHOT_METHODS)
    _assert_signatures_match(
        SourceRevisionPort, FakeSourceRevisionPort, SOURCE_REVISION_METHODS
    )
    _assert_signatures_match(
        SourceRevisionPort, PosixGitCliAdapter, SOURCE_REVISION_METHODS
    )


def test_production_adapter_public_surface_is_fixed_and_has_no_command_runner():
    public_methods = _public_method_names(PosixGitCliAdapter)
    assert public_methods == SNAPSHOT_METHODS | SOURCE_REVISION_METHODS
    assert not hasattr(PosixGitCliAdapter, "run")
    for name, member in vars(PosixGitCliAdapter).items():
        if inspect.isfunction(member):
            assert "argv" not in inspect.signature(member).parameters


def _expected_initialize_calls(directory):
    return [
        call(["git", "init"], cwd=directory, capture_output=True, check=True),
        call(
            ["git", "config", "user.email", "agent@lingtai"],
            cwd=directory,
            capture_output=True,
            check=True,
        ),
        call(
            ["git", "config", "user.name", "灵台 Agent"],
            cwd=directory,
            capture_output=True,
            check=True,
        ),
        call(
            ["git", "add", ".gitignore", "system/"],
            cwd=directory,
            capture_output=True,
            check=True,
        ),
        call(
            ["git", "commit", "-m", "init: agent working directory"],
            cwd=directory,
            capture_output=True,
            check=True,
        ),
    ]


def _assert_exact_baseline(directory):
    assert (directory / ".gitignore").read_text() == GITIGNORE_BASELINE
    assert {
        name: (directory / "system" / name).read_text()
        for name in SYSTEM_BASELINE
    } == SYSTEM_BASELINE


def test_initialize_uses_fixed_commands_and_exact_baseline(tmp_path):
    def assert_baseline_precedes_every_git_phase(*_args, **_kwargs):
        _assert_exact_baseline(tmp_path)
        return SimpleNamespace(returncode=0)

    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=assert_baseline_precedes_every_git_phase,
    ) as run:
        PosixGitCliAdapter(tmp_path).initialize()

    assert run.call_args_list == _expected_initialize_calls(tmp_path)
    _assert_exact_baseline(tmp_path)


def test_initialize_preserves_existing_git_repository_and_sentinel_files(tmp_path):
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


@pytest.mark.parametrize("failure_index", range(5))
@pytest.mark.parametrize(
    "failure_type",
    [FileNotFoundError, subprocess.CalledProcessError],
    ids=["executable-missing", "git-command-failed"],
)
def test_initialize_each_git_phase_failure_retains_exact_baseline(
    tmp_path, failure_index, failure_type
):
    expected_calls = _expected_initialize_calls(tmp_path)
    if failure_type is FileNotFoundError:
        failure = FileNotFoundError("git executable missing")
    else:
        failure = subprocess.CalledProcessError(
            1, expected_calls[failure_index].args[0]
        )
    effects = [SimpleNamespace(returncode=0) for _ in range(5)]
    effects[failure_index] = failure

    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run", side_effect=effects
    ) as run:
        PosixGitCliAdapter(tmp_path).initialize()

    assert run.call_args_list == expected_calls[: failure_index + 1]
    _assert_exact_baseline(tmp_path)


def test_snapshot_stages_all_and_clean_tree_is_noop(tmp_path):
    clean = SimpleNamespace(returncode=0)
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[SimpleNamespace(returncode=0), clean],
    ) as run:
        result = PosixGitCliAdapter(tmp_path).snapshot()
    assert result is None
    assert run.call_args_list[0].args[0] == ["git", "add", "-A"]
    assert run.call_args_list[1].args[0] == ["git", "diff", "--cached", "--quiet"]


def test_snapshot_commits_and_returns_native_short_revision(tmp_path):
    results = [
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=1),
        SimpleNamespace(returncode=0),
        SimpleNamespace(returncode=0, stdout="abc1234\n"),
    ]
    with patch("lingtai.adapters.posix.git_cli.subprocess.run", side_effect=results) as run:
        assert PosixGitCliAdapter(tmp_path).snapshot() == "abc1234"
    assert run.call_args_list[2].args[0][:3] == ["git", "commit", "-m"]
    assert run.call_args_list[2].args[0][3].startswith("snapshot ")
    assert run.call_args_list[3].args[0] == ["git", "rev-parse", "--short", "HEAD"]


def test_snapshot_cached_diff_operational_failure_does_not_commit(tmp_path):
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=2),
        ],
    ) as run:
        assert PosixGitCliAdapter(tmp_path).snapshot() is None

    assert [invocation.args[0] for invocation in run.call_args_list] == [
        ["git", "add", "-A"],
        ["git", "diff", "--cached", "--quiet"],
    ]


@pytest.mark.parametrize(
    "revision_result",
    [
        SimpleNamespace(returncode=1, stdout="ignored\n"),
        SimpleNamespace(returncode=0, stdout="\n"),
    ],
    ids=["nonzero", "empty-stdout"],
)
def test_snapshot_final_revision_failure_returns_none(tmp_path, revision_result):
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        side_effect=[
            SimpleNamespace(returncode=0),
            SimpleNamespace(returncode=1),
            SimpleNamespace(returncode=0),
            revision_result,
        ],
    ) as run:
        assert PosixGitCliAdapter(tmp_path).snapshot() is None

    assert run.call_args_list[-1].args[0] == [
        "git", "rev-parse", "--short", "HEAD"
    ]


def test_real_git_snapshot_clean_untracked_deletion_and_secret_exclusion(tmp_path):
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
    assert deletion_revision
    assert deletion_revision != untracked_revision
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


def test_collect_garbage_is_fixed_and_bounded(tmp_path):
    with patch("lingtai.adapters.posix.git_cli.subprocess.run") as run:
        PosixGitCliAdapter(tmp_path).collect_garbage()
    run.assert_called_once_with(
        ["git", "gc", "--auto"], cwd=tmp_path, capture_output=True, timeout=60
    )


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("git executable missing"),
        subprocess.CalledProcessError(1, ["git", "gc", "--auto"]),
        subprocess.TimeoutExpired(["git", "gc", "--auto"], 60),
    ],
    ids=["executable-missing", "git-command-failed", "timeout"],
)
def test_collect_garbage_swallows_every_declared_operational_failure(
    tmp_path, failure
):
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run", side_effect=failure
    ) as run:
        PosixGitCliAdapter(tmp_path).collect_garbage()
    run.assert_called_once_with(
        ["git", "gc", "--auto"], cwd=tmp_path, capture_output=True, timeout=60
    )


def test_source_revision_native_and_fixed_length_queries(tmp_path):
    adapter = PosixGitCliAdapter(tmp_path)
    with patch(
        "lingtai.adapters.posix.git_cli.subprocess.run",
        return_value=SimpleNamespace(returncode=0, stdout="abcdef\n"),
    ) as run:
        assert adapter.current_revision(None, 2.0) == "abcdef"
        assert run.call_args.args[0] == ["git", "rev-parse", "--short", "HEAD"]
        assert run.call_args.kwargs["timeout"] == 2.0
        assert adapter.current_revision(12, 0.5) == "abcdef"
        assert run.call_args.args[0] == ["git", "rev-parse", "--short=12", "HEAD"]
        assert run.call_args.kwargs["timeout"] == 0.5


def test_source_revision_failure_paths_return_none(tmp_path):
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
    assert run.call_args.args[0] == [
        "git", "status", "--porcelain", "--untracked-files=no"
    ]


def _heartbeat_agent(snapshot_port, interval, working_dir):
    shutdown = Mock()
    shutdown.is_set.return_value = False
    return SimpleNamespace(
        agent_name="snapshot-test",
        _heartbeat_thread=object(),
        _shutdown=shutdown,
        _heartbeat_runtime_ready=True,
        _config=SimpleNamespace(snapshot_interval=interval, aed_timeout=999),
        _snapshot_port=snapshot_port,
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
            _flush_system_prompt=Mock(side_effect=StopAfterInitialization),
        )
        with pytest.raises(StopAfterInitialization):
            lifecycle._start(agent)
        assert snapshot.initialize_calls == expected


def test_heartbeat_snapshot_and_gc_are_first_eligible_and_advance_clocks(tmp_path):
    snapshot = FakeSnapshotPort()
    agent = _heartbeat_agent(snapshot, 10, tmp_path)

    def stop_after_tick(_seconds):
        agent._heartbeat_thread = None

    with patch.object(lifecycle, "_write_heartbeat_tick") as heartbeat_tick, patch.object(
        lifecycle, "_check_rules_file"
    ), patch.object(lifecycle, "_maybe_sleep_after_idle_timeout"), patch(
        "lingtai.kernel.nudge.run_checks"
    ), patch(
        "lingtai.kernel.base_agent.lifecycle.time.monotonic", return_value=90000
    ) as monotonic, patch(
        "lingtai.kernel.base_agent.lifecycle.time.sleep", side_effect=stop_after_tick
    ) as sleep:
        lifecycle._heartbeat_loop(agent)

    heartbeat_tick.assert_called_once_with(agent)
    monotonic.assert_called_once_with()
    sleep.assert_called_once_with(1.0)
    assert snapshot.snapshot_calls == 1
    assert snapshot.collect_garbage_calls == 1
    assert agent._last_snapshot == 90000
    assert agent._last_gc == 90000


def test_heartbeat_gc_runs_only_at_exact_daily_boundaries(tmp_path):
    snapshot = FakeSnapshotPort()
    agent = _heartbeat_agent(snapshot, 1_000_000, tmp_path)
    clock_ticks = [86399, 86400, 86401, 172799, 172800]
    sleep_calls = []
    gc_counts_after_ticks = []
    gc_clocks_after_ticks = []

    def stop_after_final_tick(seconds):
        sleep_calls.append(seconds)
        gc_counts_after_ticks.append(snapshot.collect_garbage_calls)
        gc_clocks_after_ticks.append(agent._last_gc)
        if len(sleep_calls) == len(clock_ticks):
            agent._heartbeat_thread = None

    with patch.object(lifecycle, "_write_heartbeat_tick") as heartbeat_tick, patch.object(
        lifecycle, "_check_rules_file"
    ), patch.object(lifecycle, "_maybe_sleep_after_idle_timeout"), patch(
        "lingtai.kernel.nudge.run_checks"
    ), patch(
        "lingtai.kernel.base_agent.lifecycle.time.monotonic", side_effect=clock_ticks
    ) as monotonic, patch(
        "lingtai.kernel.base_agent.lifecycle.time.sleep",
        side_effect=stop_after_final_tick,
    ) as sleep:
        lifecycle._heartbeat_loop(agent)

    assert heartbeat_tick.call_args_list == [call(agent)] * 5
    assert monotonic.call_args_list == [call()] * 5
    assert sleep.call_args_list == [call(1.0)] * 5
    assert sleep_calls == [1.0] * 5
    assert gc_counts_after_ticks == [0, 1, 1, 1, 2]
    assert gc_clocks_after_ticks == [0.0, 86400, 86400, 86400, 172800]
    assert snapshot.snapshot_calls == 0
    assert snapshot.collect_garbage_calls == 2
    assert agent._last_snapshot == 0.0
    assert agent._last_gc == 172800
