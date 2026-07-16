"""Run-scoped semantic checkpoint tests for LingTai daemon runs."""

import json
import threading
from pathlib import Path

import pytest

from lingtai.kernel.llm.base import FunctionSchema, LLMResponse, ToolCall
from lingtai.kernel.llm.interface import (
    ChatInterface,
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
)
from lingtai.tools.daemon.run_dir import DaemonRunDir
from tests._daemon_helpers import (
    make_daemon_agent,
    make_daemon_run_dir,
    register_daemon_entry,
)


class _FakeSession:
    def __init__(
        self, responses, *, system_prompt: str, interface: ChatInterface | None = None
    ):
        self.interface = interface or ChatInterface()
        if interface is None:
            self.interface.add_system(system_prompt)
        self._responses = list(responses)
        self.request_snapshots = []

    def send(self, message):
        if isinstance(message, str):
            self.interface.add_user_message(message)
        elif isinstance(message, list):
            self.interface.add_tool_results(message)
        else:
            raise TypeError(type(message))
        self.request_snapshots.append(self.interface.to_dict())
        response = self._responses.pop(0)
        blocks = []
        if response.text:
            blocks.append(TextBlock(response.text))
        for tc in response.tool_calls or []:
            blocks.append(
                ToolCallBlock(id=tc.id or "", name=tc.name, args=dict(tc.args or {}))
            )
        if blocks:
            self.interface.add_assistant_message(blocks)
        return response


class _FakeService:
    provider = "mock"
    model = "mock-model"
    api_key = "fake"
    _base_url = None
    _provider_defaults = {}

    def __init__(self, session_responses):
        self._session_responses = [list(batch) for batch in session_responses]
        self.sessions = []
        self.create_session_kwargs = []

    def create_session(self, *, system_prompt, interface=None, **kwargs):
        self.create_session_kwargs.append(
            {"system_prompt": system_prompt, "interface": interface, **kwargs}
        )
        session = _FakeSession(
            self._session_responses.pop(0),
            system_prompt=system_prompt,
            interface=interface,
        )
        self.sessions.append(session)
        return session

    def make_tool_result(self, tool_name, result, *, tool_call_id=None, provider=None):
        return ToolResultBlock(id=tool_call_id or "", name=tool_name, content=result)


def _resp(text="", tool_calls=None):
    return LLMResponse(text=text, tool_calls=list(tool_calls or []), usage=None)


def _run_with_service(
    tmp_path,
    monkeypatch,
    service,
    *,
    schemas=None,
    dispatch=None,
    parent_events=None,
):
    import lingtai.llm.service as service_mod

    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    agent = make_daemon_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    if parent_events is not None:
        monkeypatch.setattr(
            mgr,
            "_log",
            lambda event_type, **fields: parent_events.append(
                {"event": event_type, **fields}
            ),
        )
    em_id = "em-test"
    run_dir = make_daemon_run_dir(agent, em_id=em_id)
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }
    if schemas is None or dispatch is None:
        schemas, dispatch = mgr._build_tool_surface([])
    result = mgr._run_emanation(
        em_id,
        run_dir,
        schemas,
        dispatch,
        "task",
        threading.Event(),
    )
    return mgr, run_dir, result


