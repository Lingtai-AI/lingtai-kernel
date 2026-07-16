"""Regression tests for claude-p / claude-code source-usage persistence.

Covers the production state-transition ordering for both the initial
``claude --print`` emanation (``_run_claude_code_emanation``) and the
resumed ``claude --resume`` follow-up (``_run_ask_claude_code_stream``):
usage must be buffered during the stream and persisted exactly once, only
after terminal classification (cancellation/timeout, exit code, is_error)
accepts the run as successful. This mirrors the already-fixed Cursor
backend (see ``tests/test_daemon_cursor_backend.py``).

Initial-path tests monkey-patch ``subprocess.Popen`` with the finite fake
proc used elsewhere in this suite. Resume-path tests drive
``_run_ask_claude_code_stream`` directly against a live ``_FakeProc`` so the
post-EOF wait/deadline race can be reproduced deterministically.
"""
from __future__ import annotations

import json
import threading
import time
from unittest.mock import patch

import pytest

from lingtai.tools.daemon import _normalize_claude_usage
from lingtai.tools.daemon.process_port import DaemonProcessCommand
from tests._daemon_helpers import (
    FiniteFakeProc,
    make_daemon_agent,
    make_daemon_run_dir,
    register_daemon_entry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_run_dir(agent, *, handle="em-claude-usage"):
    return make_daemon_run_dir(
        agent,
        handle=handle,
        task="dummy task",
        tools=[],
        model="claude",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="claude-p",
    )


def _claude_usage(**overrides):
    usage = {
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 2,
    }
    usage.update(overrides)
    return usage


def _claude_result(*, session_id="claude-session-XYZ", result="done", **overrides):
    event = {
        "type": "result",
        "session_id": session_id,
        "result": result,
        "is_error": False,
        "usage": _claude_usage(),
    }
    event.update(overrides)
    return event


def _source_claude_stream(*events):
    return [json.dumps(event) + "\n" for event in events]


def _usage_events(run_dir):
    return [
        json.loads(line) for line in run_dir.events_path.read_text().splitlines()
        if json.loads(line).get("event") == "cli_usage"
    ]


# ---------------------------------------------------------------------------
# Strict normalizer contract (bool/negative rejection) — see also
# tests/test_daemon_claude_usage.py for the unit-level table.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "usage",
    [
        _claude_usage(input_tokens=-1),
        _claude_usage(output_tokens=True),
        _claude_usage(cache_read_input_tokens=-5),
        {"input_tokens": "lots", "output_tokens": 7},
    ],
)
def test_claude_usage_rejects_invalid_fields(usage):
    assert _normalize_claude_usage(usage) is None


# ---------------------------------------------------------------------------
# Initial emanation: buffering + terminal-classification ordering
# ---------------------------------------------------------------------------


def test_claude_initial_valid_usage_is_buffered_until_success(tmp_path):
    """A successful run persists exactly one buffered usage candidate."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    result_event = _claude_result()
    run_dir = _make_run_dir(agent, handle="em-claude-initial-success")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(result_event),
        ),
    ):
        result = mgr._run_claude_code_emanation(
            "em-claude-initial-success", run_dir, "Use source usage.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "done"
    assert state["cli_tokens"] == {
        "input": 100, "output": 10, "cached": 7, "thinking": 0, "calls": 1,
    }
    usage_events = _usage_events(run_dir)
    assert len(usage_events) == 1
    assert usage_events[0]["raw"] == result_event["usage"]
    assert not run_dir.token_ledger_path.exists()
    assert not (agent._working_dir / "logs" / "token_ledger.jsonl").exists()


def test_claude_initial_duplicate_terminal_events_account_once(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    first = _claude_result(result="first")
    second = _claude_result(result="second")
    run_dir = _make_run_dir(agent, handle="em-claude-duplicate")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(first, second),
        ),
    ):
        result = mgr._run_claude_code_emanation(
            "em-claude-duplicate", run_dir, "Count one terminal.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "second"
    assert state["cli_tokens"]["calls"] == 1
    assert len(_usage_events(run_dir)) == 1


def test_claude_initial_missing_primary_usage_is_not_recorded(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    missing_input = _claude_result(result="missing input", usage={
        "output_tokens": 10,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2,
    })
    missing_output = _claude_result(result="missing output", usage={
        "input_tokens": 100,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2,
    })
    run_dir = _make_run_dir(agent, handle="em-claude-missing-primary-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(missing_input, missing_output),
        ),
    ):
        result = mgr._run_claude_code_emanation(
            "em-claude-missing-primary-usage", run_dir,
            "Ignore incomplete usage.", threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "missing output"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


def test_claude_initial_invalid_and_zero_usage_events_are_not_recorded(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    malformed = _claude_result(result="malformed", usage={
        "input_tokens": "100", "output_tokens": 10,
        "cache_read_input_tokens": 5, "cache_creation_input_tokens": 2,
    })
    zero = _claude_result(result="zero", usage=_claude_usage(
        input_tokens=0, output_tokens=0,
        cache_read_input_tokens=0, cache_creation_input_tokens=0,
    ))
    run_dir = _make_run_dir(agent, handle="em-claude-invalid-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(malformed, zero),
        ),
    ):
        result = mgr._run_claude_code_emanation(
            "em-claude-invalid-usage", run_dir, "Ignore malformed usage.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "zero"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


@pytest.mark.parametrize(
    ("tail_event", "returncode", "error_text"),
    [
        (
            {"type": "result", "result": "initial structured failure",
             "is_error": True},
            0,
            "is_error=true",
        ),
        (None, 7, "exited"),
    ],
)
def test_claude_initial_valid_usage_is_buffered_until_failure_classified(
    tmp_path, tail_event, returncode, error_text,
):
    """A valid usage candidate from an earlier result line must NOT be
    persisted if the run is later classified as failed (nonzero exit or
    is_error=true on a later/duplicate terminal line)."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    success = _claude_result(result="initial success")
    events = [success] + ([tail_event] if tail_event is not None else [])
    run_dir = _make_run_dir(agent, handle="em-claude-initial-failed-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(*events),
            returncode=returncode,
        ),
    ):
        with pytest.raises(RuntimeError, match=error_text):
            mgr._run_claude_code_emanation(
                "em-claude-initial-failed-usage", run_dir, "Fail after usage.",
                threading.Event(), threading.Event(),
            )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


