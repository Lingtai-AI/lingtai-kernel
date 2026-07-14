"""Tests for `_perform_refresh` filesystem handshake and lifecycle signaling.

Three call sites reach `_perform_refresh` directly:

  1. Heartbeat — has already renamed `.refresh` → `.refresh.taken` and
     intends to call `_shutdown.set()` immediately after our return.
  2. `system(action='refresh')` intrinsic — has done neither.
  3. AED preset-fallback in `turn.py` — has done neither.

`_perform_refresh` therefore normalizes the on-disk handshake itself
(making `.refresh.taken` present and clearing `.refresh`) and signals
`_cancel_event` + `_shutdown` after the watcher subprocess is spawned,
so the lock-release phase completes regardless of caller. These tests
exercise that contract without spawning a real subprocess: the injected
`FakeRefreshWatcher` (see `tests/_refresh_watcher_helpers.py`) records each
`spawn_detached(request)` call (a typed `RefreshWatcherRequest`) in place of
the production `PosixRefreshWatcherAdapter`
(`src/lingtai/kernel/refresh_watcher/CONTRACT.md`).
"""
from __future__ import annotations
from lingtai.tools.registry import INTRINSICS as _TEST_INTRINSICS

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from unittest.mock import MagicMock, patch
from tests._workdir_lease_helpers import make_test_lease
from tests._snapshot_helpers import make_test_snapshot_port, make_test_source_revision_port
from tests._lifecycle_clock_helpers import make_test_lifecycle_clock
from tests._notification_store_helpers import notification_store_for
from tests._agent_presence_helpers import make_test_presence_store
from tests._refresh_watcher_helpers import make_test_refresh_watcher


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "p"
    svc.model = "m"
    return svc


def _make_agent_with_launch_cmd(tmp_path, agent_name="alice", launch_cmd=None):
    """Build a bare BaseAgent and rebind `_build_launch_cmd` so the
    refresh path proceeds past the `cmd is None` early return. The injected
    `FakeRefreshWatcher` records spawn calls in place of a real subprocess.
    """
    from lingtai.kernel.base_agent import BaseAgent
    wd = tmp_path / "test"
    wd.mkdir(exist_ok=True)
    # The production composition root creates the log directory before a
    # refresh watcher can run. This bare BaseAgent fixture must provide the
    # same outer-runtime precondition so the watcher can open its relaunch log.
    (wd / "logs").mkdir(exist_ok=True)
    agent = BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name=agent_name,
        working_dir=wd, workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(wd),
        refresh_watcher=make_test_refresh_watcher(),
    )
    # BaseAgent._build_launch_cmd returns None; rebind to a sentinel
    # list so the handshake/signal code runs.
    if launch_cmd is None:
        launch_cmd = ["python", "-c", "print('relaunch sentinel')"]
    agent._build_launch_cmd = lambda: launch_cmd
    return agent


def _capture_watcher_script(agent):
    agent._perform_refresh()
    assert agent._refresh_watcher.spawned
    return agent._refresh_watcher.last_script


def _fast_watcher_script(script: str) -> str:
    return (
        script
        .replace("MAX_ATTEMPTS = 12", "MAX_ATTEMPTS = 2")
        .replace("HEALTH_CHECK_WAIT = 10", "HEALTH_CHECK_WAIT = 0.1")
        .replace("deadline = time.time() + 60", "deadline = time.time() + 1")
        .replace("deadline = time.time() + 5", "deadline = time.time() + 0.05")
    )


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Production adapter
# ---------------------------------------------------------------------------


def test_posix_refresh_watcher_adapter_spawns_exact_detached_process():
    """The production adapter must translate a typed `RefreshWatcherRequest`
    into the exact Popen hand-off that was extracted from `_perform_refresh`:
    current interpreter, the owned entrypoint module invoked via `-m`, the
    compact encoded-request payload, built env, detached session, and no
    inherited stdio. The Port no longer accepts raw script/env, and the
    transport no longer puts the ~480-line generated program source directly
    on argv via `-c`.
    """
    from lingtai.adapters.posix.refresh_watcher import (
        ENTRYPOINT_MODULE,
        PosixRefreshWatcherAdapter,
        build_watcher_env,
    )
    from lingtai.kernel.refresh_watcher import RefreshWatcherRequest, encode_request

    request = RefreshWatcherRequest(
        taken_path="/wd/.refresh.taken",
        lock_path="/wd/.agent.lock",
        events_path="/wd/logs/events.jsonl",
        stderr_log="/wd/logs/refresh_relaunch.log",
        working_dir="/wd",
        cmd=("lingtai-agent", "run", "/wd"),
        agent_name="alice",
        address="wd",
        identity_fields_json='{"kernel_version": "v1"}',
    )
    expected_payload = encode_request(request)

    with patch("lingtai.adapters.posix.refresh_watcher.subprocess.Popen") as popen, \
            patch.dict("os.environ", {"PATH": "/test/bin"}, clear=True):
        PosixRefreshWatcherAdapter().spawn_detached(request)
        expected_env = build_watcher_env(request)

    popen.assert_called_once_with(
        [sys.executable, "-m", ENTRYPOINT_MODULE, expected_payload],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=expected_env,
    )
    assert expected_env["PATH"] == "/test/bin"
    assert expected_env["LINGTAI_REFRESH_ENV_OVERWRITE"] == "1"
    assert ENTRYPOINT_MODULE == "lingtai.adapters.posix.refresh_watcher_entrypoint"


