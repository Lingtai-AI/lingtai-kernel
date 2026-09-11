"""Vertical evidence for Shell's declared official host-plugin slice."""
from __future__ import annotations

import sys
import time

import pytest

from lingtai.agent import Agent
from tests._service_helpers import make_gemini_mock_service


@pytest.fixture
def shell_agent(tmp_path):
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="shell-tool-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"shell": {"yolo": True}},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


def _run_input(command: str, *, asynchronous: bool = False) -> dict:
    return {
        "command": command,
        "timeout": None,
        "working_dir": None,
        "async": asynchronous,
        "reminder": None,
    }


def _official_command(marker: str) -> str:
    """Return a command accepted by the selected POSIX/PowerShell dialect."""
    if sys.platform == "win32":
        return f"Write-Output {marker}"
    return f"printf {marker}"


def _official_sleep_command(seconds: int = 30) -> str:
    if sys.platform == "win32":
        return f"Start-Sleep -Seconds {seconds}"
    return f"sleep {seconds}"


def _allow_policy(tmp_path) -> str:
    path = tmp_path / "allow-policy.json"
    path.write_text('{"allow": ["echo"]}', encoding="utf-8")
    return str(path)


def _policy_mode(manager) -> str:
    """Name the bound command policy's mode without executing anything."""
    policy = manager._policy
    summary = policy.describe()
    if not summary:
        # Parsed only; nothing is spawned.
        assert policy.is_allowed("sudo -n true")
        return "yolo"
    return summary.split(" MODE", 1)[0].lower()


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # Detached binding and preset ``shell: {}`` supply no keys at all.
        ({}, "yolo"),
        # ``setup()`` with nothing supplied forwards explicit Nones.
        ({"yolo": None, "policy_file": None, "shell_kind": None}, "yolo"),
        ({"yolo": True}, "yolo"),
        ({"yolo": True, "policy_file": "ALLOW"}, "yolo"),
        ({"yolo": False}, "denylist"),
        ({"policy_file": "ALLOW"}, "allowlist"),
        ({"yolo": False, "policy_file": "ALLOW"}, "allowlist"),
    ],
)
def test_shell_binding_resolves_one_default_policy_rule(shell_agent, tmp_path, values, expected):
    from lingtai.adapters.tool_plugin_host import (
        StaticConfigurationAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.tool_plugin import ToolPluginHost
    from lingtai.tools.bash._tool_family import DECLARATION

    allow = _allow_policy(tmp_path)
    values = {key: allow if value == "ALLOW" else value for key, value in values.items()}
    host = ToolPluginHost.grant(
        DECLARATION,
        agent_host_ports(
            shell_agent, "shell", {"configuration": StaticConfigurationAdapter(values)},
        ),
    )
    bound = DECLARATION.bind(host)
    assert _policy_mode(bound.handler.__self__.manager) == expected


def test_canonical_init_sample_shell_default_adds_no_restriction(shell_agent):
    from pathlib import Path

    from lingtai.adapters.tool_plugin_host import (
        StaticConfigurationAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.config_resolve import load_jsonc
    from lingtai.kernel.tool_plugin import ToolPluginHost
    from lingtai.tools.bash._tool_family import DECLARATION

    sample = load_jsonc(Path(__file__).parents[1] / "src/lingtai/init.jsonc")
    shell_config = sample["manifest"]["capabilities"]["shell"]
    host = ToolPluginHost.grant(
        DECLARATION,
        agent_host_ports(
            shell_agent, "shell",
            {"configuration": StaticConfigurationAdapter(dict(shell_config))},
        ),
    )
    assert _policy_mode(DECLARATION.bind(host).handler.__self__.manager) == "yolo"


def test_default_shell_is_permissive_on_every_composition_route(tmp_path):
    """Main, preset, direct setup, and detached Shell share the binding's rule."""
    from lingtai.kernel.daemon_supervisor.agent_stub import DaemonSupervisorAgentStub
    from lingtai.tools.bash import _setup_detached_daemon_shell, setup as setup_shell
    from lingtai.tools.daemon import _ToolCollector
    from tests._daemon_helpers import make_daemon_run_dir

    allow = _allow_policy(tmp_path)

    def _agent(name: str, capabilities: dict) -> Agent:
        return Agent(
            service=make_gemini_mock_service(), agent_name=name,
            working_dir=tmp_path / name, capabilities=capabilities,
        )

    def _mode(handler) -> str:
        return _policy_mode(handler.__self__.manager)

    main = _agent("main", {})
    try:
        assert _mode(main._tool_handlers["shell"]) == "yolo"

        daemon = main.get_capability("daemon")
        llm = {"provider": "mock", "model": "mock"}
        for caps, expected in (
            ({"shell": {}}, "yolo"),
            ({"shell": {"yolo": False}}, "denylist"),
            ({"shell": {"policy_file": allow}}, "allowlist"),
        ):
            _schemas, handlers = daemon._instantiate_preset_capabilities(caps, llm)
            assert _mode(handlers["shell"]) == expected, caps
        # A preset that omits shell borrows the parent's default Shell.
        _schemas, dispatch = daemon._build_tool_surface(
            ["shell"], preset_surface=daemon._instantiate_preset_capabilities({}, llm),
        )
        assert _mode(dispatch["shell"]) == "yolo"

        assert _policy_mode(setup_shell(main)) == "yolo"
        assert _policy_mode(setup_shell(main, yolo=False)) == "denylist"
    finally:
        main.stop(timeout=1.0)

    # Explicit manifest restrictions are not masked by a merged core default.
    for name, shell_config, expected in (
        ("restricted", {"yolo": False}, "denylist"),
        ("policy", {"policy_file": allow}, "allowlist"),
    ):
        agent = _agent(name, {"shell": shell_config})
        try:
            assert _mode(agent._tool_handlers["shell"]) == expected, shell_config
        finally:
            agent.stop(timeout=1.0)

    parent = tmp_path / "detached-parent"
    run_dir = make_daemon_run_dir(parent_working_dir=parent, tools=["shell"])
    manager, _adapter = _setup_detached_daemon_shell(
        _ToolCollector(DaemonSupervisorAgentStub(parent)), run_dir=run_dir,
    )
    assert _policy_mode(manager) == "yolo"


def test_shell_declaration_is_static_and_derives_its_shipped_surface():
    from lingtai.kernel.tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES
    from lingtai.tools.bash._tool_family import DECLARATION, get_schema

    assert DECLARATION.name == "shell"
    assert DECLARATION.actions == ("run", "poll", "cancel")
    assert DECLARATION.public_actions == (
        "run", "poll", "cancel", "settings", "manual"
    )
    assert DECLARATION.requires == ("workdir", "notifications", "configuration")
    assert DECLARATION.manual == "shell"
    assert "shell" in OFFICIAL_TOOL_PLUGIN_NAMES
    assert get_schema()["properties"]["action"]["enum"] == list(DECLARATION.public_actions)


def test_normal_shell_setup_and_manifest_reject_detached_only_overrides(tmp_path):
    """Normal capability configuration cannot replace Shell's notification destination."""
    from lingtai.tools.bash import setup as setup_shell
    from lingtai.tools.registry import setup_capability

    ordinary = Agent(
        service=make_gemini_mock_service(), working_dir=tmp_path / "ordinary", capabilities={},
    )
    try:
        # Shell is an ordinary Agent default. Both rejected calls leave that
        # existing normal-Agent destination and handoff untouched.
        ordinary_manager = ordinary._tool_handlers["shell"].__self__.manager
        ordinary_notifications = ordinary_manager._notifications
        with pytest.raises(RuntimeError, match="notification_port"):
            setup_shell(ordinary, notification_port="not-a-port")
        with pytest.raises(RuntimeError, match="async_handoff"):
            setup_capability(ordinary, "shell", async_handoff="not-a-daemon-handoff")
        assert ordinary._tool_handlers["shell"].__self__.manager is ordinary_manager
        assert ordinary_manager._notifications is ordinary_notifications
        assert "wake you as a notification" in ordinary_manager._async_handoff
    finally:
        ordinary.stop(timeout=1.0)

    # Agent manifests flow capability kwargs through the same public setup
    # surface, so detached-only values cannot install a poisoned normal binding.
    with pytest.raises(RuntimeError, match="async_handoff.*notification_port"):
        Agent(
            service=make_gemini_mock_service(), working_dir=tmp_path / "manifest",
            capabilities={"shell": {
                "notification_port": "not-a-port", "async_handoff": "not-a-daemon-handoff",
            }},
        )


def test_shell_bind_uses_only_its_narrow_ports_and_defers_rehydration(shell_agent, monkeypatch):
    from lingtai.adapters.tool_plugin_host import (
        StaticConfigurationAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.tool_plugin import ToolPluginHost
    from lingtai.tools.bash import ShellManager
    from lingtai.tools.bash._tool_family import DECLARATION

    rehydrated: list[ShellManager] = []
    monkeypatch.setattr(
        ShellManager, "_rehydrate_async_jobs", lambda manager: rehydrated.append(manager)
    )
    host = ToolPluginHost.grant(
        DECLARATION,
        agent_host_ports(
            shell_agent,
            "shell",
            {"configuration": StaticConfigurationAdapter({"yolo": True})},
        ),
    )
    assert host.granted == ("workdir", "notifications", "configuration")
    with pytest.raises(AttributeError, match="prompt_section"):
        host.prompt_section

    bound = DECLARATION.bind(host)
    assert rehydrated == []
    assert bound.activate is not None
    bound.activate()
    assert len(rehydrated) == 1


def test_official_shell_mount_uses_only_narrow_ports_and_keeps_real_dispatch(shell_agent):
    from lingtai.tools.bash._tool_family import DECLARATION

    assert shell_agent.official_tool_plugins["shell"] is DECLARATION
    assert [schema.name for schema in shell_agent._tool_schemas].count("shell") == 1

    handler = shell_agent._tool_handlers["shell"]
    dispatcher = handler.__self__
    manager = dispatcher.manager
    assert manager._agent is None
    assert manager._notifications is not None

    sync = handler({
        "action": "run",
        "input": _run_input(_official_command("official-shell")),
        "reasoning": "verify official Shell execution",
    })
    assert sync["status"] == "ok"
    assert sync["exit_code"] == 0
    assert sync["stdout"] == "official-shell"
    assert sync["command_status"] == "success"

    settings = handler({
        "action": "settings",
        "input": {},
        "reasoning": "inspect applied Shell settings",
    })
    assert [row["key"] for row in settings["settings"]] == [
        "shell_kind",
        "sync_timeout_default_seconds",
        "sync_timeout_max_seconds",
        "result_max_chars",
        "async_default",
        "async_reminder_default_seconds",
        "command_policy",
    ]
    assert shell_agent._build_system_prompt()

    manual = handler({"action": "manual", "input": {}, "reasoning": "read Shell manual"})
    assert manual["status"] == "ok"
    assert manual["content"][0]["text"]
    assert manual["structuredContent"]["manual_path"].endswith(
        "capabilities/shell/SKILL.md"
    )


def test_official_shell_async_run_and_poll_keep_durable_engine_semantics(shell_agent):
    handler = shell_agent._tool_handlers["shell"]
    started = handler({
        "action": "run",
        "input": _run_input(_official_command("official-async"), asynchronous=True),
        "reasoning": "verify official Shell async supervision",
    })
    assert started["status"] == "ok"
    assert "wake you as a notification" in started["handoff"]
    job_id = started["job_id"]

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = handler({
            "action": "poll",
            "input": {"job_id": job_id},
            "reasoning": "read durable async result",
        })
        if result["status"] == "done":
            break
        time.sleep(0.05)
    else:  # pragma: no cover - assertion branch is the evidence
        pytest.fail("official Shell async job did not reach a durable terminal result")

    assert result["exit_code"] == 0
    assert result["stdout"] == "official-async"
    assert result["command_status"] == "success"


def test_official_shell_async_cancel_is_native_route_safe(shell_agent):
    handler = shell_agent._tool_handlers["shell"]
    started = handler({
        "action": "run",
        "input": _run_input(_official_sleep_command(), asynchronous=True),
        "reasoning": "verify official Shell cancellation",
    })
    assert started["status"] == "ok"
    cancelled = handler({
        "action": "cancel",
        "input": {"job_id": started["job_id"]},
        "reasoning": "cancel official Shell async job",
    })
    assert cancelled == {"status": "cancelled", "job_id": started["job_id"]}
