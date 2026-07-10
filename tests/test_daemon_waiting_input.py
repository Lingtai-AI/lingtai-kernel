"""Structured daemon_common human-input pause behavior."""
import json

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
    assert "terminal_notified" not in state
    from lingtai_kernel.notifications import collect_notifications
    event = collect_notifications(agent._working_dir)["system"]["data"]["events"][-1]
    assert "waiting_input" in event["body"]
