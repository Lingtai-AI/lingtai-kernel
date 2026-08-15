"""Behavioral contract for cooperative live daemon checkpoints.

The concrete consumer is a parent correcting a long-running external CLI daemon
before another blind timeout.  Delivery is cooperative: ``daemon.ask`` queues a
message, and the worker receives it exactly once when it next calls the reserved
``daemon_common.checkpoint`` tool.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import Event

import pytest
from mcp import Client

from lingtai.adapters.posix.notification_store import PosixNotificationStoreAdapter
from lingtai.kernel.daemon_supervisor.manifest import build_manifest
from lingtai.mcp_servers.daemon_common.server import build_server
from lingtai.tools.daemon.execution_host import DetachedDaemonExecutionHost
from lingtai.tools.daemon.run_dir import DaemonRunDir
from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _disk_state(run_dir: DaemonRunDir) -> dict:
    return DaemonRunDir.read_state_from_disk(run_dir.path)


def _checkpoint_events(run_dir: DaemonRunDir) -> list[dict]:
    return [
        json.loads(line)
        for line in run_dir.events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and json.loads(line).get("event") == "daemon_checkpoint"
    ]


def _system_events(parent_workdir: Path) -> list[dict]:
    snapshot = PosixNotificationStoreAdapter(parent_workdir).snapshot(
        lambda channel: channel == "system"
    )
    return snapshot.get("system", {}).get("data", {}).get("events", [])


async def test_active_cli_ask_is_delivered_once_at_checkpoint_without_terminal_mutation(
    tmp_path, monkeypatch,
):
    """One live correction reaches an active OpenCode-like run at its checkpoint."""
    agent = make_daemon_agent(tmp_path, ["daemon"])
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-checkpoint",
        backend="opencode",
        task="long OpenCode implementation",
        tools=[],
        call_parameters={"task": "long OpenCode implementation", "tools": []},
    )
    run_dir.update_state(backend="opencode", owner="supervisor", state="active")
    manager._emanations[run_dir.run_id] = {
        "detached": True,
        "task": "long OpenCode implementation",
        "start_time": time.time(),
        "timeout_s": 1200.0,
        "run_dir": run_dir,
        "backend": "opencode",
    }

    terminal_fields = (
        "state",
        "finished_at",
        "result_path",
        "terminal_notified",
        "terminal_notification_claim",
        "terminal_notification_receipt",
    )
    before = _disk_state(run_dir)
    before_terminal = {field: before.get(field) for field in terminal_fields}

    correction = "Stop broad QA; commit the bounded writer result now."
    ask = manager.handle(
        {"action": "ask", "id": run_dir.run_id, "message": correction}
    )
    assert ask == {
        "status": "queued",
        "id": run_dir.run_id,
        "delivery": "checkpoint",
        "message_id": ask.get("message_id"),
    }
    assert isinstance(ask["message_id"], str) and ask["message_id"]

    queued = manager.handle({"action": "check", "id": run_dir.run_id})
    assert queued["pending_checkpoint_messages"] == 1

    registration = manager._daemon_common_mcp_registration(run_dir)
    assert registration["env"]["LINGTAI_DAEMON_RUN_DIR"] == str(run_dir.path)
    assert registration["env"]["LINGTAI_DAEMON_RUN_ID"] == run_dir.run_id
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_DIR", str(run_dir.path))
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", run_dir.run_id)
    os.utime(run_dir.heartbeat_path, (1, 1))

    async with Client(build_server()) as client:
        first = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {
                    "state": "implementing",
                    "summary": "Authentic RED is preserved; minimum code is next.",
                    "artifacts": ["workspace/daemon-checkpoint-pr/red-evidence.md"],
                    "request": "Apply the parent's latest scope correction.",
                },
                "reasoning": "report progress and receive any queued correction",
            },
        )
        second = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {
                    "state": "implementing",
                    "summary": "Correction applied; continuing the bounded slice.",
                },
                "reasoning": "report the next cooperative boundary",
            },
        )

    first_payload = json.loads(first.content[0].text)
    second_payload = json.loads(second.content[0].text)
    assert first.is_error is False
    assert first_payload["status"] == "ok"
    assert first_payload["checkpoint_sequence"] == 1
    assert first_payload["messages"] == [
        {"id": ask["message_id"], "message": correction}
    ]
    assert second.is_error is False
    assert second_payload["checkpoint_sequence"] == 2
    assert second_payload["messages"] == []

    state = _disk_state(run_dir)
    assert state["latest_checkpoint"]["sequence"] == 2
    assert state["latest_checkpoint"]["state"] == "implementing"
    assert state["latest_checkpoint"]["delivered_message_ids"] == []
    assert state["pending_checkpoint_messages"] == []
    assert {field: state.get(field) for field in terminal_fields} == before_terminal
    assert run_dir.heartbeat_path.stat().st_mtime_ns > 1_000_000_000

    events = _checkpoint_events(run_dir)
    assert [event["sequence"] for event in events] == [1, 2]
    assert events[0]["delivered_message_ids"] == [ask["message_id"]]
    assert events[1]["delivered_message_ids"] == []

    checked = manager.handle({"action": "check", "id": run_dir.run_id})
    assert checked["latest_checkpoint"] == state["latest_checkpoint"]
    assert checked["pending_checkpoint_messages"] == 0

    notifications = [
        event
        for event in _system_events(agent._working_dir)
        if event.get("ref_id") == run_dir.run_id
        and str(event.get("idempotency_key", "")).startswith("daemon-checkpoint:")
    ]
    assert [event["idempotency_key"] for event in notifications] == [
        f"daemon-checkpoint:{run_dir.run_id}:1",
        f"daemon-checkpoint:{run_dir.run_id}:2",
    ]
    assert all(event.get("source") == "daemon" for event in notifications)
    assert all(event.get("kind") == "daemon_checkpoint" for event in notifications)
    assert all(event.get("terminal") is False for event in notifications)

    # Detached LingTai rebuilds the same reserved contract as local tools rather
    # than connecting the external daemon_common server.  The checkpoint must
    # therefore survive this compaction/rebuild surface too.
    local_run = make_daemon_run_dir(
        agent,
        handle="em-local-checkpoint",
        backend="lingtai",
        task="local checkpoint parity",
        tools=[],
    )
    manifest = build_manifest(
        run_id=local_run.run_id,
        backend="lingtai",
        parent_working_dir=str(agent._working_dir),
        run_dir=str(local_run.path),
        task="local checkpoint parity",
        tools=[],
        max_turns=1,
        timeout_s=30,
        group_id=None,
        mcp=[],
    )
    host = DetachedDaemonExecutionHost(
        local_run, manifest, Event(), Event(), capsule={"mcp": []}
    )
    schemas, dispatch = host._completion_surface()
    assert set(schemas) == {"checkpoint", "finish"}
    assert set(dispatch) == {"checkpoint", "finish"}
    local_result = dispatch["checkpoint"](
        {
            "action": "checkpoint",
            "input": {"state": "validating", "summary": "local parity works"},
            "_reasoning": "report from the detached LingTai local surface",
        }
    )
    assert local_result["status"] == "ok"
    assert local_result["checkpoint_sequence"] == 1
    assert _disk_state(local_run)["latest_checkpoint"]["summary"] == "local parity works"


@pytest.mark.parametrize(
    "backend,checkpoint_supported",
    [
        ("claude", False),
        ("claude-interactive", False),
        ("claude-p", True),
        ("claude-code", True),
        ("codex", True),
        ("opencode", True),
        ("mimocode", False),
        ("qwen-code", True),
        ("oh-my-pi", False),
        ("kimicode", True),
        ("cursor", False),
    ],
)
async def test_active_cli_ask_checkpoint_support_matches_mounted_common_mcp(
    tmp_path, backend, checkpoint_supported,
):
    """Only launch paths that really mount daemon_common accept live messages."""
    agent = make_daemon_agent(tmp_path, ["daemon"])
    manager = agent.get_capability("daemon")
    run_dir = make_daemon_run_dir(
        agent,
        handle=f"em-{backend}",
        backend=backend,
        task=f"active {backend} task",
        tools=[],
    )
    run_dir.update_state(backend=backend, owner="supervisor", state="active")
    manager._emanations[run_dir.run_id] = {
        "detached": True,
        "task": f"active {backend} task",
        "start_time": time.time(),
        "timeout_s": 1200.0,
        "run_dir": run_dir,
        "backend": backend,
    }

    result = manager.handle(
        {"action": "ask", "id": run_dir.run_id, "message": "bounded correction"}
    )
    queue = _disk_state(run_dir).get("pending_checkpoint_messages", [])
    if checkpoint_supported:
        assert result["status"] == "queued"
        assert result["delivery"] == "checkpoint"
        assert queue == [
            {
                "id": result["message_id"],
                "message": "bounded correction",
                "queued_at": queue[0]["queued_at"],
            }
        ]
    else:
        assert result == {
            "status": "busy",
            "id": run_dir.run_id,
            "message": "primary detached CLI run is still active; retry ask after terminal state",
        }
        assert queue == []


async def test_checkpoint_rejects_wrong_identity_invalid_payload_and_terminal_run(
    tmp_path, monkeypatch,
):
    """Trust, bounds and live-state gates reject before checkpoint mutation."""
    agent = make_daemon_agent(tmp_path, ["daemon"])
    run_dir = make_daemon_run_dir(
        agent, handle="em-checkpoint-guards", backend="opencode", tools=[]
    )
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_DIR", str(run_dir.path))
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", "wrong-run")

    async with Client(build_server()) as client:
        wrong_identity = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {"state": "working", "summary": "valid payload"},
                "reasoning": "attempt the wrong run",
            },
        )
    wrong_payload = json.loads(wrong_identity.content[0].text)
    assert wrong_payload["status"] == "error"
    assert "identity mismatch" in wrong_payload["error"]
    assert _disk_state(run_dir)["checkpoint_sequence"] == 0

    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", run_dir.run_id)
    async with Client(build_server()) as client:
        oversized = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {"state": "working", "summary": "x" * 4_001},
                "reasoning": "attempt an oversized checkpoint",
            },
        )
    oversized_payload = json.loads(oversized.content[0].text)
    assert oversized_payload["status"] == "error"
    assert "summary exceeds 4000 characters" in oversized_payload["error"]
    assert _disk_state(run_dir)["checkpoint_sequence"] == 0

    run_dir.mark_done("already terminal")
    before_terminal = _disk_state(run_dir)
    async with Client(build_server()) as client:
        terminal = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {"state": "late", "summary": "must not land"},
                "reasoning": "attempt a terminal checkpoint",
            },
        )
    terminal_payload = json.loads(terminal.content[0].text)
    assert terminal_payload["status"] == "error"
    assert "requires a live run" in terminal_payload["error"]
    assert _disk_state(run_dir) == before_terminal


async def test_checkpoint_wake_failure_keeps_record_and_delivers_drained_messages(
    tmp_path, monkeypatch,
):
    """A failed parent wake is honest without hiding an acknowledged message."""
    agent = make_daemon_agent(tmp_path, ["daemon"])
    run_dir = make_daemon_run_dir(
        agent, handle="em-checkpoint-wake", backend="opencode", tools=[]
    )
    message_id = run_dir.enqueue_checkpoint_message("use the smaller contract")
    assert message_id
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_DIR", str(run_dir.path))
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", run_dir.run_id)

    def fail_wake(*_args, **_kwargs):
        raise OSError("simulated wake failure")

    monkeypatch.setattr(
        "lingtai.mcp_servers.daemon_common.server._enqueue_system_notification",
        fail_wake,
    )
    payloads = []
    async with Client(build_server()) as client:
        for sequence in (1, 2):
            result = await client.call_tool(
                "checkpoint",
                {
                    "action": "checkpoint",
                    "input": {
                        "state": "working",
                        "summary": f"recorded checkpoint {sequence}",
                    },
                    "reasoning": "record progress even if the wake sink fails",
                },
            )
            assert result.is_error is True
            payloads.append(json.loads(result.content[0].text))

    assert payloads[0]["status"] == "error"
    assert payloads[0]["checkpoint_recorded"] is True
    assert payloads[0]["checkpoint_sequence"] == 1
    assert payloads[0]["messages"] == [
        {"id": message_id, "message": "use the smaller contract"}
    ]
    assert "parent wake failed" in payloads[0]["error"]
    assert payloads[1]["checkpoint_sequence"] == 2
    assert payloads[1]["messages"] == []
    state = _disk_state(run_dir)
    assert state["checkpoint_sequence"] == 2
    assert state["pending_checkpoint_messages"] == []


@pytest.mark.parametrize(
    ("failure_site", "failure_text"),
    [
        ("event", "simulated checkpoint event append failure"),
        ("heartbeat", "simulated checkpoint heartbeat touch failure"),
    ],
)
async def test_checkpoint_post_record_failure_keeps_drained_messages_visible(
    tmp_path, monkeypatch, failure_site, failure_text,
):
    """Post-record bookkeeping failures report the durable ack and messages."""
    agent = make_daemon_agent(tmp_path, ["daemon"])
    run_dir = make_daemon_run_dir(
        agent, handle=f"em-checkpoint-{failure_site}", backend="opencode", tools=[]
    )
    message_id = run_dir.enqueue_checkpoint_message("preserve this correction")
    assert message_id
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_DIR", str(run_dir.path))
    monkeypatch.setenv("LINGTAI_DAEMON_RUN_ID", run_dir.run_id)

    if failure_site == "event":
        original_append = DaemonRunDir._append_jsonl

        def fail_checkpoint_event(self, path, entry):
            if entry.get("event") == "daemon_checkpoint":
                raise OSError(failure_text)
            return original_append(self, path, entry)

        monkeypatch.setattr(DaemonRunDir, "_append_jsonl", fail_checkpoint_event)

        def restore_failure():
            monkeypatch.setattr(DaemonRunDir, "_append_jsonl", original_append)
    else:
        original_touch = Path.touch
        heartbeat_path = run_dir.heartbeat_path

        def fail_checkpoint_heartbeat(self, *args, **kwargs):
            if self == heartbeat_path:
                raise OSError(failure_text)
            return original_touch(self, *args, **kwargs)

        monkeypatch.setattr(Path, "touch", fail_checkpoint_heartbeat)

        def restore_failure():
            monkeypatch.setattr(Path, "touch", original_touch)

    async with Client(build_server()) as client:
        first = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {
                    "state": "working",
                    "summary": f"checkpoint before {failure_site} failure",
                },
                "reasoning": "preserve the durable result across a post-record failure",
            },
        )
        restore_failure()
        retry = await client.call_tool(
            "checkpoint",
            {
                "action": "checkpoint",
                "input": {"state": "working", "summary": "retry after failure"},
                "reasoning": "verify the acknowledged message is not redelivered",
            },
        )

    first_payload = json.loads(first.content[0].text)
    retry_payload = json.loads(retry.content[0].text)
    assert first.is_error is True
    assert first_payload["status"] == "error"
    assert first_payload["checkpoint_recorded"] is True
    assert first_payload["checkpoint_sequence"] == 1
    assert first_payload["messages"] == [
        {"id": message_id, "message": "preserve this correction"}
    ]
    assert failure_text in first_payload["error"]
    assert retry.is_error is False
    assert retry_payload["checkpoint_sequence"] == 2
    assert retry_payload["messages"] == []
    state = _disk_state(run_dir)
    assert state["checkpoint_sequence"] == 2
    assert state["pending_checkpoint_messages"] == []