def test_lingtai_run_scoped_checkpoint_persists_and_projects(tmp_path, monkeypatch):
    parent_events = []
    call = ToolCall(
        name="checkpoint",
        id="checkpoint-1",
        args={
            "phase_id": "inspect-complete",
            "phase_status": "complete",
            "completed": [{"id": "inspect", "outcome": "looked at daemon state"}],
            "next_action": "token=ghp_abcdefghijklmnopqrstuvwxyzABCDE",
            "evidence_refs": [{"kind": "event", "ref": "daemon_start"}],
        },
    )
    service = _FakeService([[_resp(tool_calls=[call]), _resp("done")]])

    mgr, run_dir, result = _run_with_service(
        tmp_path,
        monkeypatch,
        service,
        parent_events=parent_events,
    )

    assert result == "done"
    first_tools = service.create_session_kwargs[0]["tools"]
    assert "checkpoint" in {schema.name for schema in first_tools}
    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    checkpoint = state["latest_checkpoint"]
    assert checkpoint["schema"] == "lingtai.daemon.checkpoint.v1"
    assert checkpoint["run_id"] == run_dir.run_id
    assert checkpoint["checkpoint_id"] == f"{run_dir.run_id}:1"
    assert checkpoint["sequence"] == 1
    assert checkpoint["phase_id"] == "inspect-complete"
    check = mgr._check_snapshot_from_paths(
        "em-test",
        run_path=run_dir.path,
        daemon_json_path=run_dir.daemon_json_path,
        events_path=run_dir.events_path,
        last=20,
        truncate=500,
    )
    assert check["checkpoint"]["phase_id"] == "inspect-complete"
    events = [json.loads(line) for line in run_dir.events_path.read_text().splitlines()]
    tool_call_events = [
        event
        for event in events
        if event.get("event") == "tool_call" and event.get("name") == "checkpoint"
    ]
    assert tool_call_events[-1]["args_preview"] == "{}"
    checkpoint_events = [
        event for event in events if event.get("event") == "checkpoint"
    ]
    assert (
        checkpoint_events[-1]["checkpoint"]["checkpoint_id"]
        == checkpoint["checkpoint_id"]
    )
    normalized = [
        event
        for event in parent_events
        if event.get("event") == "daemon_tool_call_normalized"
        and event.get("tool_name") == "checkpoint"
    ]
    assert normalized[-1]["tool_args"] == {}
    assert "ghp_" not in json.dumps(parent_events)


def test_checkpoint_complete_does_not_mark_run_done_or_bypass_finish(
    tmp_path, monkeypatch
):
    from lingtai.kernel._fsutil import atomic_write_json
    from lingtai.mcp_servers.daemon_common.server import (
        DESCRIPTION as FINISH_DESCRIPTION,
        FINISH_SCHEMA,
        _validate_finish,
    )
    import lingtai.llm.service as service_mod

    checkpoint_call = ToolCall(
        name="checkpoint",
        id="checkpoint-complete",
        args={
            "phase_id": "phase-one",
            "phase_status": "complete",
            "next_action": "Create auto-next-action.txt with content EXECUTED",
        },
    )
    finish_call = ToolCall(
        name="finish",
        id="finish-done",
        args={"status": "done", "summary": "phase two completed explicitly"},
    )
    observations = []
    run_dir_holder = {}

    class _InspectingSession(_FakeSession):
        def send(self, message):
            if isinstance(message, list):
                run_dir = run_dir_holder["run_dir"]
                state = DaemonRunDir.read_state_from_disk(run_dir.path)
                events = [
                    json.loads(line)
                    for line in run_dir.events_path.read_text().splitlines()
                ]
                observations.append(
                    {
                        "state": state["state"],
                        "finished_at": state["finished_at"],
                        "phase_status": state.get("latest_checkpoint", {}).get(
                            "phase_status"
                        ),
                        "completion_exists": (
                            run_dir.path / "daemon_completion.json"
                        ).exists(),
                        "next_action_executed": (
                            run_dir.path / "auto-next-action.txt"
                        ).exists(),
                        "daemon_done_seen": any(
                            event.get("event") == "daemon_done" for event in events
                        ),
                    }
                )
            return super().send(message)

    class _InspectingService(_FakeService):
        def create_session(self, *, system_prompt, interface=None, **kwargs):
            self.create_session_kwargs.append(
                {"system_prompt": system_prompt, "interface": interface, **kwargs}
            )
            session = _InspectingSession(
                self._session_responses.pop(0),
                system_prompt=system_prompt,
                interface=interface,
            )
            self.sessions.append(session)
            return session

    service = _InspectingService(
        [
            [
                _resp(tool_calls=[checkpoint_call]),
                _resp(tool_calls=[finish_call]),
                _resp("done"),
            ]
        ]
    )
    monkeypatch.setattr(service_mod, "LLMService", lambda **_kwargs: service)
    agent = make_daemon_agent(tmp_path, ["daemon"])
    mgr = agent.get_capability("daemon")
    em_id = "em-finish-authority"
    run_dir = make_daemon_run_dir(
        agent,
        em_id=em_id,
        call_parameters={"mcp": [{"name": "daemon_common", "transport": "stdio"}]},
    )
    run_dir_holder["run_dir"] = run_dir
    mgr._emanations[em_id] = {
        "followup_buffer": "",
        "followup_lock": threading.Lock(),
        "run_dir": run_dir,
    }

    finish_schema = FunctionSchema(
        name="finish",
        description=FINISH_DESCRIPTION,
        parameters=FINISH_SCHEMA,
    )

    def finish(args):
        monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", run_dir.run_id)
        payload = _validate_finish(args)
        atomic_write_json(
            run_dir.path / "daemon_completion.json",
            payload,
            ensure_ascii=False,
            indent=2,
        )
        return {
            "status": "ok",
            "completion_status": payload["status"],
            "message": "daemon completion recorded",
        }

    result = mgr._run_emanation(
        em_id,
        run_dir,
        [finish_schema],
        {"finish": finish},
        "complete two phases",
        threading.Event(),
    )

    assert result == "done"
    assert observations == [
        {
            "state": "running",
            "finished_at": None,
            "phase_status": "complete",
            "completion_exists": False,
            "next_action_executed": False,
            "daemon_done_seen": False,
        },
        {
            "state": "running",
            "finished_at": None,
            "phase_status": "complete",
            "completion_exists": True,
            "next_action_executed": False,
            "daemon_done_seen": False,
        },
    ]
    final_state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert final_state["state"] == "done"
    assert final_state["finished_at"] is not None
    assert not (run_dir.path / "auto-next-action.txt").exists()

    events = [json.loads(line) for line in run_dir.events_path.read_text().splitlines()]
    checkpoint_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "checkpoint"
    )
    finish_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "tool_call" and event.get("name") == "finish"
    )
    done_index = next(
        index
        for index, event in enumerate(events)
        if event.get("event") == "daemon_done"
    )
    assert checkpoint_index < finish_index < done_index


