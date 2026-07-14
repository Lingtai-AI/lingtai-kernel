"""Tests for the MiMo Code (``mimocode`` / ``mimo``) daemon backend's
JSONL answer/error/session-selector contract.

Verified against official MiMo Code 0.1.5 (tag commit
31c972081865dc60e3f2be1abeffe7333c27921d):

1. In JSONL mode (``mimo run --format json``) the user-visible answer is
   ONLY an event with ``type == "text"`` whose nested ``part.text`` is a
   string. MiMo reasoning / tool / step events ALSO carry a ``part.text``
   field; surfacing those as the answer (which the generic OpenCode parser
   did) leaks internal chatter as the daemon result. They must be ignored.
2. A structured ``type == "error"`` event must make the daemon fail loudly
   even when the process exits 0. The human-visible detail is bounded
   (<=500 chars) and secret-redacted; the raw nested payload is not exposed.
3. Harness-owned MiMo session selectors (``--session`` / ``-s`` /
   ``--continue`` / ``-c`` / ``--fork``) are rejected in ``backend_options``
   while ``--format`` stays reserved and normal options still pass through.
4. The harness-owned ask resume command is preserved exactly:
   ``mimo run --session <mimocode_session_id> --format json <message>`` and
   the same answer/error contract applies to the resume stream.

These are unit tests over the runner with monkey-patched ``subprocess.Popen``
— the MiMo CLI is not required to be installed.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from lingtai.tools.daemon import DaemonManager
from tests._daemon_helpers import (
    FiniteFakeProc,
    completed_future,
    make_daemon_agent,
    make_daemon_run_dir,
    register_daemon_entry,
)


def _make_run_dir(agent, *, handle="em-mimo"):
    return make_daemon_run_dir(
        agent,
        handle=handle,
        task="dummy task",
        tools=[],
        model="mimocode",
        max_turns=10,
        timeout_s=60,
        parent_addr=agent._working_dir.name,
        parent_pid=1,
        system_prompt="[stub]",
        backend="mimocode",
    )


# ---------------------------------------------------------------------------
# Pure MiMo answer/error extractors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("event,expected", [
    # type:text with nested part.text is the ONLY answer surface.
    ({"type": "text", "part": {"text": "the final answer"}}, "the final answer"),
    # Whitespace-only / empty part.text yields nothing.
    ({"type": "text", "part": {"text": "   "}}, ""),
    ({"type": "text", "part": {"text": ""}}, ""),
    # Reasoning / tool / step events ALSO carry part.text — never surfaced.
    ({"type": "reasoning", "part": {"text": "let me think about this"}}, ""),
    ({"type": "tool", "part": {"text": "running a shell command"}}, ""),
    ({"type": "step", "part": {"text": "step boundary marker"}}, ""),
    ({"type": "step-start", "part": {"text": "starting"}}, ""),
    # A type:text event without a nested string part.text yields nothing.
    ({"type": "text", "part": {}}, ""),
    ({"type": "text"}, ""),
    ({"type": "text", "part": {"text": 123}}, ""),
    # Non-text, non-part structural events yield nothing.
    ({"type": "session", "id": "sess-1"}, ""),
    ({}, ""),
])
def test_mimocode_answer_extraction_only_from_type_text(event, expected):
    assert DaemonManager._mimocode_extract_answer_text(event) == expected


def test_mimocode_error_extraction_prefers_official_data_message():
    """MiMo Code 0.1.5 run.ts derives the detail from
    ``String(props.error.data.message)`` when present. The extractor must
    pin that official nested shape, not a synthetic top-level ``message``."""
    event = {
        "type": "error",
        "error": {
            "name": "ProviderError",
            "data": {"message": "provider auth failed"},
        },
    }
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert "provider auth failed" in detail
    assert len(detail) <= 500


def test_mimocode_error_extraction_falls_back_to_error_name():
    """When ``error.data.message`` is absent, run.ts uses
    ``String(props.error.name)`` — the extractor must too."""
    event = {"type": "error", "error": {"name": "AuthProviderError"}}
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert "AuthProviderError" in detail


def test_mimocode_error_name_outranks_defensive_fields():
    """Official ``error.name`` ranks above the defensive non-official fields.
    When ``error.data.message`` is absent but ``error.name`` and synthetic
    ``message``/``detail``/``reason`` are all present, ``error.name`` wins and
    the synthetic values never appear in the detail."""
    event = {
        "type": "error",
        "error": {
            "name": "OfficialErrorName",
            "message": "synthetic-message-should-not-win",
            "detail": "synthetic-detail-should-not-win",
            "reason": "synthetic-reason-should-not-win",
        },
    }
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert "OfficialErrorName" in detail
    for synthetic in (
        "synthetic-message-should-not-win",
        "synthetic-detail-should-not-win",
        "synthetic-reason-should-not-win",
    ):
        assert synthetic not in detail


def test_mimocode_error_extraction_blank_or_nonstring_does_not_suppress_valid():
    """A whitespace-only or non-string higher-priority field must not
    suppress a later valid string: the first NONBLANK STRING wins.
    Here ``error.data.message`` is blank and ``error.message`` is a dict
    (non-string), so the extractor must fall through to ``error.name``."""
    event = {
        "type": "error",
        "error": {
            "data": {"message": "   "},          # whitespace-only, skipped
            "message": {"nested": "not a str"},   # non-string, skipped
            "name": "FallbackName",               # first nonblank string
        },
    }
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert "FallbackName" in detail


def test_mimocode_error_extraction_generic_when_no_string_detail():
    """No usable string anywhere → an explicit generic detail, never None
    (the event is still a terminal failure)."""
    event = {"type": "error", "error": {"data": {}, "code": 500}}
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert detail.strip()


def test_mimocode_error_extraction_redacts_secrets():
    # A leaked bearer token in the official data.message must be redacted,
    # not surfaced verbatim.
    event = {
        "type": "error",
        "error": {"data": {"message": "bad request: api_key=sk-ABCDEF1234567890abcdef"}},
    }
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert "sk-ABCDEF1234567890abcdef" not in detail


def test_mimocode_error_extraction_bounds_long_detail():
    event = {"type": "error", "error": {"data": {"message": "x" * 5000}}}
    detail = DaemonManager._mimocode_extract_error(event)
    assert detail is not None
    assert len(detail) <= 500


@pytest.mark.parametrize("event", [
    {"type": "text", "part": {"text": "not an error"}},
    {"type": "reasoning", "part": {"text": "thinking"}},
    {"type": "session", "id": "sess"},
    {},
])
def test_mimocode_error_extraction_returns_none_for_non_error_events(event):
    assert DaemonManager._mimocode_extract_error(event) is None


# ---------------------------------------------------------------------------
# Initial run: source-shaped usage
# ---------------------------------------------------------------------------


def _step_finish_event(part_id="part-1", *, input=123, output=7,
                       reasoning=19, cache_read=100, cache_write=5,
                       **part_fields):
    part = {
        "id": part_id,
        "type": "step-finish",
        "reason": "stop",
        "cost": 0,
        "tokens": {
            "input": input,
            "output": output,
            "reasoning": reasoning,
            "cache": {"read": cache_read, "write": cache_write},
        },
        **part_fields,
    }
    return {"type": "step_finish", "part": part}


def _cli_usage_events(run_dir):
    return [
        json.loads(line)
        for line in run_dir.events_path.read_text().splitlines()
        if json.loads(line).get("event") == "cli_usage"
    ]


def test_mimocode_run_records_source_usage_without_ledgers(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    part = _step_finish_event(
        "part-exact", input=123, output=7, reasoning=19,
        cache_read=100, cache_write=5, cost=1.25,
    )["part"]
    stdout_lines = [
        json.dumps({"type": "session", "id": "mimo-usage-1"}) + "\n",
        json.dumps({"type": "reasoning", "part": {"text": "not the answer"}}) + "\n",
        json.dumps({"type": "step_finish", "part": part}) + "\n",
        json.dumps({"type": "text", "part": {"text": "answer"}}) + "\n",
    ]

    run_dir = _make_run_dir(agent, handle="em-mimo-usage-exact")
    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(stdout_lines=stdout_lines),
    ):
        result = mgr._run_mimocode_emanation(
            "em-mimo-usage-exact", run_dir, "task",
            threading.Event(), threading.Event(),
        )

    assert result == "answer"
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"] == {
        "input": 123, "output": 7, "cached": 105, "thinking": 19, "calls": 1,
    }
    usage = _cli_usage_events(run_dir)
    assert len(usage) == 1
    assert usage[0]["input"] == 123
    assert usage[0]["output"] == 7
    assert usage[0]["cached"] == 105
    assert usage[0]["thinking"] == 19
    assert usage[0]["raw"] == part
    assert not run_dir.token_ledger_path.exists()
    assert not (agent._working_dir / "logs" / "token_ledger.jsonl").exists()


def test_mimocode_usage_accumulates_distinct_parts_once(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    part_one = _step_finish_event("part-one", input=10, output=2, reasoning=3,
                                  cache_read=4, cache_write=1)
    part_two = _step_finish_event("part-two", input=20, output=5, reasoning=6,
                                  cache_read=7, cache_write=2)
    stdout_lines = [
        json.dumps({"type": "session", "id": "mimo-usage-2"}) + "\n",
        json.dumps(part_one) + "\n",
        json.dumps(part_one) + "\n",  # replay of the same source part
        json.dumps(part_two) + "\n",  # distinct model step
        json.dumps({"type": "text", "part": {"text": "done"}}) + "\n",
    ]

    run_dir = _make_run_dir(agent, handle="em-mimo-usage-dedupe")
    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(stdout_lines=stdout_lines),
    ):
        mgr._run_mimocode_emanation(
            "em-mimo-usage-dedupe", run_dir, "task",
            threading.Event(), threading.Event(),
        )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"] == {
        "input": 30, "output": 7, "cached": 14, "thinking": 9, "calls": 2,
    }
    assert len(_cli_usage_events(run_dir)) == 2


@pytest.mark.parametrize("bad_field,bad_value", [
    ("input", True),
    ("output", "7"),
    ("reasoning", -1),
    ("cache_read", None),
    ("cache_write", 1.5),
])
def test_mimocode_usage_suppresses_malformed_and_zero_parts(
    tmp_path, bad_field, bad_value,
):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    malformed = _step_finish_event("part-bad", **{bad_field: bad_value})
    zero = _step_finish_event(
        "part-zero", input=0, output=0, reasoning=0,
        cache_read=0, cache_write=0,
    )
    invalid_shape = {"type": "step_finish", "part": {
        "id": "part-wrong-type", "type": "step", "tokens": zero["part"]["tokens"],
    }}
    stdout_lines = [
        json.dumps(malformed) + "\n",
        json.dumps(zero) + "\n",
        json.dumps(invalid_shape) + "\n",
        json.dumps({"type": "step_finish", "part": {
            "id": "", "type": "step-finish", "tokens": zero["part"]["tokens"],
        }}) + "\n",
        json.dumps({"type": "text", "part": {"text": "still okay"}}) + "\n",
    ]

    run_dir = _make_run_dir(agent, handle=f"em-mimo-usage-invalid-{bad_field}")
    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=FiniteFakeProc(stdout_lines=stdout_lines),
    ):
        result = mgr._run_mimocode_emanation(
            f"em-mimo-usage-invalid-{bad_field}", run_dir, "task",
            threading.Event(), threading.Event(),
        )

    assert result == "still okay"
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"] == {
        "input": 0, "output": 0, "cached": 0, "thinking": 0, "calls": 0,
    }
    assert _cli_usage_events(run_dir) == []
    assert not run_dir.token_ledger_path.exists()
    assert not (agent._working_dir / "logs" / "token_ledger.jsonl").exists()


# ---------------------------------------------------------------------------
# Initial run: answer surfacing
# ---------------------------------------------------------------------------


def test_mimocode_run_surfaces_only_type_text_answer(tmp_path):
    """Reasoning/tool/step part.text must never leak into the result; the
    type:text nested part.text is the daemon answer."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    stdout_lines = [
        '{"type":"session","id":"mimo-sess-1"}\n',
        '{"type":"reasoning","part":{"text":"internal chain of thought"}}\n',
        '{"type":"tool","part":{"text":"executing bash: ls -la"}}\n',
        '{"type":"step","part":{"text":"step boundary"}}\n',
        '{"type":"text","part":{"text":"The answer is 42."}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-mimo-answer")
    cancel = threading.Event()
    timeout = threading.Event()

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr._run_mimocode_emanation(
            "em-mimo-answer", run_dir, "What is the answer?",
            cancel, timeout,
        )

    assert result == "The answer is 42."
    for leaked in ("internal chain of thought", "executing bash", "step boundary"):
        assert leaked not in result
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["mimocode_session_id"] == "mimo-sess-1"