def _sample_request(**overrides):
    from lingtai.kernel.refresh_watcher import RefreshWatcherRequest

    fields = dict(
        taken_path="/wd/.refresh.taken",
        lock_path="/wd/.agent.lock",
        events_path="/wd/logs/events.jsonl",
        stderr_log="/wd/logs/refresh_relaunch.log",
        working_dir="/wd",
        cmd=("lingtai-agent", "run", "/wd"),
        agent_name="alice",
        address="wd",
        identity_fields_json='{"kernel_version": "v1"}',
    )
    fields.update(overrides)
    return RefreshWatcherRequest(**fields)


def test_encode_decode_request_roundtrip():
    """`decode_request(encode_request(request))` must reproduce an equal
    request — the wire shape a transport carries across a process boundary
    must be lossless for every field, including the tuple `cmd` (JSON has no
    tuple type, so `decode_request` must restore it, not leave it a list).
    """
    from lingtai.kernel.refresh_watcher import decode_request, encode_request

    request = _sample_request()
    payload = encode_request(request)
    decoded = decode_request(payload)

    assert decoded == request
    assert isinstance(decoded.cmd, tuple)


def test_encode_request_is_deterministic():
    """The same request must always encode to the same bytes — a fixed field
    order, not dict-iteration-order-dependent — so the transport payload is
    directly diffable/testable.
    """
    from lingtai.kernel.refresh_watcher import encode_request

    request = _sample_request()
    assert encode_request(request) == encode_request(request)


@pytest.mark.parametrize(
    "bad_payload",
    [
        "not json at all",
        "[1, 2, 3]",
        '"just a string"',
        "42",
        "null",
        "{}",  # missing every field
        '{"cmd": [1, 2]}',  # cmd elements not strings, plus missing fields
    ],
)
def test_decode_request_invalid_payload_fails_loudly(bad_payload):
    from lingtai.kernel.refresh_watcher import decode_request

    with pytest.raises(ValueError):
        decode_request(bad_payload)


def test_decode_request_rejects_extra_and_wrong_type_fields():
    from lingtai.kernel.refresh_watcher import decode_request, encode_request

    request = _sample_request()
    payload_dict = json.loads(encode_request(request))

    extra = dict(payload_dict)
    extra["unexpected"] = "field"
    with pytest.raises(ValueError):
        decode_request(json.dumps(extra))

    wrong_type = dict(payload_dict)
    wrong_type["env_overwrite"] = "yes"
    with pytest.raises(ValueError):
        decode_request(json.dumps(wrong_type))

    wrong_cmd = dict(payload_dict)
    wrong_cmd["cmd"] = "not-a-list"
    with pytest.raises(ValueError):
        decode_request(json.dumps(wrong_cmd))


def test_refresh_watcher_entrypoint_main_renders_and_executes_request():
    """`refresh_watcher_entrypoint.main([payload])` must decode the request,
    render the exact same program text `render_watcher_script` would, and
    execute it — proving the entrypoint module is itself directly callable
    for small contract tests, not just a subprocess black box. Uses a fast
    deadline (0 sleep) via a launch cmd that immediately marks the agent
    heartbeat fresh so the relaunch loop exits without a real subprocess
    round-trip through this in-process call.
    """
    import lingtai.adapters.posix.refresh_watcher_entrypoint as entrypoint_mod
    from lingtai.kernel.refresh_watcher import encode_request
    from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script

    request = _sample_request()
    payload = encode_request(request)

    captured = {}

    def fake_exec(code, globals_):
        captured["code"] = code
        captured["globals"] = globals_

    with patch.object(entrypoint_mod, "exec", fake_exec, create=True):
        result = entrypoint_mod.main([payload])

    assert result == 0
    expected_script = render_watcher_script(request)
    assert captured["code"] == compile(expected_script, "<refresh_watcher>", "exec")


def test_refresh_watcher_entrypoint_main_rejects_bad_argv():
    import lingtai.adapters.posix.refresh_watcher_entrypoint as entrypoint_mod

    with pytest.raises(SystemExit):
        entrypoint_mod.main([])
    with pytest.raises(SystemExit):
        entrypoint_mod.main(["one", "two"])