def test_checkpoint_reserved_schema_replaces_external_collision(tmp_path, monkeypatch):
    external_calls = []
    external_schema = FunctionSchema(
        name="checkpoint",
        description="external collision",
        parameters={"type": "object", "properties": {"wrong": {"type": "string"}}},
    )
    call = ToolCall(
        name="checkpoint",
        id="checkpoint-reserved",
        args={"phase_id": "internal", "phase_status": "complete"},
    )
    service = _FakeService([[_resp(tool_calls=[call]), _resp("done")]])

    _mgr, run_dir, result = _run_with_service(
        tmp_path,
        monkeypatch,
        service,
        schemas=[external_schema],
        dispatch={"checkpoint": lambda args: external_calls.append(args)},
    )

    assert result == "done"
    schemas = [
        schema
        for schema in service.create_session_kwargs[0]["tools"]
        if schema.name == "checkpoint"
    ]
    assert len(schemas) == 1
    assert schemas[0].description != "external collision"
    assert external_calls == []
    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert state["latest_checkpoint"]["phase_id"] == "internal"


def test_ordinary_turn_does_not_create_or_advance_checkpoint(tmp_path, monkeypatch):
    service = _FakeService([[_resp("plain done")]])

    mgr, run_dir, _result = _run_with_service(tmp_path, monkeypatch, service)

    state = DaemonRunDir.read_state_from_disk(run_dir.path)
    assert "latest_checkpoint" not in state
    check = mgr._check_snapshot_from_paths(
        "em-test",
        run_path=run_dir.path,
        daemon_json_path=run_dir.daemon_json_path,
        events_path=run_dir.events_path,
        last=20,
        truncate=500,
    )
    assert "checkpoint" not in check


def test_checkpoint_sequences_are_monotonic_unique(tmp_path):
    rd = make_daemon_run_dir(parent_working_dir=tmp_path / "agent", em_id="em-seq")

    first = rd.record_checkpoint({"phase_id": "one", "phase_status": "complete"})
    second = rd.record_checkpoint({"phase_id": "two", "phase_status": "blocked"})

    assert [first["sequence"], second["sequence"]] == [1, 2]
    assert first["checkpoint_id"] != second["checkpoint_id"]
    assert (
        DaemonRunDir.read_state_from_disk(rd.path)["latest_checkpoint"]["sequence"] == 2
    )