def test_mimocode_run_last_type_text_wins(tmp_path):
    """When multiple type:text events stream, the last one is the answer."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    stdout_lines = [
        '{"type":"session","id":"mimo-sess-2"}\n',
        '{"type":"text","part":{"text":"partial thought"}}\n',
        '{"type":"reasoning","part":{"text":"more reasoning"}}\n',
        '{"type":"text","part":{"text":"final consolidated answer"}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-mimo-last")
    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr._run_mimocode_emanation(
            "em-mimo-last", run_dir, "task", threading.Event(), threading.Event(),
        )
    assert result == "final consolidated answer"


def test_mimocode_run_only_non_answer_events_yields_no_output(tmp_path):
    """A run that emits only reasoning/tool events (no type:text) must not
    fabricate an answer out of internal chatter — it surfaces the explicit
    no-output sentinel."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    stdout_lines = [
        '{"type":"session","id":"mimo-sess-3"}\n',
        '{"type":"reasoning","part":{"text":"thinking hard"}}\n',
        '{"type":"tool","part":{"text":"ran a tool"}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-mimo-noans")
    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr._run_mimocode_emanation(
            "em-mimo-noans", run_dir, "task", threading.Event(), threading.Event(),
        )
    assert result == "[no output]"
    for leaked in ("thinking hard", "ran a tool"):
        assert leaked not in result