def test_claude_initial_post_eof_cancel_cannot_persist_usage_or_mark_done(tmp_path):
    """REGRESSION (root cause): a watchdog/manual cancel that fires while
    the worker is blocked in ``proc.wait()`` after stdout EOF — i.e. after
    a valid terminal ``result`` line already arrived — must still produce
    a cancelled/timeout outcome, not a false 'done' with persisted usage.
    """
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    timeout_event = threading.Event()

    class WaitSignalsCancel(FiniteFakeProc):
        def wait(self, timeout=None):
            timeout_event.set()
            cancel.set()
            return super().wait(timeout)

    run_dir = _make_run_dir(agent, handle="em-claude-post-eof-cancel")
    proc = WaitSignalsCancel(
        stdout_lines=_source_claude_stream(
            _claude_result(result="valid before post-EOF cancel"),
        ),
    )

    with patch("lingtai.tools.daemon.subprocess.Popen", return_value=proc):
        result = mgr._run_claude_code_emanation(
            "em-claude-post-eof-cancel", run_dir, "Cancel after EOF.",
            cancel, timeout_event,
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "[cancelled]"
    assert state["state"] == "timeout"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


def test_claude_initial_manual_cancel_after_eof_marks_cancelled_not_timeout(tmp_path):
    """Same post-EOF race, but a manual reclaim (cancel_event only, no
    timeout_event) must mark ``cancelled`` rather than ``timeout``."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    timeout_event = threading.Event()

    class WaitSignalsCancel(FiniteFakeProc):
        def wait(self, timeout=None):
            cancel.set()
            return super().wait(timeout)

    run_dir = _make_run_dir(agent, handle="em-claude-post-eof-manual-cancel")
    proc = WaitSignalsCancel(
        stdout_lines=_source_claude_stream(
            _claude_result(result="valid before post-EOF manual cancel"),
        ),
    )

    with patch("lingtai.tools.daemon.subprocess.Popen", return_value=proc):
        result = mgr._run_claude_code_emanation(
            "em-claude-post-eof-manual-cancel", run_dir, "Cancel after EOF.",
            cancel, timeout_event,
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "[cancelled]"
    assert state["state"] == "cancelled"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


# ---------------------------------------------------------------------------
# Initial emanation: _require_done_completion is itself a terminal gate.
# When daemon_common MCP is loaded, a missing/bad finish() must fail the
# run and persist no usage — mirrors _write_completion/_mark_common_mcp_loaded
# from tests/test_daemon_claude_p_background_guard.py.
# ---------------------------------------------------------------------------


def _write_completion(run_dir, status: str, **extra) -> None:
    payload = {
        "schema": "lingtai.daemon_completion.v1",
        "status": status,
        "run_id": run_dir.run_id,
    }
    payload.update(extra)
    (run_dir.path / "daemon_completion.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )


def _mark_common_mcp_loaded(run_dir) -> None:
    run_dir._state.setdefault("call_parameters", {})["mcp"] = [
        {"name": "daemon_common", "transport": "stdio"}
    ]
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)


def test_claude_initial_missing_finish_fails_before_usage_persistence(tmp_path):
    """A run that exits 0 with a valid usage-bearing result, but never
    calls finish() while daemon_common MCP is loaded, must be rejected by
    _require_done_completion before any usage is persisted or mark_done
    runs."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-missing-finish")
    _mark_common_mcp_loaded(run_dir)

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(
                _claude_result(result="Done without finish."),
            ),
        ),
    ):
        with pytest.raises(RuntimeError, match="missing completion"):
            mgr._run_claude_code_emanation(
                "em-claude-missing-finish", run_dir, "Do work.",
                threading.Event(), threading.Event(),
            )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


