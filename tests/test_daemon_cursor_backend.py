"""Tests for the Cursor Agent CLI daemon backend.

Cursor exposes its headless agent as the ``agent`` executable.  The daemon
backend uses print mode with stream-json output so it behaves like the other
external CLI backends: command construction is deterministic, JSONL progress is
persisted to the daemon run dir, and ``daemon(ask)`` resumes by session id.

The tests exercise the injected Port with a patched POSIX adapter; Cursor
itself is not required.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from lingtai.tools.daemon import DaemonManager, _normalize_cursor_usage
from tests._daemon_helpers import (
    FiniteFakeProc,
    completed_future,
    install_fake_detached_owner,
    make_daemon_agent,
    make_daemon_run_dir,
    register_daemon_entry,
    wait_daemon_terminal,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_run_dir(agent, *, handle="em-cursor"):
    return make_daemon_run_dir(
        agent,
        handle=handle,
        task="dummy task",
        tools=[],
        model="cursor",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="cursor",
    )


def _cursor_usage(**overrides):
    usage = {
        "inputTokens": 1234,
        "outputTokens": 56,
        "cacheReadTokens": 789,
        "cacheWriteTokens": 12,
    }
    usage.update(overrides)
    return usage


def _cursor_result(*, session_id="cursor-session-XYZ", result="done", **overrides):
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": result,
        "session_id": session_id,
        "request_id": "request-1",
        "usage": _cursor_usage(),
    }
    event.update(overrides)
    return event


def _source_cursor_stream(*events):
    """JSONL fixture shaped like Cursor Agent 2026.05.28-a70ca7c."""
    return [json.dumps(event) + "\n" for event in events]


# ---------------------------------------------------------------------------
# Schema surface
# ---------------------------------------------------------------------------


def test_schema_enum_includes_cursor():
    from lingtai.tools.daemon import get_schema

    schema = get_schema("en")
    backend = schema["properties"]["backend"]
    assert "cursor" in backend["enum"]
    assert "cursor" in backend["description"]


def test_schema_backend_options_description_mentions_cursor():
    from lingtai.tools.daemon import get_schema

    schema = get_schema("en")
    bo = schema["properties"]["tasks"]["items"]["properties"]["backend_options"]
    assert "cursor" in bo["description"]
    assert "agent --help" in bo["description"]


# ---------------------------------------------------------------------------
# Cursor event shapes
# ---------------------------------------------------------------------------


def test_cursor_documented_result_event_extracts_session_and_text():
    event = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "full assistant text",
        "session_id": "cursor-session-123",
    }
    assert DaemonManager._opencode_extract_session_id(event) == "cursor-session-123"
    assert DaemonManager._opencode_extract_text(event) == "full assistant text"


def test_cursor_usage_maps_net_input_and_preserves_cache_totals():
    event = _cursor_result()
    assert _normalize_cursor_usage(event) == {
        "input": 1234,
        "output": 56,
        "cached": 789 + 12,
        "thinking": 0,
    }


@pytest.mark.parametrize(
    "event",
    [
        {"type": "result", "subtype": "success", "is_error": False,
         "usage": _cursor_usage(inputTokens=-1)},
        {"type": "result", "subtype": "success", "is_error": False,
         "usage": _cursor_usage(outputTokens=True)},
        {"type": "result", "subtype": "success", "is_error": False,
         "usage": {"inputTokens": 1}},
        {"type": "result", "subtype": "error", "is_error": True,
         "usage": _cursor_usage()},
        {"type": "result", "subtype": "success", "is_error": False,
         "usage": _cursor_usage(inputTokens=0, outputTokens=0,
                                 cacheReadTokens=0, cacheWriteTokens=0)},
    ],
)
def test_cursor_usage_rejects_invalid_zero_and_unsuccessful_events(event):
    assert _normalize_cursor_usage(event) is None


# ---------------------------------------------------------------------------
# Command construction / streaming
# ---------------------------------------------------------------------------


def test_cursor_emanate_cmd_uses_agent_print_stream_json(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    captured: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        captured.append(list(cmd))
        return FiniteFakeProc()

    run_dir = _make_run_dir(agent, handle="em-cur-cmd")
    cancel = threading.Event()
    timeout = threading.Event()

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_cursor_emanation(
            "em-cur-cmd", run_dir, "Refactor the auth module.",
            cancel, timeout,
        )

    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[:5] == ["agent", "-p", "--force", "--output-format", "stream-json"]
    assert cmd[-1].rstrip().endswith("Refactor the auth module.")
    assert "LingTai daemon" in cmd[-1]


def test_cursor_emanate_appends_backend_argv_before_prompt(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    captured: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        captured.append(list(cmd))
        return FiniteFakeProc()

    run_dir = _make_run_dir(agent, handle="em-cur-opts")
    cancel = threading.Event()
    timeout_event = threading.Event()

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_cursor_emanation(
            "em-cur-opts", run_dir, "Find the bug.",
            cancel, timeout_event,
            backend_argv=["--model", "gpt-5", "--stream-partial-output"],
        )

    cmd = captured[0]
    assert cmd[:5] == ["agent", "-p", "--force", "--output-format", "stream-json"]
    assert cmd.index("--model") > cmd.index("stream-json")
    assert cmd.index("--stream-partial-output") < len(cmd) - 1
    assert cmd[-1].rstrip().endswith("Find the bug.")


def test_cursor_emanate_persists_session_id_and_final_result(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    stdout_lines = [
        '{"type":"system","session_id":"cursor-session-XYZ"}\n',
        '{"type":"assistant","text":"working..."}\n',
        '{"type":"result","subtype":"success","result":"final cursor answer","session_id":"cursor-session-XYZ"}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-cur-sid")
    cancel = threading.Event()
    timeout = threading.Event()

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr._run_cursor_emanation(
            "em-cur-sid", run_dir, "What is the answer?",
            cancel, timeout,
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cursor_session_id"] == "cursor-session-XYZ"
    assert result == "final cursor answer"


def test_cursor_source_usage_is_ui_only_raw_and_model_joined(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    init = {
        "type": "system", "subtype": "init", "apiKeySource": "login",
        "session_id": "cursor-source-session", "model": "gpt-example",
    }
    result_event = _cursor_result(session_id="cursor-source-session")
    run_dir = _make_run_dir(agent, handle="em-cur-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_cursor_stream(init, result_event),
        ),
    ):
        result = mgr._run_cursor_emanation(
            "em-cur-usage", run_dir, "Use source usage.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "done"
    assert state["backend"] == "cursor"
    assert state["model"] == "gpt-example"
    assert "provider" not in state
    assert state["cli_tokens"] == {
        "input": 1234, "output": 56, "cached": 801,
        "thinking": 0, "calls": 1,
    }
    events = [
        json.loads(line) for line in run_dir.events_path.read_text().splitlines()
    ]
    usage_events = [event for event in events if event.get("event") == "cli_usage"]
    assert len(usage_events) == 1
    assert usage_events[0]["raw"] == result_event["usage"]
    assert not run_dir.token_ledger_path.exists()
    assert not (agent._working_dir / "logs" / "token_ledger.jsonl").exists()


def test_cursor_model_transaction_preserves_concurrent_owner_state(tmp_path):
    """A stale Cursor writer cannot erase fields committed by another owner."""
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    stale = _make_run_dir(agent, handle="em-cur-model-transaction")
    concurrent = DaemonRunDir.attach(stale.path)
    concurrent.update_state(followup_status="done", followup_generation="g-other")

    mgr._cursor_set_model(stale, "gpt-transactional")

    state = json.loads(stale.daemon_json_path.read_text(encoding="utf-8"))
    assert state["model"] == "gpt-transactional"
    assert state["followup_status"] == "done"
    assert state["followup_generation"] == "g-other"


def test_cursor_model_join_requires_preceding_matching_init_and_provider_stays_unknown(
    tmp_path,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    init = {
        "type": "system", "subtype": "init", "apiKeySource": "login",
        "session_id": "init-session", "model": "should-not-join",
    }
    result_event = _cursor_result(session_id="different-session")
    run_dir = _make_run_dir(agent, handle="em-cur-unmatched")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_cursor_stream(init, result_event),
        ),
    ):
        mgr._run_cursor_emanation(
            "em-cur-unmatched", run_dir, "Keep model unknown.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["model"] == "unknown"
    assert state["backend"] == "cursor"
    assert "provider" not in state


def test_cursor_duplicate_terminal_events_account_once(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    first = _cursor_result(result="first")
    second = _cursor_result(result="second")
    run_dir = _make_run_dir(agent, handle="em-cur-duplicate")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_cursor_stream(first, second),
        ),
    ):
        result = mgr._run_cursor_emanation(
            "em-cur-duplicate", run_dir, "Count one terminal.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "second"
    assert state["cli_tokens"]["calls"] == 1
    usage_events = [
        json.loads(line) for line in run_dir.events_path.read_text().splitlines()
        if '"event": "cli_usage"' in line
    ]
    assert len(usage_events) == 1


def test_cursor_invalid_and_zero_usage_events_are_not_recorded(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    malformed = _cursor_result(result="malformed", **{"usage": {
        "inputTokens": "1234", "outputTokens": 56,
        "cacheReadTokens": 789, "cacheWriteTokens": 12,
    }})
    zero = _cursor_result(
        result="zero",
        **{"usage": _cursor_usage(inputTokens=0, outputTokens=0,
                                  cacheReadTokens=0, cacheWriteTokens=0)},
    )
    run_dir = _make_run_dir(agent, handle="em-cur-invalid-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_cursor_stream(malformed, zero),
        ),
    ):
        result = mgr._run_cursor_emanation(
            "em-cur-invalid-usage", run_dir, "Ignore malformed usage.",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "zero"
    assert state["cli_tokens"]["calls"] == 0
    assert '"event": "cli_usage"' not in run_dir.events_path.read_text()


def test_cursor_emanate_marks_error_result_failed(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    stdout_lines = [
        '{"type":"result","subtype":"error","is_error":true,"result":"Cursor failed to apply patch"}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-cur-error")
    cancel = threading.Event()
    timeout = threading.Event()

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        try:
            mgr._run_cursor_emanation(
                "em-cur-error", run_dir, "Please fail", cancel, timeout,
            )
        except RuntimeError as exc:
            assert "error result" in str(exc)
            assert "Cursor failed to apply patch" in str(exc)
        else:  # pragma: no cover - test must fail if no exception is raised
            raise AssertionError("Cursor error result should fail the emanation")

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["cli_tokens"]["calls"] == 0



@pytest.mark.parametrize(
    ("tail_event", "returncode", "error_text"),
    [
        (
            {"type": "result", "subtype": "error", "is_error": True,
             "result": "initial structured failure"},
            0,
            "initial structured failure",
        ),
        (None, 7, "exited"),
    ],
)
def test_cursor_initial_valid_usage_is_buffered_until_success(
    tmp_path, tail_event, returncode, error_text,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    success = _cursor_result(result="initial success")
    events = [success] + ([tail_event] if tail_event is not None else [])
    run_dir = _make_run_dir(agent, handle="em-cur-initial-failed-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_cursor_stream(*events),
            returncode=returncode,
        ),
    ):
        with pytest.raises(RuntimeError, match=error_text):
            mgr._run_cursor_emanation(
                "em-cur-initial-failed-usage", run_dir, "Fail after usage.",
                threading.Event(), threading.Event(),
            )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "failed"
    assert state["cli_tokens"]["calls"] == 0
    usage_events = [
        json.loads(line) for line in run_dir.events_path.read_text().splitlines()
        if json.loads(line).get("event") == "cli_usage"
    ]
    assert usage_events == []



def test_cursor_initial_wait_signal_cannot_persist_usage_candidate(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    cancel = threading.Event()
    timeout_event = threading.Event()

    class WaitSignalsCancel(FiniteFakeProc):
        def wait(self, timeout=None):
            timeout_event.set()
            cancel.set()
            return super().wait(timeout)

    run_dir = _make_run_dir(agent, handle="em-cur-post-eof-cancel")
    proc = WaitSignalsCancel(
        stdout_lines=_source_cursor_stream(
            _cursor_result(result="valid before post-EOF timeout"),
        ),
    )

    with patch("lingtai.tools.daemon.subprocess.Popen", return_value=proc):
        result = mgr._run_cursor_emanation(
            "em-cur-post-eof-cancel", run_dir, "Cancel after EOF.",
            cancel, timeout_event,
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "[cancelled]"
    assert state["state"] == "timeout"
    assert state["cli_tokens"]["calls"] == 0
    events = [
        json.loads(line) for line in run_dir.events_path.read_text().splitlines()
    ]
    assert [event for event in events if event.get("event") == "cli_usage"] == []



def test_emanate_cursor_routes_to_cli_handler(tmp_path, monkeypatch):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    records = install_fake_detached_owner(monkeypatch)

    result = mgr.handle({
        "action": "emanate",
        "backend": "cursor",
        "tasks": [{
            "task": "Summarise the changelog.",
            "tools": [],
            "backend_options": {"model": "gpt-5"},
        }],
    })
    assert result["status"] == "dispatched"
    assert result["backend"] == "cursor"
    em_id = result["ids"][0]
    state = wait_daemon_terminal(mgr._emanations[em_id]["run_dir"])

    manifest = records[0]["manifest"]
    assert manifest["backend"] == "cursor"
    assert manifest["task"] == "Summarise the changelog."
    assert manifest["backend_argv"] == ["--model", "gpt-5"]
    assert state["backend"] == "cursor"
    assert state["backend_options"] == {"model": "gpt-5"}
    assert state["backend_argv"] == ["--model", "gpt-5"]
    assert "future" not in mgr._emanations[em_id]


# ---------------------------------------------------------------------------
# ask routing
# ---------------------------------------------------------------------------


def _register_cursor_entry(mgr, run_dir, em_id="em-cur-resume"):
    return register_daemon_entry(
        mgr,
        em_id,
        run_dir,
        future=completed_future("[fake done]"),
        task="x",
        backend="cursor",
        ask_in_flight=False,
    )


def test_ask_cursor_errors_when_no_session_id(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-cur-noresume")
    _register_cursor_entry(mgr, run_dir, em_id="em-cur-noresume")

    result = mgr.handle({
        "action": "ask",
        "id": "em-cur-noresume",
        "message": "any update?",
    })

    assert result["status"] == "error"
    assert "cursor session ID" in result["message"]
    assert "em-cur-noresume" in result["message"]


def test_ask_cursor_resumes_with_captured_session_id(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.append(list(cmd))
        return FiniteFakeProc(stdout_lines=[
            '{"type":"result","subtype":"success","result":"follow-up done"}\n',
        ])

    run_dir = _make_run_dir(agent, handle="em-cur-resume")
    run_dir._state["cursor_session_id"] = "cursor-resumable-123"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
    _register_cursor_entry(mgr, run_dir, em_id="em-cur-resume")

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr.handle({
            "action": "ask",
            "id": "em-cur-resume",
            "message": "how is it going?",
        })

    assert result["status"] == "sent"
    assert result.get("async") is True
    ask_future = mgr._emanations["em-cur-resume"]["ask_future"]
    if ask_future is not None:
        ask_future.result(timeout=5)

    assert len(captured_cmd) == 1
    cmd = captured_cmd[0]
    assert cmd[:3] == ["agent", "-p", "--force"]
    assert "--resume" in cmd
    assert cmd[cmd.index("--resume") + 1] == "cursor-resumable-123"
    assert "--output-format" in cmd
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[-1] == "how is it going?"


def test_cursor_initial_and_resume_accumulate_ui_usage_without_ledgers(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    session_id = "cursor-initial-resume"
    init = {
        "type": "system", "subtype": "init", "apiKeySource": "login",
        "session_id": session_id, "model": "cursor-model",
    }
    initial_result = _cursor_result(
        session_id=session_id, result="initial done",
        **{"usage": _cursor_usage(inputTokens=100, outputTokens=10,
                                  cacheReadTokens=20, cacheWriteTokens=3)},
    )
    resume_result = _cursor_result(
        session_id=session_id, result="resume done",
        **{"usage": _cursor_usage(inputTokens=200, outputTokens=20,
                                  cacheReadTokens=30, cacheWriteTokens=4)},
    )
    run_dir = _make_run_dir(agent, handle="em-cur-initial-resume")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        side_effect=[
            FiniteFakeProc(stdout_lines=_source_cursor_stream(init, initial_result)),
            FiniteFakeProc(stdout_lines=_source_cursor_stream(resume_result)),
        ],
    ):
        mgr._run_cursor_emanation(
            "em-cur-initial-resume", run_dir, "Initial task.",
            threading.Event(), threading.Event(),
        )
        run_dir._state["cursor_session_id"] = session_id
        run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
        _register_cursor_entry(mgr, run_dir, em_id="em-cur-initial-resume")
        sent = mgr.handle({
            "action": "ask", "id": "em-cur-initial-resume",
            "message": "Resume task.",
        })
        assert sent["status"] == "sent"
        mgr._emanations["em-cur-initial-resume"]["ask_future"].result(timeout=5)

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["model"] == "cursor-model"
    assert state["cli_tokens"] == {
        "input": 300, "output": 30, "cached": 57,
        "thinking": 0, "calls": 2,
    }
    assert not run_dir.token_ledger_path.exists()
    assert not (agent._working_dir / "logs" / "token_ledger.jsonl").exists()


def test_ask_cursor_error_result_publishes_failure(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=[
            '{"type":"result","subtype":"error","is_error":true,"result":"resume failed"}\n',
        ])

    run_dir = _make_run_dir(agent, handle="em-cur-resume-error")
    run_dir._state["cursor_session_id"] = "cursor-resumable-error"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
    _register_cursor_entry(mgr, run_dir, em_id="em-cur-resume-error")

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr.handle({
            "action": "ask",
            "id": "em-cur-resume-error",
            "message": "try again",
        })

    assert result["status"] == "sent"
    ask_future = mgr._emanations["em-cur-resume-error"]["ask_future"]
    assert ask_future is not None
    followup = ask_future.result(timeout=5)
    assert followup["status"] == "error"
    assert "error result" in followup["message"]
    assert "resume failed" in followup["message"]



@pytest.mark.parametrize(
    ("tail_event", "returncode", "error_text"),
    [
        (
            {"type": "result", "subtype": "error", "is_error": True,
             "result": "resume structured failure"},
            0,
            "resume structured failure",
        ),
        (None, 7, "exited"),
    ],
)
def test_cursor_resume_valid_usage_is_buffered_until_success(
    tmp_path, tail_event, returncode, error_text,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    success = _cursor_result(result="resume success")
    events = [success] + ([tail_event] if tail_event is not None else [])
    run_dir = _make_run_dir(agent, handle="em-cur-resume-failed-usage")
    run_dir._state["cursor_session_id"] = "cursor-resumable-failed-usage"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
    _register_cursor_entry(mgr, run_dir, em_id="em-cur-resume-failed-usage")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(
            stdout_lines=_source_cursor_stream(*events),
            returncode=returncode,
        ),
    ):
        sent = mgr.handle({
            "action": "ask",
            "id": "em-cur-resume-failed-usage",
            "message": "Fail after resume usage.",
        })

    assert sent["status"] == "sent"
    ask_future = mgr._emanations["em-cur-resume-failed-usage"]["ask_future"]
    assert ask_future is not None
    followup = ask_future.result(timeout=5)
    assert followup["status"] == "error"
    assert error_text in followup["message"]

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"]["calls"] == 0
    usage_events = [
        json.loads(line) for line in run_dir.events_path.read_text().splitlines()
        if json.loads(line).get("event") == "cli_usage"
    ]
    assert usage_events == []



def test_ask_cursor_concurrent_returns_busy(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    run_dir = _make_run_dir(agent, handle="em-cur-busy")
    run_dir._state["cursor_session_id"] = "cursor-busy-1"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
    _register_cursor_entry(mgr, run_dir, em_id="em-cur-busy")
    mgr._emanations["em-cur-busy"]["ask_in_flight"] = True

    result = mgr._handle_ask("em-cur-busy", "second concurrent ask")

    assert result["status"] == "busy"
    assert "em-cur-busy" in result["message"]