def test_refresh_watcher_entrypoint_invoked_via_dash_m_runs_watcher_program(tmp_path):
    """End-to-end smoke test of the new transport: launching
    `sys.executable -m lingtai.adapters.posix.refresh_watcher_entrypoint
    <payload>` — exactly the argv shape
    `PosixRefreshWatcherAdapter.spawn_detached` uses — must decode the
    request and run the real generated watcher program, reaching the same
    ack/success events the previous `-c <script>` transport did. The agent
    already appears alive (a fresh heartbeat) so the relaunch loop's first
    "already alive" branch exits immediately, keeping the test fast without
    needing to shrink MAX_ATTEMPTS/HEALTH_CHECK_WAIT inside the real module.
    """
    from lingtai.adapters.posix.refresh_watcher import ENTRYPOINT_MODULE, build_watcher_env
    from lingtai.kernel.refresh_watcher import encode_request

    wd = tmp_path / "wd"
    (wd / "logs").mkdir(parents=True)
    (wd / ".agent.heartbeat").write_text(str(__import__("time").time()), encoding="utf-8")
    request = _sample_request(
        taken_path=str(wd / ".refresh.taken"),
        lock_path=str(wd / ".agent.lock"),
        events_path=str(wd / "logs" / "events.jsonl"),
        stderr_log=str(wd / "logs" / "refresh_relaunch.log"),
        working_dir=str(wd),
        cmd=(sys.executable, "-c", "pass"),
    )
    (wd / ".refresh.taken").touch()
    payload = encode_request(request)
    env = build_watcher_env(request)
    # Pytest adds this src-layout checkout to the in-process import path, but
    # that path is not automatically inherited by a fresh interpreter. Point
    # the smoke-test subprocess at this exact worktree's src tree so `-m`
    # exercises the candidate module instead of any separately installed
    # LingTai version (or failing because no distribution is installed).
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")

    result = subprocess.run(
        [sys.executable, "-m", ENTRYPOINT_MODULE, payload],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    events = (wd / "logs" / "events.jsonl").read_text(encoding="utf-8")
    assert "refresh_watcher_ack" in events
    assert "refresh_watcher_already_alive" in events


def test_build_watcher_env_false_overrides_preexisting_parent_marker(monkeypatch):
    """`request.env_overwrite=False` must actually mean false: the marker
    must be absent from the built environment even if the *parent* process's
    own `os.environ` already has `LINGTAI_REFRESH_ENV_OVERWRITE=1` set (e.g.
    because this process was itself launched as a prior watcher's relaunch
    target). Without an explicit removal in the false branch, `build_watcher_env`
    would silently inherit the parent's stale `True` via the `dict(os.environ)`
    base copy, so `False` would not mean `False`. The parent's own os.environ
    must itself be left untouched by the call.
    """
    from lingtai.adapters.posix.refresh_watcher import build_watcher_env
    from lingtai.kernel.refresh_watcher import RefreshWatcherRequest

    monkeypatch.setenv("LINGTAI_REFRESH_ENV_OVERWRITE", "1")
    monkeypatch.setenv("PATH", "/test/bin")

    request = RefreshWatcherRequest(
        taken_path="/wd/.refresh.taken",
        lock_path="/wd/.agent.lock",
        events_path="/wd/logs/events.jsonl",
        stderr_log="/wd/logs/refresh_relaunch.log",
        working_dir="/wd",
        cmd=("lingtai-agent", "run", "/wd"),
        agent_name="alice",
        address="wd",
        identity_fields_json="{}",
        env_overwrite=False,
    )

    env = build_watcher_env(request)

    assert "LINGTAI_REFRESH_ENV_OVERWRITE" not in env
    assert env["PATH"] == "/test/bin"
    # The parent process's own environment is a read source, never mutated.
    assert os.environ["LINGTAI_REFRESH_ENV_OVERWRITE"] == "1"


def test_identity_fields_json_snapshot_immune_to_nested_source_mutation():
    """S8 Repair 2 regression: `runtime_identity_event_fields()` returns a
    dict whose `kernel_runtime` value is itself a nested mutable dict (in
    production, the same object as the module-level identity cache — not a
    copy). A shallow container (e.g. a tuple of `(key, value)` pairs) would
    still alias and expose that nested dict, so mutating it *after*
    `RefreshWatcherRequest` construction could silently change what the
    watcher later renders. `identity_fields_json` — a JSON string snapshot
    taken at construction time — must be immune: mutating the *source* dict
    (including its nested sub-dict) after building the request must not
    change the request's already-serialized snapshot or the rendered
    program's `identity_fields` literal.
    """
    from lingtai.kernel.refresh_watcher import RefreshWatcherRequest
    from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script

    nested = {"version": "1.2.3", "stamp": "1.2.3+git.abc123", "git_dirty": False}
    source = {
        "kernel_version": "1.2.3",
        "kernel_runtime_stamp": "1.2.3+git.abc123",
        "kernel_runtime": nested,
    }
    request = RefreshWatcherRequest(
        taken_path="/wd/.refresh.taken",
        lock_path="/wd/.agent.lock",
        events_path="/wd/logs/events.jsonl",
        stderr_log="/wd/logs/refresh_relaunch.log",
        working_dir="/wd",
        cmd=("lingtai-agent", "run", "/wd"),
        agent_name="alice",
        address="wd",
        identity_fields_json=json.dumps(source),
    )

    # Mutate the source dict AND its nested sub-dict after construction —
    # exactly the shape a shallow tuple-of-pairs would still have aliased.
    source["kernel_version"] = "MUTATED"
    nested["version"] = "MUTATED"
    nested["git_dirty"] = True
    source["kernel_runtime"]["stamp"] = "MUTATED"

    script = render_watcher_script(request)

    assert "MUTATED" not in script
    assert "1.2.3+git.abc123" in script
    tree = ast.parse(script)
    identity_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "identity_fields"
            for target in node.targets
        )
    )
    rendered = ast.literal_eval(identity_assignment.value)
    assert rendered["kernel_version"] == "1.2.3"
    assert rendered["kernel_runtime"]["version"] == "1.2.3"
    assert rendered["kernel_runtime"]["stamp"] == "1.2.3+git.abc123"
    assert rendered["kernel_runtime"]["git_dirty"] is False


@pytest.mark.parametrize(
    "bad_json",
    [
        "not json at all",
        "[1, 2, 3]",
        '"just a string"',
        "42",
        "null",
    ],
)
def test_identity_fields_json_invalid_or_non_object_fails_loudly(bad_json):
    """An invalid or non-object `identity_fields_json` must raise before any
    generated source is produced, rather than silently rendering broken or
    empty watcher-program text."""
    from lingtai.kernel.refresh_watcher import RefreshWatcherRequest
    from lingtai.kernel.refresh_watcher.watcher_program import render_watcher_script

    request = RefreshWatcherRequest(
        taken_path="/wd/.refresh.taken",
        lock_path="/wd/.agent.lock",
        events_path="/wd/logs/events.jsonl",
        stderr_log="/wd/logs/refresh_relaunch.log",
        working_dir="/wd",
        cmd=("lingtai-agent", "run", "/wd"),
        agent_name="alice",
        address="wd",
        identity_fields_json=bad_json,
    )

    with pytest.raises(ValueError):
        render_watcher_script(request)