def test_claude_initial_bad_finish_status_fails_before_usage_persistence(tmp_path):
    """Same gate, but finish() was called with a non-done status."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-bad-finish")
    _mark_common_mcp_loaded(run_dir)
    _write_completion(run_dir, "failed", reason="blocked on review")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(
                _claude_result(result="I could not finish."),
            ),
        ),
    ):
        with pytest.raises(RuntimeError):
            mgr._run_claude_code_emanation(
                "em-claude-bad-finish", run_dir, "Do work.",
                threading.Event(), threading.Event(),
            )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["cli_tokens"]["calls"] == 0
    assert _usage_events(run_dir) == []


def test_claude_initial_done_finish_persists_usage_after_gate(tmp_path):
    """Positive case: once finish(done) is recorded, the buffered usage
    candidate is persisted and the run is marked done — proving the gate
    relocation didn't just make every daemon_common run fail closed."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-done-finish")
    _mark_common_mcp_loaded(run_dir)
    _write_completion(run_dir, "done", summary="completed")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(
                _claude_result(result="Done. Suite green."),
            ),
        ),
    ):
        result = mgr._run_claude_code_emanation(
            "em-claude-done-finish", run_dir, "Do work.",
            threading.Event(), threading.Event(),
        )

    assert result == "Done. Suite green."
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "done"
    assert state["cli_tokens"] == {
        "input": 100, "output": 10, "cached": 7, "thinking": 0, "calls": 1,
    }
    assert len(_usage_events(run_dir)) == 1


# ---------------------------------------------------------------------------
# Resume follow-up: buffering + deadline/error ordering
# ---------------------------------------------------------------------------


class _FakeAskStream:
    """Line-iterable stream the test can append to and close on demand."""

    def __init__(self):
        self._lock = threading.Lock()
        self._lines: list[str] = []
        self._closed = False
        self._cond = threading.Condition(self._lock)

    def feed(self, line: str) -> None:
        with self._cond:
            self._lines.append(line)
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def __iter__(self):
        return self

    def __next__(self):
        with self._cond:
            while not self._lines and not self._closed:
                self._cond.wait()
            if self._lines:
                return self._lines.pop(0)
            raise StopIteration