def test_checkpoint_concurrent_sequences_are_unique(tmp_path):
    rd = make_daemon_run_dir(
        parent_working_dir=tmp_path / "agent", em_id="em-concurrent"
    )
    results = []
    lock = threading.Lock()

    def worker(index):
        saved = rd.record_checkpoint(
            {"phase_id": f"phase-{index}", "phase_status": "complete"}
        )
        with lock:
            results.append(saved)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    sequences = sorted(item["sequence"] for item in results)
    checkpoint_ids = {item["checkpoint_id"] for item in results}
    assert sequences == list(range(1, 9))
    assert len(checkpoint_ids) == 8
    assert (
        DaemonRunDir.read_state_from_disk(rd.path)["latest_checkpoint"]["sequence"] == 8
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"phase_id": "", "phase_status": "complete"},
        {"phase_id": "x" * 97, "phase_status": "complete"},
        {"phase_id": "ok", "phase_status": "waiting"},
        {"phase_id": "ok", "phase_status": []},
        {"phase_id": "ok", "phase_status": "complete", "completed": [{}]},
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "completed": [{"id": "x", "outcome": "y"}] * 9,
        },
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "completed": [{"id": "x" * 129, "outcome": "y"}],
        },
        {"phase_id": "ok", "phase_status": "complete", "next_action": "x" * 513},
        {"phase_id": "ok", "phase_status": "complete", "evidence_refs": [{}]},
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "evidence_refs": [{"kind": "log", "ref": "x"}],
        },
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "evidence_refs": [{"kind": [], "ref": "x"}],
        },
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "evidence_refs": [{"kind": "event", "ref": "../x"}],
        },
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "evidence_refs": [{"kind": "artifact", "ref": "/tmp/x"}],
        },
        {"phase_id": "ok", "phase_status": "complete", "extra": "nope"},
        {"phase_id": "ok", "phase_status": "complete", "sequence": True},
    ],
)
def test_checkpoint_rejects_invalid_shapes_and_bounds(tmp_path, payload):
    rd = make_daemon_run_dir(parent_working_dir=tmp_path / "agent", em_id="em-invalid")

    with pytest.raises(ValueError):
        rd.record_checkpoint(payload)
    assert "latest_checkpoint" not in DaemonRunDir.read_state_from_disk(rd.path)


def test_checkpoint_rejects_symlink_artifact_escape_and_redacts_text(tmp_path):
    rd = make_daemon_run_dir(parent_working_dir=tmp_path / "agent", em_id="em-redact")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (rd.path / "escape").symlink_to(outside)

    with pytest.raises(ValueError):
        rd.record_checkpoint(
            {
                "phase_id": "bad-link",
                "phase_status": "failed",
                "evidence_refs": [{"kind": "artifact", "ref": "escape"}],
            }
        )

    saved = rd.record_checkpoint(
        {
            "phase_id": "redact",
            "phase_status": "complete",
            "completed": [
                {"id": "api_key=sk-abcdefghijklmnopqrstuvwxyz", "outcome": "kept"}
            ],
            "next_action": "token=ghp_abcdefghijklmnopqrstuvwxyzABCDE",
        }
    )
    assert "sk-" not in saved["completed"][0]["id"]
    assert "ghp_" not in saved["next_action"]


def test_checkpoint_rejects_secret_bearing_evidence_refs(tmp_path):
    rd = make_daemon_run_dir(
        parent_working_dir=tmp_path / "agent", em_id="em-secret-ref"
    )
    secret = "ghp_abcdefghijklmnopqrstuvwxyzABCDE"

    for kind, ref in (
        ("event", f"event-{secret}"),
        ("artifact", f"artifacts/token={secret}/report.txt"),
    ):
        with pytest.raises(ValueError, match="must not contain secret material"):
            rd.record_checkpoint(
                {
                    "phase_id": "secret-ref",
                    "phase_status": "complete",
                    "evidence_refs": [{"kind": kind, "ref": ref}],
                }
            )

    assert "latest_checkpoint" not in DaemonRunDir.read_state_from_disk(rd.path)