def test_agent_explicit_none_still_composes_production_watcher(monkeypatch, tmp_path):
    """A production Agent always receives the real capability; explicit None
    means "not supplied", not an opt-out that violates the composition invariant.
    """
    from lingtai.adapters.posix.refresh_watcher import PosixRefreshWatcherAdapter
    from lingtai.agent import Agent

    captured = {}

    class CapturedComposition(Exception):
        pass

    def capture_base_agent_init(_self, **kwargs):
        captured.update(kwargs)
        raise CapturedComposition

    monkeypatch.setattr("lingtai.agent.BaseAgent.__init__", capture_base_agent_init)
    with pytest.raises(CapturedComposition):
        Agent(service=object(), working_dir=tmp_path, refresh_watcher=None)

    assert isinstance(captured["refresh_watcher"], PosixRefreshWatcherAdapter)


# ---------------------------------------------------------------------------
# Filesystem handshake
# ---------------------------------------------------------------------------


def test_perform_refresh_direct_call_synthesizes_taken(tmp_path):
    """Direct call with neither .refresh nor .refresh.taken on disk:
    `_perform_refresh` synthesizes `.refresh.taken` so the watcher's
    ack-phase poll finds it."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    wd = agent._working_dir
    assert not (wd / ".refresh").exists()
    assert not (wd / ".refresh.taken").exists()

    agent._perform_refresh()
    assert agent._refresh_watcher.spawned  # watcher subprocess spawned

    assert (wd / ".refresh.taken").exists(), \
        ".refresh.taken must exist after direct refresh — watcher polls for it"
    assert not (wd / ".refresh").exists(), \
        ".refresh must be absent so heartbeat does not spawn a duplicate watcher"


def test_perform_refresh_preserves_existing_taken(tmp_path):
    """Heartbeat path: `.refresh.taken` already exists. `_perform_refresh`
    must preserve it (not overwrite or remove) and still proceed."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    wd = agent._working_dir
    taken = wd / ".refresh.taken"
    taken.write_text("preexisting body")  # heartbeat just renamed; some marker payload

    agent._perform_refresh()
    assert agent._refresh_watcher.spawned

    assert taken.exists()
    assert taken.read_text() == "preexisting body", \
        "preexisting .refresh.taken contents must be preserved"


def test_perform_refresh_renames_existing_refresh(tmp_path):
    """Direct call but `.refresh` is on disk (e.g. tool-call path raced
    with heartbeat detection): rename .refresh → .refresh.taken instead
    of synthesizing, preserving any payload."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    wd = agent._working_dir
    refresh = wd / ".refresh"
    refresh.write_text("refresh body")
    assert not (wd / ".refresh.taken").exists()

    agent._perform_refresh()
    assert agent._refresh_watcher.spawned

    assert not refresh.exists()
    taken = wd / ".refresh.taken"
    assert taken.exists()
    assert taken.read_text() == "refresh body", \
        "rename should preserve .refresh payload as .refresh.taken"


def test_perform_refresh_removes_stale_refresh_if_both_exist(tmp_path):
    """Both .refresh and .refresh.taken on disk (rare race): preserve
    .refresh.taken (the ack the watcher polls for) and unlink .refresh
    so the heartbeat doesn't spawn a duplicate watcher next tick."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    wd = agent._working_dir
    (wd / ".refresh").write_text("racey")
    (wd / ".refresh.taken").write_text("ack")

    agent._perform_refresh()
    assert agent._refresh_watcher.spawned

    assert not (wd / ".refresh").exists()
    assert (wd / ".refresh.taken").exists()
    assert (wd / ".refresh.taken").read_text() == "ack"


# ---------------------------------------------------------------------------
# Lifecycle signaling
# ---------------------------------------------------------------------------



def test_perform_refresh_ack_write_failure_does_not_shutdown(tmp_path):
    """If the ack file cannot be established, fail safe: do not spawn
    the watcher and do not shut the running agent down. A failed refresh
    should leave the current process alive rather than killing it without
    a relaunch path.
    """
    agent = _make_agent_with_launch_cmd(tmp_path)
    wd = agent._working_dir

    real_touch = type(wd / ".refresh.taken").touch

    def touch_side_effect(self, *args, **kwargs):
        if self.name == ".refresh.taken":
            raise OSError("simulated ack write failure")
        return real_touch(self, *args, **kwargs)

    log_events = []
    agent._log = lambda event, **kw: log_events.append((event, kw))

    with patch("pathlib.Path.touch", touch_side_effect):
        agent._perform_refresh()

    assert not agent._refresh_watcher.spawned
    assert not (wd / ".refresh.taken").exists()
    assert not agent._shutdown.is_set()
    assert not agent._cancel_event.is_set()
    assert any(event == "refresh_ack_failed" for event, _ in log_events)