# ---------------------------------------------------------------------------
# Initial run: structured error → loud failure even on exit 0
# ---------------------------------------------------------------------------


def test_mimocode_run_structured_error_fails_even_on_exit_zero(tmp_path):
    """A type:error event must terminate the run as a failure even though
    the process exits 0."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    stdout_lines = [
        '{"type":"session","id":"mimo-sess-err"}\n',
        '{"type":"text","part":{"text":"looks fine so far"}}\n',
        # Official MiMo Code 0.1.5 shape: error.data.message.
        '{"type":"error","error":{"name":"ProviderError",'
        '"data":{"message":"model provider rejected request"}}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        # Exit code 0 despite the structured error event.
        return FiniteFakeProc(stdout_lines=stdout_lines, returncode=0)

    run_dir = _make_run_dir(agent, handle="em-mimo-err")
    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(RuntimeError, match="model provider rejected request"):
            mgr._run_mimocode_emanation(
                "em-mimo-err", run_dir, "task",
                threading.Event(), threading.Event(),
            )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "failed"


def test_mimocode_run_structured_error_detail_is_bounded_and_redacted(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    secret = "sk-SECRET1234567890abcdefSECRET"
    stdout_lines = [
        '{"type":"session","id":"mimo-sess-err2"}\n',
        json.dumps({
            "type": "error",
            "error": {"data": {
                "message": "auth failure token=" + secret + " " + "y" * 4000,
            }},
        }) + "\n",
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines, returncode=0)

    run_dir = _make_run_dir(agent, handle="em-mimo-err2")
    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        with pytest.raises(RuntimeError) as excinfo:
            mgr._run_mimocode_emanation(
                "em-mimo-err2", run_dir, "task",
                threading.Event(), threading.Event(),
            )

    msg = str(excinfo.value)
    assert secret not in msg
    # The bounded detail portion the runner appends must stay <=500 chars.
    assert "y" * 600 not in msg


# ---------------------------------------------------------------------------
# Session-selector reserved flags (MiMo-specific)
# ---------------------------------------------------------------------------


def test_mimocode_rejects_harness_owned_session_selectors(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    for flag, key, value in (
        ("--format", "format", "json"),
        ("--session", "session", "mimo-sess-1"),
        ("--continue", "continue", True),
        ("--fork", "fork", True),
    ):
        result = mgr.handle({
            "action": "emanate",
            "backend": "mimo",
            "tasks": [{"task": "bad", "tools": [],
                       "backend_options": {key: value}}],
        })
        assert result["status"] == "error", flag
        assert f"{flag} is reserved by the mimocode daemon backend" in result["message"], flag
        assert mgr._emanations == {}, flag


def test_mimocode_short_session_selectors_reserved():
    """Short aliases -s / -c are reserved for defense-in-depth (backend_options
    only emits long flags, but the reserved set must list them)."""
    from lingtai.tools.daemon import _MIMOCODE_RESERVED_BACKEND_FLAGS
    for token in ("--session", "-s", "--continue", "-c", "--fork", "--format"):
        assert token in _MIMOCODE_RESERVED_BACKEND_FLAGS, token


def test_mimocode_normal_backend_options_still_pass_through(tmp_path):
    """A non-reserved option (e.g. --model) must still reach the argv."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    captured_cmd: list[list[str]] = []

    stdout_lines = [
        '{"type":"session","id":"mimo-sess-ok"}\n',
        '{"type":"text","part":{"text":"done"}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.append(list(cmd))
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-mimo-ok")
    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        mgr._run_mimocode_emanation(
            "em-mimo-ok", run_dir, "task", threading.Event(), threading.Event(),
            backend_argv=["--model", "mimo-auto"],
        )

    cmd = captured_cmd[0]
    assert cmd[:4] == ["mimo", "run", "--format", "json"]
    assert cmd[4:6] == ["--model", "mimo-auto"]


# ---------------------------------------------------------------------------
# Ask resume: command shape preserved + answer/error contract applied
# ---------------------------------------------------------------------------


def test_mimocode_usage_is_shared_by_initial_and_followup_streams(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    shared = _step_finish_event(
        "part-shared", input=11, output=2, reasoning=3,
        cache_read=4, cache_write=1,
    )
    distinct = _step_finish_event(
        "part-followup", input=13, output=5, reasoning=7,
        cache_read=8, cache_write=2,
    )
    processes = [
        FiniteFakeProc(stdout_lines=[
            json.dumps({"type": "session", "id": "mimo-followup-session"}) + "\n",
            json.dumps(shared) + "\n",
            json.dumps({"type": "text", "part": {"text": "initial answer"}}) + "\n",
        ]),
        FiniteFakeProc(stdout_lines=[
            json.dumps(shared) + "\n",  # replayed initial part
            json.dumps(distinct) + "\n",
            json.dumps({"type": "reasoning", "part": {"text": "not a reply"}}) + "\n",
            json.dumps({"type": "text", "part": {"text": "follow-up answer"}}) + "\n",
        ]),
    ]
    run_dir = _make_run_dir(agent, handle="em-mimo-usage-followup")

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        side_effect=lambda *args, **kwargs: processes.pop(0),
    ):
        assert mgr._run_mimocode_emanation(
            "em-mimo-usage-followup", run_dir, "task",
            threading.Event(), threading.Event(),
        ) == "initial answer"
        register_daemon_entry(
            mgr,
            "em-mimo-usage-followup",
            run_dir,
            future=completed_future("[fake done]"),
            task="task",
            backend="mimocode",
            ask_in_flight=False,
        )
        result = mgr.handle({
            "action": "ask",
            "id": "em-mimo-usage-followup",
            "message": "continue",
        })
        assert result["status"] == "sent"
        ask_future = mgr._emanations["em-mimo-usage-followup"]["ask_future"]
        ask_future.result(timeout=5)

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["cli_tokens"] == {
        "input": 24, "output": 7, "cached": 15, "thinking": 10, "calls": 2,
    }
    usage = _cli_usage_events(run_dir)
    assert [entry["raw"]["id"] for entry in usage] == [
        "part-shared", "part-followup",
    ]


def test_mimocode_ask_uses_harness_resume_command(tmp_path):
    """ask resumes with ``mimo run --session <id> --format json <message>``."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    captured_cmd: list[list[str]] = []

    def fake_popen(cmd, *args, **kwargs):
        captured_cmd.append(list(cmd))
        return FiniteFakeProc(
            stdout_lines=['{"type":"text","part":{"text":"resumed reply"}}\n'],
        )

    run_dir = _make_run_dir(agent, handle="em-mimo-resume")
    run_dir._state["mimocode_session_id"] = "mimo-resumable-9"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)

    register_daemon_entry(
        mgr,
        "em-mimo-resume",
        run_dir,
        future=completed_future("[fake done]"),
        task="x",
        backend="mimocode",
        ask_in_flight=False,
    )

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen):
        result = mgr.handle({
            "action": "ask",
            "id": "em-mimo-resume",
            "message": "any update?",
        })
        assert result["status"] == "sent"
        ask_future = mgr._emanations["em-mimo-resume"]["ask_future"]
        if ask_future is not None:
            ask_future.result(timeout=5)

    assert len(captured_cmd) == 1
    # The harness-owned resume argv is exact — no extra tokens, correct order.
    assert captured_cmd[0] == [
        "mimo", "run", "--session", "mimo-resumable-9",
        "--format", "json", "any update?",
    ]


def test_mimocode_ask_surfaces_only_type_text_reply(tmp_path):
    """The resume stream must apply the same answer contract: only
    type:text nested part.text is the reply; reasoning is ignored."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    published: list[dict] = []

    def fake_publish(em_id, *, status, text, run_dir):
        published.append({"status": status, "text": text})

    stdout_lines = [
        '{"type":"reasoning","part":{"text":"resume reasoning leak"}}\n',
        '{"type":"tool","part":{"text":"resume tool leak"}}\n',
        '{"type":"text","part":{"text":"the real resumed answer"}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines)

    run_dir = _make_run_dir(agent, handle="em-mimo-resume-ans")
    run_dir._state["mimocode_session_id"] = "mimo-resumable-ans"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)

    entry = register_daemon_entry(
        mgr,
        "em-mimo-resume-ans",
        run_dir,
        future=completed_future("[fake done]"),
        task="x",
        backend="mimocode",
        ask_in_flight=False,
    )

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen), \
            patch.object(mgr, "_publish_followup_if_live", side_effect=fake_publish):
        mgr.handle({
            "action": "ask",
            "id": "em-mimo-resume-ans",
            "message": "continue",
        })
        ask_future = mgr._emanations["em-mimo-resume-ans"]["ask_future"]
        if ask_future is not None:
            ask_future.result(timeout=5)

    completed = [p for p in published if p["status"] == "follow-up completed"]
    assert completed, published
    reply = completed[-1]["text"]
    assert reply == "the real resumed answer"
    assert "resume reasoning leak" not in reply
    assert "resume tool leak" not in reply