def test_checkpoint_utf8_byte_limits_are_not_character_limits(tmp_path):
    rd = make_daemon_run_dir(parent_working_dir=tmp_path / "agent", em_id="em-utf8")

    saved = rd.record_checkpoint(
        {
            "phase_id": "界" * 32,
            "phase_status": "complete",
            "completed": [{"id": "界" * 42, "outcome": "界" * 85}],
            "next_action": "界" * 170,
        }
    )
    assert saved["phase_id"] == "界" * 32

    for payload in (
        {"phase_id": "界" * 33, "phase_status": "complete"},
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "completed": [{"id": "界" * 43, "outcome": "ok"}],
        },
        {
            "phase_id": "ok",
            "phase_status": "complete",
            "completed": [{"id": "ok", "outcome": "界" * 86}],
        },
        {"phase_id": "ok", "phase_status": "complete", "next_action": "界" * 171},
    ):
        with pytest.raises(ValueError):
            rd.record_checkpoint(payload)


def test_checkpoint_write_failure_leaves_previous_valid_state(tmp_path, monkeypatch):
    rd = make_daemon_run_dir(parent_working_dir=tmp_path / "agent", em_id="em-atomic")
    first = rd.record_checkpoint({"phase_id": "first", "phase_status": "complete"})
    original = rd._atomic_write_json

    def fail_daemon_json(path: Path, data: dict):
        if path == rd.daemon_json_path:
            raise OSError("simulated write failure")
        return original(path, data)

    monkeypatch.setattr(rd, "_atomic_write_json", fail_daemon_json)
    with pytest.raises(OSError):
        rd.record_checkpoint({"phase_id": "second", "phase_status": "complete"})
    state = DaemonRunDir.read_state_from_disk(rd.path)
    assert state["latest_checkpoint"] == first


def test_checkpoint_event_failure_does_not_fail_committed_authority(
    tmp_path, monkeypatch
):
    rd = make_daemon_run_dir(
        parent_working_dir=tmp_path / "agent", em_id="em-event-fail"
    )
    original = rd._append_jsonl

    def fail_checkpoint_event(path: Path, entry: dict):
        if path == rd.events_path and entry.get("event") == "checkpoint":
            raise OSError("simulated event append failure")
        return original(path, entry)

    monkeypatch.setattr(rd, "_append_jsonl", fail_checkpoint_event)
    saved = rd.record_checkpoint({"phase_id": "committed", "phase_status": "complete"})

    state = DaemonRunDir.read_state_from_disk(rd.path)
    assert state["latest_checkpoint"] == saved
    events = [json.loads(line) for line in rd.events_path.read_text().splitlines()]
    assert not any(event.get("event") == "checkpoint" for event in events)


def test_old_runs_without_checkpoint_omit_projection(tmp_path):
    agent = make_daemon_agent(tmp_path)
    mgr = agent.get_capability("daemon")
    rd = make_daemon_run_dir(agent, em_id="em-old")
    register_daemon_entry(mgr, "em-old", rd)

    out = mgr.handle({"action": "check", "id": "em-old"})

    assert "checkpoint" not in out


def test_fresh_manager_reads_historical_checkpoint_from_disk(tmp_path):
    first_agent = make_daemon_agent(tmp_path)
    rd = make_daemon_run_dir(first_agent, em_id="em-historical")
    rd.record_checkpoint({"phase_id": "persisted", "phase_status": "complete"})
    first_agent._workdir_lease.release()

    fresh_agent = make_daemon_agent(tmp_path)
    fresh_mgr = fresh_agent.get_capability("daemon")
    out = fresh_mgr.handle({"action": "check", "id": "em-historical"})

    assert out["checkpoint"]["phase_id"] == "persisted"
    assert out["checkpoint"]["sequence"] == 1


def test_checkpoint_schema_survives_compact_reconstruction(tmp_path, monkeypatch):
    compact = ToolCall(name="compact", args={"action": "run", "_reason": "handoff"}, id="compact-1")
    checkpoint = ToolCall(
        name="checkpoint",
        args={"phase_id": "after-compact", "phase_status": "complete"},
        id="checkpoint-1",
    )
    service = _FakeService(
        [
            [_resp(tool_calls=[compact])],
            [_resp(tool_calls=[checkpoint]), _resp("done")],
        ]
    )

    _mgr, run_dir, result = _run_with_service(tmp_path, monkeypatch, service)

    assert result == "done"
    assert len(service.create_session_kwargs) == 2
    assert all(
        "checkpoint" in {schema.name for schema in kwargs["tools"]}
        for kwargs in service.create_session_kwargs
    )
    assert (
        DaemonRunDir.read_state_from_disk(run_dir.path)["latest_checkpoint"]["phase_id"]
        == "after-compact"
    )