def test_perform_refresh_sets_shutdown_and_cancel(tmp_path):
    """Direct callers (tool-call refresh, AED preset fallback) don't go
    through the heartbeat's shutdown-set step. `_perform_refresh` must
    set both `_shutdown` and `_cancel_event` itself so the run loop
    exits, the lock releases, and the watcher's second phase completes.
    """
    agent = _make_agent_with_launch_cmd(tmp_path)
    assert not agent._shutdown.is_set()
    assert not agent._cancel_event.is_set()

    agent._perform_refresh()

    assert agent._shutdown.is_set(), \
        "_perform_refresh must set _shutdown so the run loop exits and the lock releases"
    assert agent._cancel_event.is_set(), \
        "_perform_refresh must set _cancel_event so in-flight turn work yields"


def test_perform_refresh_skips_chat_history_save_when_interface_poisoned(tmp_path):
    """A poisoned interface must not be serialized: `_perform_refresh` skips
    the chat-history save and logs the skip reason, but still relaunches."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    agent._llm_worker_interface_poisoned = True

    def fail_save(*_args, **_kwargs):
        raise AssertionError("poisoned refresh must not save chat history")

    agent._save_chat_history = fail_save
    log_events = []
    real_log = agent._log

    def log_capture(event, **kw):
        log_events.append((event, kw))
        return real_log(event, **kw)

    agent._log = log_capture

    agent._perform_refresh()

    assert agent._refresh_watcher.spawned
    assert any(
        event == "refresh_chat_history_save_skipped"
        and fields.get("reason") == "worker_still_running_interface_unsafe"
        for event, fields in log_events
    )


def test_perform_refresh_no_launch_cmd_skips_handshake(tmp_path):
    """When `_build_launch_cmd()` returns None (e.g. bare BaseAgent),
    `_perform_refresh` logs and returns BEFORE touching the handshake or
    signaling shutdown — those signals would orphan the agent without a
    relaunch to recover it."""
    from lingtai.kernel.base_agent import BaseAgent
    wd = tmp_path / "test"
    wd.mkdir(exist_ok=True)
    agent = BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name="alice",
        working_dir=wd, workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(wd),
        refresh_watcher=make_test_refresh_watcher(),
    )
    # Default _build_launch_cmd returns None — do not override.

    agent._perform_refresh()
    assert not agent._refresh_watcher.spawned

    assert not (wd / ".refresh.taken").exists(), \
        "no-launch-cmd path must not synthesize the ack file"
    assert not agent._shutdown.is_set(), \
        "no-launch-cmd path must not orphan the agent by setting shutdown"


def test_perform_refresh_no_launch_cmd_works_without_refresh_watcher(tmp_path):
    """A raw BaseAgent built with no `refresh_watcher` (the ~230 unrelated
    construction sites across the suite) must still construct and no-op
    `_perform_refresh` cleanly when there is no launch command — the
    no-refresh path never touches the Port."""
    from lingtai.kernel.base_agent import BaseAgent
    wd = tmp_path / "test"
    wd.mkdir(exist_ok=True)
    agent = BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name="alice",
        working_dir=wd, workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(wd),
        # refresh_watcher omitted entirely — must default to None and construct.
    )
    assert agent._refresh_watcher is None
    # Default _build_launch_cmd returns None — do not override.

    agent._perform_refresh()  # must not raise

    assert not (wd / ".refresh.taken").exists()
    assert not agent._shutdown.is_set()


def test_perform_refresh_real_launch_cmd_without_watcher_raises_before_handshake(tmp_path):
    """A real launch command with no injected `refresh_watcher` must fail
    loudly — but only before any handshake or shutdown mutation, so a missing
    Port never orphans the agent mid-handshake."""
    from lingtai.kernel.base_agent import BaseAgent
    wd = tmp_path / "test"
    wd.mkdir(exist_ok=True)
    (wd / "logs").mkdir(exist_ok=True)
    agent = BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name="alice",
        working_dir=wd, workdir_lease=make_test_lease(),
        snapshot_port=make_test_snapshot_port(), agent_presence=make_test_presence_store(), lifecycle_clock=make_test_lifecycle_clock(), source_revision_port=make_test_source_revision_port(), notification_store=notification_store_for(wd),
        # refresh_watcher omitted entirely — must default to None.
    )
    agent._build_launch_cmd = lambda: ["python", "-c", "print('relaunch sentinel')"]

    with pytest.raises(RuntimeError, match="RefreshWatcherPort"):
        agent._perform_refresh()

    assert not (wd / ".refresh").exists()
    assert not (wd / ".refresh.taken").exists(), \
        "missing-Port failure must precede handshake mutation"
    assert not agent._shutdown.is_set(), \
        "missing-Port failure must precede shutdown signaling"
    assert not agent._cancel_event.is_set()


def test_perform_refresh_logs_handshake_source(tmp_path):
    """`refresh_deferred_relaunch` event carries a `handshake` field so
    production telemetry can confirm which code path executed."""
    agent = _make_agent_with_launch_cmd(tmp_path)

    log_events = []
    real_log = agent._log

    def log_capture(event, **kw):
        log_events.append((event, kw))
        return real_log(event, **kw)

    agent._log = log_capture

    agent._perform_refresh()

    relaunch_events = [
        (e, kw) for e, kw in log_events if e == "refresh_deferred_relaunch"
    ]
    assert len(relaunch_events) == 1
    _, kw = relaunch_events[0]
    assert kw.get("handshake") == "synthesized_direct_call", \
        f"expected synthesized_direct_call, got {kw.get('handshake')!r}"


def test_perform_refresh_watcher_marks_env_file_overwrite(tmp_path):
    """A refresh relaunch inherits the old process environment. Mark the
    watcher process so the relaunched agent overwrites stale env_file values
    with freshly edited .env contents.
    """
    agent = _make_agent_with_launch_cmd(tmp_path)

    agent._perform_refresh()

    assert agent._refresh_watcher.spawned
    assert agent._refresh_watcher.last_env["LINGTAI_REFRESH_ENV_OVERWRITE"] == "1"


def test_refresh_watcher_script_cleans_stale_duplicate_process(tmp_path):
    """Production incident 2026-06-04: refresh watcher relaunches can be
    blocked by a stale LingTai run process. The watcher script
    must detect the duplicate-process guard stderr and terminate only a stale
    same-agent process (no fresh heartbeat) before retrying.
    """
    agent = _make_agent_with_launch_cmd(tmp_path)

    agent._perform_refresh()

    assert agent._refresh_watcher.spawned
    script = agent._refresh_watcher.last_script
    assert "another lingtai agent is already running" in script
    assert "def _cleanup_stale_duplicate" in script
    assert "def match_agent_run" in script
    assert "match_agent_run(cmdline, wd) is not None" in script
    assert "heartbeat_age" in script
    assert "signal.SIGTERM" in script
    assert "signal.SIGKILL" in script
    assert "refresh_watcher_stale_duplicate" in script


# ---------------------------------------------------------------------------
# Relaunch watcher secret redaction (T3)
#
# The watcher subprocess writes events.jsonl through its own inline `log()`,
# bypassing CompositeLoggingService.redact_for_trajectory. Secret-shaped values
# can reach those events via `stderr_tail` (subprocess stderr, e.g. a config
# traceback echoing a token), `cmdline` (process command line), and `error`
# strings. The generated script must redact string fields before persisting.
#
# All tokens below are FAKE, fixed-shape values used only to exercise the
# redaction regexes — they are not, and never were, live credentials.
# ---------------------------------------------------------------------------

# Fake, structurally-valid-shaped credentials (not real secrets).
_FAKE_TELEGRAM_TOKEN = "123456789:" + "A" * 35
_FAKE_BEARER = "Bearer " + "a1b2c3" + "d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9.t0"
_FAKE_OPENAI_KEY = "sk-" + "x" * 40
_FAKE_ENV_ASSIGN = "BOT_TOKEN=" + "z" * 20


def _extract_relaunch_script(agent):
    agent._perform_refresh()
    assert agent._refresh_watcher.spawned
    return agent._refresh_watcher.last_script


def test_refresh_watcher_script_embeds_redactor(tmp_path):
    """The generated watcher script must wire in the kernel redactor at its
    single events.jsonl write chokepoint so stderr/cmdline/error previews are
    redacted before persistence — not left raw like CompositeLoggingService
    avoids."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    script = _extract_relaunch_script(agent)
    # The redactor is sourced from the kernel module (single source of truth)
    # and applied to the whole event dict inside log() before the JSONL write.
    # Use redact_for_trajectory (not just redact_text value-walking) for
    # key-aware parity with normal trajectory logging.
    assert "trace_redaction" in script
    assert "redact_for_trajectory" in script
    # The write chokepoint (json.dumps(entry)) must come after a redaction step.
    assert "_redact_for_trajectory(entry)" in script
    # Degradation must be diagnosable via a non-secret marker, not silent.
    assert "redaction_unavailable" in script


