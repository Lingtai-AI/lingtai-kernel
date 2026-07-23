"""Structured daemon_common human-input pause behavior."""
import json

import pytest

from lingtai.mcp_servers.daemon_common.server import _validate_ask_human
from tests._daemon_helpers import (
    completed_future,
    make_daemon_agent,
    make_daemon_run_dir,
    register_daemon_entry,
)


def test_ask_human_payload_is_structured_and_bound_to_run(monkeypatch):
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", "em-question")

    payload = _validate_ask_human({
        "question": "Which dataset?",
        "choices": ["A", "B"],
        "default": "A",
        "reason": "The task names two inputs.",
    })

    assert payload == {
        "schema": "lingtai.daemon_input_request.v1",
        "run_id": "em-question",
        "question": "Which dataset?",
        "choices": ["A", "B"],
        "default": "A",
        "reason": "The task names two inputs.",
    }


def test_runtime_owns_waiting_input_state_and_list_visibility(tmp_path):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-question",
        backend="claude-p",
        call_parameters={"mcp": [{"name": "daemon_common"}]},
    )
    request = {
        "schema": "lingtai.daemon_input_request.v1",
        "run_id": run_dir.run_id,
        "question": "Which dataset?",
        "choices": ["A", "B"],
    }
    (run_dir.path / "daemon_input_request.json").write_text(json.dumps(request))

    assert manager._require_done_completion(run_dir, "I need clarification") is False
    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "waiting_input"
    assert state["finished_at"] is None
    assert state["input_request"] == request
    event = json.loads(run_dir.events_path.read_text().splitlines()[-1])
    assert event["event"] == "daemon_waiting_input"

    listing = manager._daemon_list_entry_from_state(state, run_dir.path)
    assert listing["status"] == "waiting_input"
    assert listing["input_request"]["question"] == "Which dataset?"


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        json.dumps({
            "schema": "lingtai.daemon_input_request.v1",
            "question": "Choose",
            "choices": [],
        }),
        json.dumps({
            "schema": "lingtai.daemon_input_request.v1",
            "question": "Choose",
            "default": 1,
        }),
        json.dumps({
            "schema": "lingtai.daemon_input_request.v1",
            "question": "Choose",
            "reason": ["invalid"],
        }),
    ],
)
def test_invalid_input_request_marks_run_failed(tmp_path, payload):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-invalid-question",
        backend="claude-p",
        call_parameters={"mcp": [{"name": "daemon_common"}]},
    )
    (run_dir.path / "daemon_input_request.json").write_text(payload)

    with pytest.raises(RuntimeError, match="daemon input request"):
        manager._require_done_completion(run_dir, "I need clarification")

    state = run_dir.state_snapshot()
    assert state["state"] == "failed"
    assert "daemon input request" in state["error"]["message"]
    assert manager._daemon_list_entry_from_state(state, run_dir.path)["status"] == "failed"
    assert manager._handle_check(run_dir.run_id)["state"] == "failed"


def test_list_bounds_input_request_preview(tmp_path):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, handle="em-large-question")
    state = run_dir.state_snapshot()
    state["input_request"] = {
        "question": "q" * 1000,
        "choices": [str(index) * 100 for index in range(20)],
        "default": "d" * 1000,
        "reason": "r" * 1000,
    }

    preview = manager._daemon_list_entry_from_state(state, run_dir.path)["input_request"]

    assert len(preview["question"]) <= 512
    assert len(preview["choices"]) == 10
    assert all(len(choice) <= 512 for choice in preview["choices"])
    assert len(preview["default"]) <= 512
    assert len(preview["reason"]) <= 512


def test_input_request_takes_precedence_over_done_completion(tmp_path):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-question-and-done",
        backend="claude-p",
        call_parameters={"mcp": [{"name": "daemon_common"}]},
    )
    request = {
        "schema": "lingtai.daemon_input_request.v1",
        "run_id": run_dir.run_id,
        "question": "Which dataset?",
    }
    (run_dir.path / "daemon_input_request.json").write_text(json.dumps(request))
    (run_dir.path / "daemon_completion.json").write_text(json.dumps({
        "schema": "lingtai.daemon_completion.v1",
        "run_id": run_dir.run_id,
        "status": "done",
    }))

    assert manager._require_done_completion(run_dir, "Done") is False
    assert run_dir.state_snapshot()["state"] == "waiting_input"


def test_daemon_common_registration_and_prompt_advertise_ask_human(tmp_path):
    run_dir = make_daemon_run_dir(parent_working_dir=tmp_path / "parent")
    agent = make_daemon_agent(tmp_path, working_dir_name="other")
    manager = agent.get_capability("daemon")

    registration = manager._daemon_common_mcp_registration(run_dir)
    assert registration["env"]["LINGTAI_DAEMON_INPUT_REQUEST_FILE"].endswith(
        "daemon_input_request.json"
    )
    assert "ask_human" in manager._daemon_common_context()


def test_waiting_input_notification_does_not_claim_terminal_slot(tmp_path):
    agent = make_daemon_agent(tmp_path)
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(agent, handle="em-question", backend="claude-p")
    run_dir.mark_waiting_input({"question": "Proceed?"})
    future = completed_future("[waiting_input]")
    register_daemon_entry(manager, "em-question", run_dir, future=future)

    manager._on_emanation_done("em-question", "task", future)

    state = json.loads(run_dir.daemon_json_path.read_text())
    assert state["state"] == "waiting_input"
    assert state["terminal_notified"] is False
    from lingtai_kernel.notifications import collect_notifications
    event = collect_notifications(agent._working_dir)["system"]["data"]["events"][-1]
    assert "waiting_input" in event["body"]
