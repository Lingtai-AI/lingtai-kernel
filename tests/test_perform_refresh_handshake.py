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
exercise that contract without spawning a real subprocess.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "p"
    svc.model = "m"
    return svc


def _make_agent_with_launch_cmd(tmp_path, agent_name="alice"):
    """Build a bare BaseAgent and rebind `_build_launch_cmd` so the
    refresh path proceeds past the `cmd is None` early return. The
    actual subprocess call is patched in each test to avoid relaunches.
    """
    from lingtai_kernel.base_agent import BaseAgent
    wd = tmp_path / "test"
    wd.mkdir(exist_ok=True)
    agent = BaseAgent(
        service=make_mock_service(),
        agent_name=agent_name,
        working_dir=wd,
    )
    # BaseAgent._build_launch_cmd returns None; rebind to a sentinel
    # list so the handshake/signal code runs.
    agent._build_launch_cmd = lambda: ["python", "-c", "print('relaunch sentinel')"]
    return agent


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

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()
        assert mock_popen.called  # watcher subprocess spawned

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

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()
        assert mock_popen.called

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

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()
        assert mock_popen.called

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

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()
        assert mock_popen.called

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

    with patch("pathlib.Path.touch", touch_side_effect), \
         patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()

    assert not mock_popen.called
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

    with patch("subprocess.Popen"):
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

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()

    assert mock_popen.called
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
    from lingtai_kernel.base_agent import BaseAgent
    wd = tmp_path / "test"
    wd.mkdir(exist_ok=True)
    agent = BaseAgent(
        service=make_mock_service(),
        agent_name="alice",
        working_dir=wd,
    )
    # Default _build_launch_cmd returns None — do not override.

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()
        assert not mock_popen.called

    assert not (wd / ".refresh.taken").exists(), \
        "no-launch-cmd path must not synthesize the ack file"
    assert not agent._shutdown.is_set(), \
        "no-launch-cmd path must not orphan the agent by setting shutdown"


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

    with patch("subprocess.Popen"):
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

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()

    assert mock_popen.called
    _, kwargs = mock_popen.call_args
    assert kwargs["env"]["LINGTAI_REFRESH_ENV_OVERWRITE"] == "1"


def test_refresh_watcher_script_cleans_stale_duplicate_process(tmp_path):
    """Production incident 2026-06-04: refresh watcher relaunches can be
    blocked by a stale `lingtai run <agent-dir>` process. The watcher script
    must detect the duplicate-process guard stderr and terminate only a stale
    same-agent process (no fresh heartbeat) before retrying.
    """
    agent = _make_agent_with_launch_cmd(tmp_path)

    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()

    assert mock_popen.called
    args, _kwargs = mock_popen.call_args
    script = args[0][2]
    assert "another lingtai agent is already running" in script
    assert "def _cleanup_stale_duplicate" in script
    assert "('lingtai run ' + wd) in cmdline" in script
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
    with patch("subprocess.Popen") as mock_popen:
        agent._perform_refresh()
    assert mock_popen.called
    args, _kwargs = mock_popen.call_args
    return args[0][2]


def test_refresh_watcher_script_embeds_redactor(tmp_path):
    """The generated watcher script must wire in the kernel redactor at its
    single events.jsonl write chokepoint so stderr/cmdline/error previews are
    redacted before persistence — not left raw like CompositeLoggingService
    avoids."""
    agent = _make_agent_with_launch_cmd(tmp_path)
    script = _extract_relaunch_script(agent)
    # The redactor is sourced from the kernel module (single source of truth)
    # and applied inside log() before the JSONL write.
    assert "trace_redaction" in script
    assert "redact_text" in script
    # The write chokepoint (json.dumps(entry)) must come after a redaction step.
    assert "_redact_entry" in script or "redact_text" in script


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
