from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from lingtai.execution_policy.configured import ConfiguredExecutionPolicy
from lingtai.kernel.execution_policy import ExecutionDecision
from lingtai.tools.daemon import DaemonManager, get_schema, setup


class _SelectingPolicy:
    api_version = 1

    def __init__(self, preset: str) -> None:
        self.preset = preset
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return ExecutionDecision(
            preset=request.preset or self.preset,
            backend=request.backend,
            route_id="selected-route",
        )


class _BackendSelectingPolicy:
    api_version = 1

    def __init__(self) -> None:
        self.requests = []

    def decide(self, request):
        self.requests.append(request)
        return ExecutionDecision(
            preset=None,
            backend="deepseek",
            route_id="dsh-fast",
        )


def _agent(tmp_path, allowed: list[str]):
    return SimpleNamespace(
        service=SimpleNamespace(model="mock-model"),
        _working_dir=tmp_path,
        _admin={"karma": True},
        _log=lambda *args, **kwargs: None,
        _read_preset_from_init=lambda: {"allowed": allowed},
    )


def test_daemon_schema_exposes_workload_without_changing_required_fields():
    schema = get_schema()
    emanate_branch = next(
        branch
        for branch in schema["allOf"]
        if branch["if"]["properties"]["action"].get("const") == "emanate"
    )
    task = emanate_branch["then"]["properties"]["input"]["properties"][
        "tasks"
    ]["items"]

    assert task["properties"]["workload"]["type"] == "string"
    assert task["required"] == ["task", "tools"]


def test_setup_uses_agent_canonical_init_reader_for_policy_declaration(tmp_path):
    (tmp_path / "init.json").write_text(
        "{ // valid JSONC handled by Agent._read_init\n}", encoding="utf-8"
    )
    (tmp_path / "policy.json").write_text(
        json.dumps(
            {
                "api_version": 1,
                "workloads": {
                    "worker": [
                        {"route_id": "worker", "preset": "allowed.json"}
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "health.json").write_text(
        json.dumps(
            {"api_version": 1, "routes": {"worker": {"available": True}}}
        ),
        encoding="utf-8",
    )
    agent = MagicMock()
    agent._working_dir = tmp_path
    agent._read_init.return_value = {
        "manifest": {
            "execution_policy": {
                "api_version": 1,
                "adapter": "configured",
                "config": "policy.json",
                "health": "health.json",
            }
        }
    }

    manager = setup(agent)

    agent._read_init.assert_called_once_with()
    assert isinstance(manager._execution_policy, ConfiguredExecutionPolicy)


def test_policy_selected_preset_still_passes_through_authorization(tmp_path):
    policy = _SelectingPolicy("not-allowed.json")
    agent = _agent(tmp_path, ["allowed.json"])
    manager = DaemonManager(agent, execution_policy=policy)

    result = manager._handle_emanate(
        [{"task": "plan", "tools": [], "workload": "architecture"}]
    )

    assert result["status"] == "error"
    assert "not in this agent's allowed list" in result["message"]
    assert policy.requests[0].workload == "architecture"
    assert policy.requests[0].parent_is_admin is True


def test_policy_defaults_omitted_workload_to_worker_and_reaches_preset_loader(
    tmp_path,
):
    policy = _SelectingPolicy("allowed.json")
    agent = _agent(tmp_path, ["allowed.json"])

    def load_preset(name):
        raise ValueError(f"sentinel {name}")

    agent.load_preset = load_preset
    manager = DaemonManager(agent, execution_policy=policy)

    result = manager._handle_emanate([{"task": "implement", "tools": []}])

    assert result["status"] == "error"
    assert "sentinel allowed.json" in result["message"]
    assert policy.requests[0].workload == "worker"


def test_policy_selected_dsh_backend_reaches_existing_cli_dispatch(tmp_path):
    policy = _BackendSelectingPolicy()
    manager = DaemonManager(_agent(tmp_path, []), execution_policy=policy)
    manager._handle_emanate_cli = MagicMock(
        return_value={"status": "dispatched", "backend": "deepseek"}
    )

    result = manager._handle_emanate(
        [{"task": "implement", "tools": [], "workload": "implementation"}]
    )

    assert result == {"status": "dispatched", "backend": "deepseek"}
    manager._handle_emanate_cli.assert_called_once()
    assert manager._handle_emanate_cli.call_args.kwargs["backend"] == "deepseek"
    routed_task = manager._handle_emanate_cli.call_args.args[0][0]
    assert routed_task["_execution_route_id"] == "dsh-fast"


def test_workload_rejects_whitespace_normalization(tmp_path):
    policy = _SelectingPolicy("allowed.json")
    manager = DaemonManager(
        _agent(tmp_path, ["allowed.json"]), execution_policy=policy
    )

    result = manager._handle_emanate(
        [{"task": "review", "tools": [], "workload": " review"}]
    )

    assert result == {
        "status": "error",
        "message": "tasks[0].workload must be a non-empty string",
    }
    assert policy.requests == []