def test_refresh_watcher_log_redacts_secret_fields(tmp_path):
    """Executing the generated log() with secret-shaped string fields must
    write a redacted events.jsonl record. Uses fake tokens only."""
    import json as _json
    import re as _re

    agent = _make_agent_with_launch_cmd(tmp_path)
    events_path = agent._working_dir / "logs" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)

    script = _extract_relaunch_script(agent)

    # Slice the script prefix up to and including the log() definition, then
    # run just that prefix plus an explicit log() call. This avoids executing
    # the watcher's blocking relaunch loop while exercising the real write path.
    marker = "deadline = time.time() + 60\n"
    assert marker in script, "expected loop-start marker to slice the script"
    prefix = script.split(marker, 1)[0]

    fake_stderr_tail = (
        "Traceback (most recent call last):\n"
        f"  config error: telegram bot_token={_FAKE_TELEGRAM_TOKEN}\n"
        f"  auth header: {_FAKE_BEARER}\n"
        f"  openai: {_FAKE_OPENAI_KEY}\n"
        f"  env: {_FAKE_ENV_ASSIGN}\n"
    )
    fake_cmdline = f"lingtai run {agent._working_dir} --token {_FAKE_OPENAI_KEY}"

    call = (
        "log('refresh_watcher_relaunch_dead', attempt=1, pid=4242, "
        "stderr_tail=_TEST_STDERR[-500:])\n"
        "log('refresh_watcher_stale_duplicate_terminate', attempt=1, pid=4242, "
        "heartbeat_age=99.0, cmdline=_TEST_CMDLINE[-300:])\n"
    )
    ns = {"_TEST_STDERR": fake_stderr_tail, "_TEST_CMDLINE": fake_cmdline}
    exec(compile(prefix + call, "<relaunch_script>", "exec"), ns)

    raw = events_path.read_text(encoding="utf-8")
    # No fake token shape may survive into the durable event log.
    assert _FAKE_TELEGRAM_TOKEN not in raw
    assert _FAKE_OPENAI_KEY not in raw
    # The bearer credential body must be gone (the literal word "Bearer" may
    # remain as part of the redaction placeholder).
    assert "d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9" not in raw
    assert "REDACTED" in raw

    # The records are still valid JSON with their type/metadata intact.
    records = [_json.loads(line) for line in raw.splitlines() if line.strip()]
    types = {r["type"] for r in records}
    assert "refresh_watcher_relaunch_dead" in types
    assert "refresh_watcher_stale_duplicate_terminate" in types
    dead = next(r for r in records if r["type"] == "refresh_watcher_relaunch_dead")
    assert dead["attempt"] == 1
    assert dead["pid"] == 4242
    assert _re.search(r"REDACTED", dead["stderr_tail"])


