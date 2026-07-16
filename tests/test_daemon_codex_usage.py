"""Source-shaped Codex CLI usage tests (no external Codex process)."""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest

from lingtai.tools.daemon import _normalize_codex_usage
from lingtai.tools.daemon.process_port import DaemonProcessCommand
from tests._daemon_helpers import FiniteFakeProc, make_daemon_agent, make_daemon_run_dir


def _line(event: dict) -> str:
    return json.dumps(event) + "\n"


def _usage(*, input_tokens=100, cached_input_tokens=25, output_tokens=7) -> dict:
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
    }


def test_normalize_codex_usage_uses_disjoint_input_and_source_fields_only():
    usage = _usage(input_tokens=100, cached_input_tokens=25, output_tokens=7)
    usage["total_tokens"] = 132
    usage["reasoning_output_tokens"] = 999

    assert _normalize_codex_usage(usage) == {
        "input": 75,
        "cached": 25,
        "output": 7,
    }


def test_normalize_codex_usage_clamps_cached_input_over_total():
    assert _normalize_codex_usage(
        _usage(input_tokens=10, cached_input_tokens=15, output_tokens=0)
    ) == {"input": 0, "cached": 15, "output": 0}


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": 100, "cached_input_tokens": 0},
        {"input_tokens": "100", "cached_input_tokens": 0, "output_tokens": 1},
        {"input_tokens": 100, "cached_input_tokens": -1, "output_tokens": 1},
        {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
    ],
)
def test_normalize_codex_usage_suppresses_missing_malformed_and_zero(usage):
    assert _normalize_codex_usage(usage) is None


def _run_codex_fixture(tmp_path, events: list[dict]):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, backend="codex")
    proc = FiniteFakeProc(stdout_lines=[_line(event) for event in events])

    with patch(
        "lingtai.tools.daemon.subprocess.Popen",
        return_value=proc,
    ):
        result = manager._run_codex_emanation(
            "em-codex-usage",
            run_dir,
            "report the result",
            threading.Event(),
            threading.Event(),
        )
    return result, run_dir


def test_codex_terminal_usage_persists_ui_totals_raw_event_and_result(tmp_path):
    usage = _usage(input_tokens=100, cached_input_tokens=25, output_tokens=7)
    result, run_dir = _run_codex_fixture(
        tmp_path,
        [
            {"type": "thread.started", "thread_id": "thread-1"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "Codex result"},
            },
            {"type": "turn.completed", "usage": usage},
        ],
    )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == "Codex result"
    assert state["state"] == "done"
    assert state["result_preview"] == "Codex result"
    assert state["cli_tokens"] == {
        "input": 75,
        "cached": 25,
        "output": 7,
        "thinking": 0,
        "calls": 1,
    }

    events = [json.loads(line) for line in run_dir.events_path.read_text().splitlines()]
    usage_events = [event for event in events if event.get("event") == "cli_usage"]
    output_events = [event for event in events if event.get("event") == "cli_output"]
    assert len(usage_events) == 1
    assert usage_events[0]["raw"] == usage
    assert usage_events[0]["input"] == 75
    assert usage_events[0]["cached"] == 25
    assert usage_events[0]["output"] == 7
    assert any(event["text"] == "Codex result" for event in output_events)
    assert any(event["event"] == "daemon_done" for event in events)


def test_codex_terminal_usage_never_mutates_either_token_ledger(tmp_path):
    _, run_dir = _run_codex_fixture(
        tmp_path,
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "ok"},
            },
            {"type": "turn.completed", "usage": _usage()},
        ],
    )

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["tokens"] == {"input": 0, "output": 0, "thinking": 0, "cached": 0}
    assert not run_dir.token_ledger_path.exists()
    assert not run_dir._parent_token_ledger.exists()


def test_codex_duplicate_terminal_events_account_once(tmp_path):
    usage = _usage(input_tokens=100, cached_input_tokens=25, output_tokens=7)
    _, run_dir = _run_codex_fixture(
        tmp_path,
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "once"},
            },
            {"type": "turn.completed", "usage": usage},
            {
                "type": "turn.completed",
                "usage": _usage(
                    input_tokens=900, cached_input_tokens=1, output_tokens=2
                ),
            },
        ],
    )

    state = json.loads(run_dir.daemon_json_path.read_text())
    events = [json.loads(line) for line in run_dir.events_path.read_text().splitlines()]
    assert state["cli_tokens"]["calls"] == 1
    assert state["cli_tokens"]["input"] == 75
    assert len([event for event in events if event.get("event") == "cli_usage"]) == 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {"input_tokens": "bad", "cached_input_tokens": 0, "output_tokens": 1},
        {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0},
    ],
)
def test_codex_malformed_missing_or_zero_terminal_usage_is_silent(tmp_path, usage):
    event = {"type": "turn.completed"}
    if usage is not None:
        event["usage"] = usage
    result, run_dir = _run_codex_fixture(
        tmp_path,
        [
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "ok"},
            },
            event,
        ],
    )

    state = json.loads(run_dir.daemon_json_path.read_text())
    events = [json.loads(line) for line in run_dir.events_path.read_text().splitlines()]
    assert result == "ok"
    assert state["cli_tokens"]["calls"] == 0
    assert not [event for event in events if event.get("event") == "cli_usage"]


def test_codex_followup_terminal_usage_uses_same_ui_only_path(tmp_path):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, backend="codex")
    entry = {"followup_lock": threading.Lock(), "ask_in_flight": True}
    proc = FiniteFakeProc(
        stdout_lines=[
            _line(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "followup"},
                }
            ),
            _line({"type": "turn.completed", "usage": _usage()}),
        ]
    )

    with patch(
        "lingtai.tools.daemon.posix_process.subprocess.Popen", return_value=proc,
    ):
        handle = manager._process_port.spawn(
            DaemonProcessCommand(("codex",), agent._working_dir),
        )
    result = manager._run_ask_codex_stream("em-followup", entry, handle, run_dir)

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert result == {"status": "sent", "id": "em-followup", "output": "followup"}
    assert state["cli_tokens"]["calls"] == 1
    assert state["cli_tokens"]["input"] == 75
    assert entry["ask_in_flight"] is False
