"""Focused Codex daemon-resume completion-contract regressions."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lingtai.tools.daemon import setup
from lingtai.tools.daemon.process_port import (
    DaemonProcessCommand,
    DaemonProcessExit,
    DaemonProcessHandle,
)
from tests._daemon_helpers import make_daemon_agent, make_daemon_run_dir, register_daemon_entry


class _Stderr:
    lines: list[str] = []

    def join(self, timeout: float = 2.0) -> None:
        return None


class _Port:
    """Fake Port that discovers the real per-ask MCP identity from Codex argv."""

    def __init__(self, finish_modes: list[str], usage: dict | None = None):
        self.finish_modes = finish_modes
        self.usage = usage
        self.commands: list[DaemonProcessCommand] = []
        self._handles: list[DaemonProcessHandle] = []
        self.discovered: list[tuple[str, str]] = []

    def spawn(self, command, *, group_id=None):
        handle = DaemonProcessHandle(len(self._handles))
        self._handles.append(handle)
        self.commands.append(command)
        completion_path = run_id = None
        for index, arg in enumerate(command.argv[:-1]):
            if arg != "-c":
                continue
            value = command.argv[index + 1]
            for key in ("LINGTAI_DAEMON_COMPLETION_FILE", "LINGTAI_DAEMON_RUN_ID"):
                match = re.search(rf'{key}\s*=\s*("(?:\\.|[^"])*")', value)
                if match:
                    parsed = json.loads(match.group(1))
                    if key == "LINGTAI_DAEMON_COMPLETION_FILE":
                        completion_path = parsed
                    else:
                        run_id = parsed
        assert completion_path and run_id
        self.discovered.append((completion_path, run_id))
        mode = self.finish_modes[min(len(self._handles) - 1, len(self.finish_modes) - 1)]
        if mode == "done":
            _completion(Path(completion_path), run_id)
        elif mode == "stale":
            _completion(Path(completion_path).parent.parent / "daemon_completion.json", run_id)
        elif mode == "invalid":
            path = Path(completion_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"status": "done", "run_id": run_id}), encoding="utf-8")
        elif mode == "null-summary":
            _completion(Path(completion_path), run_id, extra={"summary": None})
        elif mode == "unknown-key":
            _completion(Path(completion_path), run_id, extra={"unexpected": True})
        elif mode == "bad-artifacts":
            _completion(Path(completion_path), run_id, extra={"artifacts": ["ok", 3]})
        elif mode == "bad-summary":
            _completion(Path(completion_path), run_id, extra={"summary": 3})
        elif mode == "bad-reason":
            _completion(Path(completion_path), run_id, extra={"reason": False})
        elif mode == "bad-artifacts-type":
            _completion(Path(completion_path), run_id, extra={"artifacts": "not-a-list"})
        elif mode != "missing":
            _completion(Path(completion_path), run_id, mode)
        return handle

    def drain_stderr(self, handle, *, on_line=None, thread_name="daemon-stderr"):
        return _Stderr()

    def iter_stdout(self, handle, *, deadline=None):
        completed = {"type": "turn.completed"}
        if self.usage is not None:
            completed["usage"] = self.usage
        return iter([
            '{"type":"item.completed","item":{"type":"agent_message","text":"follow-up"}}\n',
            json.dumps(completed) + "\n",
        ])

    def wait(self, handle, *, timeout=None):
        return DaemonProcessExit(0)

    def release(self, handle):
        return True

    def terminate(self, handle, *, reason=None):
        return DaemonProcessExit(-15, reason)


def _completion(
    path: Path,
    run_id: str,
    status: str = "done",
    *,
    extra: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "lingtai.daemon_completion.v1",
        "status": status,
        "run_id": run_id,
        **({"reason": "blocked"} if status != "done" else {}),
    }
    if extra:
        payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_resume(
    tmp_path: Path,
    *,
    finish_modes: list[str] | None = None,
    usage: dict | None = None,
):
    agent = make_daemon_agent(tmp_path)
    port = _Port(finish_modes or ["done"], usage=usage)
    mgr = setup(agent, process_port=port)
    published: list[dict] = []
    mgr._publish_daemon_notification = lambda em_id, **kwargs: published.append({
        "id": em_id, **kwargs,
    })
    run_dir = make_daemon_run_dir(
        agent,
        handle="em-codex-contract",
        task="resume contract",
        tools=[],
        model="codex",
        timeout_s=30,
        backend="codex",
        call_parameters={"mcp": [{"name": "daemon_common", "transport": "stdio"}]},
    )
    run_dir._state["codex_session_id"] = "codex-session"
    run_dir._atomic_write_json(run_dir.daemon_json_path, run_dir._state)
    entry = register_daemon_entry(
        mgr, run_dir.handle, run_dir, backend="codex", ask_in_flight=False,
    )
    result = mgr._handle_ask_codex(run_dir.handle, entry, "continue")
    return result, entry, port, run_dir, mgr, published


def test_codex_resume_live_ask_provisions_and_accepts_fresh_finish(tmp_path):
    result, entry, port, run_dir, _mgr, published = _run_resume(tmp_path)

    assert result["status"] == "sent"
    assert entry["ask_future"].result(timeout=5) == {
        "status": "sent", "id": run_dir.handle, "output": "follow-up",
    }
    completion_path, run_id = port.discovered[0]
    assert run_id == run_dir.run_id
    assert entry["followup_completion_path"] == completion_path
    assert Path(completion_path).exists()
    command = port.commands[0].argv
    assert "-c" in command
    rendered = " ".join(command)
    assert "mcp_servers.daemon_common.command" in rendered
    assert "LINGTAI_DAEMON_COMPLETION_FILE" in rendered
    assert "live-" in completion_path
    assert not (run_dir.path / "daemon_completion.json").exists()
    assert [item["status"] for item in published] == ["follow-up completed"]


def test_sequential_live_asks_get_distinct_fresh_receipts(tmp_path):
    _result, entry, port, run_dir, mgr, published = _run_resume(
        tmp_path, finish_modes=["done", "missing"],
    )
    entry["ask_future"].result(timeout=5)
    first_path = entry["followup_completion_path"]

    second = mgr._handle_ask_codex(run_dir.handle, entry, "continue again")
    second_path = entry["followup_completion_path"]
    assert second["status"] == "sent"
    assert first_path != second_path
    assert Path(first_path).exists()
    # The second receipt is deliberately absent; an old receipt must not
    # satisfy the new path/generation.
    failure = entry["ask_future"].result(timeout=5)
    assert failure["status"] == "error"
    assert "completion" in failure["message"]
    assert [item["status"] for item in published] == [
        "follow-up completed", "follow-up failed",
    ]
    assert len(port.discovered) == 2


@pytest.mark.parametrize(
    "mode",
    [
        "missing", "stale", "failed", "incomplete", "invalid",
        "null-summary", "unknown-key", "bad-artifacts", "bad-summary",
        "bad-reason", "bad-artifacts-type",
    ],
)
def test_live_bad_fresh_finish_is_structured_failure_and_one_notification(tmp_path, mode):
    result, entry, _port, run_dir, _mgr, published = _run_resume(
        tmp_path, finish_modes=[mode],
    )
    assert result["status"] == "sent"
    failure = entry["ask_future"].result(timeout=5)
    assert failure["status"] == "error"
    assert failure["id"] == run_dir.handle
    assert "completion" in failure["message"]
    assert entry["ask_in_flight"] is False
    assert len(published) == 1
    assert published[0]["status"] == "follow-up failed"


def test_fresh_finish_receipt_gates_codex_usage_persistence(tmp_path):
    usage = {
        "input_tokens": 12,
        "cached_input_tokens": 2,
        "output_tokens": 7,
    }
    _result, entry, _port, run_dir, _mgr, _published = _run_resume(
        tmp_path / "valid", usage=usage,
    )
    assert entry["ask_future"].result(timeout=5)["status"] == "sent"
    valid_tokens = json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))["cli_tokens"]
    assert valid_tokens["calls"] == 1
    assert valid_tokens["input"] == 10
    assert valid_tokens["cached"] == 2

    _result, entry, _port, run_dir, _mgr, _published = _run_resume(
        tmp_path / "invalid", finish_modes=["null-summary"], usage=usage,
    )
    assert entry["ask_future"].result(timeout=5)["status"] == "error"
    invalid_tokens = json.loads(run_dir.daemon_json_path.read_text(encoding="utf-8"))["cli_tokens"]
    assert invalid_tokens["calls"] == 0
    assert invalid_tokens["input"] == 0
    assert invalid_tokens["output"] == 0
    assert invalid_tokens["cached"] == 0