def test_refresh_watcher_log_redacts_secret_named_key_value(tmp_path):
    """Whole-entry redact_for_trajectory must remove a value under a secret-named
    key even when the value does not match any provider token shape — the
    key-aware parity the value-walking redact_text path lacked. Uses a fake,
    non-token-shaped password value only."""
    import json as _json

    agent = _make_agent_with_launch_cmd(tmp_path)
    events_path = agent._working_dir / "logs" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)

    script = _extract_relaunch_script(agent)
    marker = "deadline = time.time() + 60\n"
    assert marker in script
    prefix = script.split(marker, 1)[0]

    # A plausible app password: ordinary characters, no token prefix/shape, so
    # redact_text alone would leave it raw. The secret-named key triggers
    # key-aware redaction in redact_for_trajectory.
    fake_app_password = "hunter2-correct-horse-battery"
    call = (
        "log('refresh_watcher_relaunch_error', attempt=1, "
        "email_password=_TEST_PW)\n"
    )
    ns = {"_TEST_PW": fake_app_password}
    exec(compile(prefix + call, "<relaunch_script>", "exec"), ns)

    raw = events_path.read_text(encoding="utf-8")
    assert fake_app_password not in raw
    assert "REDACTED" in raw
    records = [_json.loads(line) for line in raw.splitlines() if line.strip()]
    record = next(r for r in records if r["type"] == "refresh_watcher_relaunch_error")
    assert record["email_password"] == "<REDACTED:secret>"


def test_refresh_watcher_log_marks_redaction_unavailable_on_import_failure(tmp_path):
    """If the kernel redactor cannot be imported, the watcher must fail open to
    keep relaunch reliable, but stamp a non-secret `redaction_unavailable=True`
    marker so the security degradation is diagnosable rather than silent."""
    import json as _json

    agent = _make_agent_with_launch_cmd(tmp_path)
    events_path = agent._working_dir / "logs" / "events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)

    script = _extract_relaunch_script(agent)
    marker = "deadline = time.time() + 60\n"
    assert marker in script
    prefix = script.split(marker, 1)[0]

    # Simulate the import failure path by forcing the fallback identity redactor
    # and the import-failed flag, then logging a benign event.
    call = (
        "_REDACTOR_IMPORT_OK = False\n"
        "log('refresh_watcher_relaunch', attempt=1)\n"
    )
    exec(compile(prefix + call, "<relaunch_script>", "exec"), {})

    raw = events_path.read_text(encoding="utf-8")
    records = [_json.loads(line) for line in raw.splitlines() if line.strip()]
    record = next(r for r in records if r["type"] == "refresh_watcher_relaunch")
    assert record["attempt"] == 1
    assert record["redaction_unavailable"] is True


def test_refresh_watcher_failure_metadata_bounds_and_redacts_cleanup_and_relaunch_errors(tmp_path):
    """S8 bugfix regression: `_failure_metadata()` previously bounded and
    redacted only `last_stderr_tail`; `last_cleanup_error` and
    `last_relaunch_error` (raw `str(exception)`) passed through unbounded and
    unredacted. Both must now be bounded/redacted identically before reaching
    `refresh_failed_permanent.json` / the operator notification / the final
    event.

    Reaching a real `last_cleanup_error`/`last_relaunch_error` value through
    the full real-interpreter relaunch loop requires a genuine `os.kill`
    permission failure or an unlaunchable relaunch command, which is not a
    reliable, portable way to trigger the bug deterministically in a fast
    test — so this test directly exercises the rendered program's own
    `_failure_metadata()` function (sliced from the real generated script,
    the same technique the redaction tests above use) with a synthetic
    `failure_state` carrying oversized, secret-shaped values in both fields,
    proving the fix in the actual generated code rather than a reimplementation.
    """
    import json as _json

    agent = _make_agent_with_launch_cmd(tmp_path)
    script = _extract_relaunch_script(agent)
    marker = "deadline = time.time() + 60\n"
    assert marker in script
    prefix = script.split(marker, 1)[0]

    oversized_cleanup_error = "x" * 2000 + f" openai api_key={_FAKE_OPENAI_KEY}"
    oversized_relaunch_error = "y" * 2000 + f" openai api_key={_FAKE_OPENAI_KEY}"
    call = (
        "failure_state['last_cleanup_error'] = _TEST_CLEANUP_ERR\n"
        "failure_state['last_relaunch_error'] = _TEST_RELAUNCH_ERR\n"
        "_TEST_RESULT['meta'] = _failure_metadata()\n"
    )
    ns = {
        "_TEST_CLEANUP_ERR": oversized_cleanup_error,
        "_TEST_RELAUNCH_ERR": oversized_relaunch_error,
        "_TEST_RESULT": {},
    }
    exec(compile(prefix + call, "<relaunch_script>", "exec"), ns)

    meta = ns["_TEST_RESULT"]["meta"]
    assert len(meta["last_cleanup_error"]) <= 1200
    assert len(meta["last_relaunch_error"]) <= 1200
    assert _FAKE_OPENAI_KEY not in meta["last_cleanup_error"]
    assert _FAKE_OPENAI_KEY not in meta["last_relaunch_error"]
    assert "REDACTED" in meta["last_cleanup_error"]
    assert "REDACTED" in meta["last_relaunch_error"]
    # metadata must still be JSON-serializable (the artifact/notification sink).
    _json.dumps(meta)