def test_mimocode_ask_structured_error_fails_followup(tmp_path):
    """A type:error in the resume stream must publish a follow-up failure,
    not a fake success — even on exit 0."""
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")

    published: list[dict] = []

    def fake_publish(em_id, *, status, text, run_dir):
        published.append({"status": status, "text": text})

    stdout_lines = [
        '{"type":"text","part":{"text":"looks okay"}}\n',
        # Official MiMo Code 0.1.5 shape: error.data.message.
        '{"type":"error","error":{"name":"ProviderError",'
        '"data":{"message":"resume provider error"}}}\n',
    ]

    def fake_popen(cmd, *args, **kwargs):
        return FiniteFakeProc(stdout_lines=stdout_lines, returncode=0)

    run_dir = _make_run_dir(agent, handle="em-mimo-resume-err")
    run_dir._state["mimocode_session_id"] = "mimo-resumable-err"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)

    register_daemon_entry(
        mgr,
        "em-mimo-resume-err",
        run_dir,
        future=completed_future("[fake done]"),
        task="x",
        backend="mimocode",
        ask_in_flight=False,
    )

    with patch("lingtai.tools.daemon.subprocess.Popen", side_effect=fake_popen), \
            patch.object(mgr, "_publish_followup_if_live", side_effect=fake_publish):
        mgr.handle({
            "action": "ask",
            "id": "em-mimo-resume-err",
            "message": "continue",
        })
        ask_future = mgr._emanations["em-mimo-resume-err"]["ask_future"]
        if ask_future is not None:
            ask_future.result(timeout=5)

    failed = [p for p in published if p["status"] == "follow-up failed"]
    assert failed, published
    assert "resume provider error" in failed[-1]["text"]