class _FakeAskProc:
    """subprocess.Popen stand-in with controllable stdout for ask workers."""

    def __init__(self):
        self.stdout = _FakeAskStream()
        self.stderr = _FakeAskStream()
        self.returncode: int | None = None
        self.pid = 0
        self._wait_evt = threading.Event()

    def finish(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout.close()
        self.stderr.close()
        self._wait_evt.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        import subprocess as _sp
        if not self._wait_evt.wait(timeout=timeout):
            raise _sp.TimeoutExpired(cmd=["fake"], timeout=timeout)
        return self.returncode


def _port_handle(mgr, proc):
    with patch("lingtai.tools.daemon.posix_process.subprocess.Popen",
               return_value=proc):
        return mgr._process_port.spawn(
            DaemonProcessCommand(("claude",), mgr._agent._working_dir),
            group_id=None,
        )


def _ask_entry(run_dir, backend="claude-code"):
    return {
        "future": None,
        "task": "x",
        "start_time": time.time(),
        "cancel_event": threading.Event(),
        "timeout_event": threading.Event(),
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
        "backend": backend,
        "ask_in_flight": True,
        "ask_future": None,
    }


def test_claude_resume_valid_usage_persists_on_success(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-resume-success")
    entry = _ask_entry(run_dir)
    proc = _FakeAskProc()

    proc.stdout.feed(json.dumps(_claude_result(result="resume done")) + "\n")
    proc.finish(returncode=0)

    result = mgr._run_ask_claude_code_stream(
        "em-claude-resume-success", entry, _port_handle(mgr, proc), run_dir,
    )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result["status"] == "sent"
    assert state["cli_tokens"] == {
        "input": 100, "output": 10, "cached": 7, "thinking": 0, "calls": 1,
    }
    assert len(_usage_events(run_dir)) == 1


@pytest.mark.parametrize(
    ("tail_event", "returncode", "expected_status"),
    [
        (
            {"type": "result", "result": "resume structured failure",
             "is_error": True},
            0,
            "error",
        ),
        (None, 7, "error"),
    ],
)
def test_claude_resume_valid_usage_is_buffered_until_failure_classified(
    tmp_path, tail_event, returncode, expected_status,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-resume-failed-usage")
    entry = _ask_entry(run_dir)
    proc = _FakeAskProc()

    proc.stdout.feed(json.dumps(_claude_result(result="resume success")) + "\n")
    if tail_event is not None:
        proc.stdout.feed(json.dumps(tail_event) + "\n")
    proc.finish(returncode=returncode)

    result = mgr._run_ask_claude_code_stream(
        "em-claude-resume-failed-usage", entry, _port_handle(mgr, proc), run_dir,
    )

    assert result["status"] == expected_status
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state.get("cli_tokens", {"calls": 0}).get("calls", 0) == 0
    assert _usage_events(run_dir) == []


def test_claude_resume_result_read_at_deadline_does_not_persist_usage(tmp_path):
    """REGRESSION: a terminal ``result`` line read right at the deadline
    must not leave usage behind when the worker classifies the run as
    timed out — the buffered candidate must be dropped, not persisted."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-resume-deadline")
    entry = _ask_entry(run_dir)
    proc = _FakeAskProc()
    mgr._timeout = 0.05

    # Feed a valid terminal result but never call proc.finish(); the
    # reader will observe the deadline (mgr._timeout) with the process
    # still "running" from the worker's point of view, forcing the
    # timed_out branch even though a valid result already streamed.
    proc.stdout.feed(json.dumps(_claude_result(result="raced with deadline")) + "\n")

    import lingtai.tools.daemon.posix_process as posix_process
    killed = threading.Event()

    def fake_kill(p):
        killed.set()
        p.finish(returncode=-15)

    with patch.object(posix_process.os, "killpg",
                      side_effect=lambda pid, sig: fake_kill(proc)):
        result = mgr._run_ask_claude_code_stream(
            "em-claude-resume-deadline", entry, _port_handle(mgr, proc), run_dir,
        )

    assert killed.is_set()
    assert result["status"] == "error"
    assert "timed out" in result["message"]
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state.get("cli_tokens", {"calls": 0}).get("calls", 0) == 0
    assert _usage_events(run_dir) == []


def test_claude_resume_duplicate_terminal_events_account_once(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-claude-resume-duplicate")
    entry = _ask_entry(run_dir)
    proc = _FakeAskProc()

    proc.stdout.feed(json.dumps(_claude_result(result="first")) + "\n")
    proc.stdout.feed(json.dumps(_claude_result(result="second")) + "\n")
    proc.finish(returncode=0)

    result = mgr._run_ask_claude_code_stream(
        "em-claude-resume-duplicate", entry, _port_handle(mgr, proc), run_dir,
    )

    assert result["status"] == "sent"
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"]["calls"] == 1
    assert len(_usage_events(run_dir)) == 1


# ---------------------------------------------------------------------------
# Initial + resume accumulation across both paths
# ---------------------------------------------------------------------------


def test_claude_initial_and_resume_accumulate_ui_usage_without_ledgers(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    session_id = "claude-initial-resume"
    initial_result = _claude_result(
        session_id=session_id, result="initial done",
        usage=_claude_usage(input_tokens=100, output_tokens=10,
                             cache_read_input_tokens=20, cache_creation_input_tokens=3),
    )
    run_dir = _make_run_dir(agent, handle="em-claude-initial-resume")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_claude_stream(initial_result),
        ),
    ):
        mgr._run_claude_code_emanation(
            "em-claude-initial-resume", run_dir, "Initial task.",
            threading.Event(), threading.Event(),
        )

    entry = _ask_entry(run_dir)
    entry["ask_in_flight"] = False
    proc = _FakeAskProc()
    resume_result = _claude_result(
        session_id=session_id, result="resume done",
        usage=_claude_usage(input_tokens=200, output_tokens=20,
                             cache_read_input_tokens=30, cache_creation_input_tokens=4),
    )
    proc.stdout.feed(json.dumps(resume_result) + "\n")
    proc.finish(returncode=0)

    mgr._run_ask_claude_code_stream(
        "em-claude-initial-resume", entry, _port_handle(mgr, proc), run_dir,
    )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"] == {
        "input": 300, "output": 30, "cached": 57, "thinking": 0, "calls": 2,
    }
    assert not run_dir.token_ledger_path.exists()
    assert not (agent._working_dir / "logs" / "token_ledger.jsonl").exists()