# ---------------------------------------------------------------------------
# Terminal refresh-failure visibility (PR #292)
#
# When relaunch attempts are permanently exhausted the watcher must make the
# failure visible: log `refresh_failed_permanent`, write
# logs/refresh_failed_permanent.json, and publish a high-priority system
# notification carrying attempts / duplicate-PID / heartbeat / stderr /
# cleanup / relaunch / guidance metadata.
# ---------------------------------------------------------------------------


def test_refresh_watcher_permanent_failure_writes_operator_alert(tmp_path):
    """Terminal relaunch failure writes both the operator notification and
    the durable failure artifact with bounded, actionable metadata.
    """
    stderr_text = (
        "x" * 3000
        + f"\nopenai api_key={_FAKE_OPENAI_KEY}\n"
        + "\nanother lingtai agent is already running\n"
        + "PID 424242: stale duplicate\n"
    )
    launch_cmd = [
        sys.executable,
        "-c",
        f"import sys; sys.stderr.write({stderr_text!r}); sys.exit(7)",
    ]
    agent = _make_agent_with_launch_cmd(tmp_path, launch_cmd=launch_cmd)
    wd = agent._working_dir
    script = _fast_watcher_script(_capture_watcher_script(agent))
    agent._workdir_lease.release()

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    notification = _read_json(wd / ".notification" / "system.json")
    event = notification["data"]["events"][-1]
    metadata = event["metadata"]
    artifact = _read_json(wd / "logs" / "refresh_failed_permanent.json")

    assert notification["priority"] == "high"
    assert event["source"] == "refresh"
    assert event["ref_id"] == "refresh_failed_permanent"
    assert metadata["attempts"] == 2
    assert metadata["last_pid"] == 424242
    assert metadata["last_duplicate_pid"] == 424242
    assert metadata["last_heartbeat_status"] == "missing"
    assert metadata["last_cleanup_action"] == "inspect_duplicate_guard"
    assert metadata["last_cleanup_result"] == "skipped_not_same_agent"
    assert len(metadata["last_stderr_tail"]) <= 1200
    assert _FAKE_OPENAI_KEY not in metadata["last_stderr_tail"]
    assert "REDACTED" in metadata["last_stderr_tail"]
    notification_raw = (wd / ".notification" / "system.json").read_text(
        encoding="utf-8"
    )
    artifact_raw = (wd / "logs" / "refresh_failed_permanent.json").read_text(
        encoding="utf-8"
    )
    assert _FAKE_OPENAI_KEY not in notification_raw
    assert _FAKE_OPENAI_KEY not in artifact_raw
    assert "REDACTED" in notification_raw
    assert "REDACTED" in artifact_raw
    assert "logs/refresh_relaunch.log" in metadata["recovery_guidance"][0]
    assert any(".agent.lock" in item for item in metadata["recovery_guidance"])
    assert artifact["type"] == "refresh_failed_permanent"
    assert artifact["metadata"] == metadata

    events = [
        json.loads(line)
        for line in (wd / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    final = [event for event in events if event["type"] == "refresh_failed_permanent"][-1]
    assert final["last_duplicate_pid"] == 424242
    assert final["last_cleanup_result"] == "skipped_not_same_agent"
    assert _FAKE_OPENAI_KEY not in final["last_stderr_tail"]
    assert "REDACTED" in final["last_stderr_tail"]
    assert final["artifact_path"].endswith("logs/refresh_failed_permanent.json")


def test_refresh_watcher_cleanup_then_success_does_not_write_failure_alert(tmp_path):
    """A retry that enters stale-duplicate cleanup and then succeeds must not
    leave a permanent-failure notification behind.
    """
    agent = _make_agent_with_launch_cmd(tmp_path)
    wd = agent._working_dir
    fake_console = tmp_path / "lingtai-agent"
    fake_console.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
    fake_console.chmod(0o755)
    duplicate = subprocess.Popen(
        [str(fake_console), "run", str(wd)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    launch_code = textwrap.dedent(
        f"""
        import pathlib
        import sys
        import time
        wd = pathlib.Path({str(wd)!r})
        counter = wd / 'refresh_attempt_counter'
        if not counter.exists():
            counter.write_text('1', encoding='utf-8')
            sys.stderr.write('another lingtai agent is already running\\n')
            sys.stderr.write('PID {duplicate.pid}: same agent\\n')
            sys.exit(1)
        (wd / '.agent.heartbeat').write_text(str(time.time()), encoding='utf-8')
        """
    )
    agent._build_launch_cmd = lambda: [sys.executable, "-c", launch_code]

    try:
        script = _fast_watcher_script(_capture_watcher_script(agent))
        agent._workdir_lease.release()
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
    finally:
        try:
            duplicate.terminate()
            duplicate.wait(timeout=1)
        except Exception:
            duplicate.kill()
            duplicate.wait(timeout=1)

    assert result.returncode == 0, result.stderr
    assert not (wd / ".notification" / "system.json").exists()
    assert not (wd / "logs" / "refresh_failed_permanent.json").exists()

    events = [
        json.loads(line)
        for line in (wd / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_types = [event["type"] for event in events]
    assert "refresh_watcher_stale_duplicate_terminate" in event_types
    assert "refresh_watcher_success" in event_types
    assert "refresh_failed_permanent" not in event_types
