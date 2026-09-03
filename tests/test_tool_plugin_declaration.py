"""Focused behavioral coverage for official declared host-plugin mounts.

The shared primitive has several real vertical proofs: ``mcp`` demonstrates the
small presentation-only slice, ``notification`` demonstrates a Core-backed
state slice bound only to a narrow port, ``daemon`` demonstrates a manager-owning
slice that consumes its current-agent model/tool/preset/notification semantics
through the capability-native runtime port, ``plugin`` demonstrates a slice
whose only earned port is a detached read-only projection, ``vision``
demonstrates a slice that reads the live active provider through one
read-through port plus a setup-selected configuration snapshot, and ``web``
demonstrates a slice whose setup-composed typed value is granted to its own
declaration alone through ``extra_ports_for`` beside one narrow read-only
provider label. All are mounted only through the registrar's controlled host
path.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from lingtai.agent import Agent
from lingtai.services.vision import VisionService
from tests._service_helpers import make_gemini_mock_service


@pytest.fixture
def mcp_agent(tmp_path):
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="tool-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"mcp": {}},
        addons=["imap"],
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


@pytest.fixture
def daemon_agent(tmp_path):
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="daemon-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"daemon": {}},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


@pytest.fixture
def plugin_agent(tmp_path):
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="tool-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"plugin": {}},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


@pytest.fixture
def task_card_agent(tmp_path):
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="task-card-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"task_card": {}},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


def test_all_fifteen_official_families_mount_exactly_once_together(tmp_path):
    """The cumulative composition keeps every landed family and no duplicate."""
    from lingtai.kernel.tool_plugin import OFFICIAL_TOOL_PLUGIN_NAMES

    assert OFFICIAL_TOOL_PLUGIN_NAMES == (
        "mcp", "avatar", "context", "daemon", "email", "file", "plugin", "psyche",
        "notification", "shell", "soul", "system", "task_card", "vision", "web",
    )
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="all-fifteen-official-plugins",
        working_dir=tmp_path / "agent",
        capabilities={
            "mcp": {},
            "avatar": {},
            "context": {},
            "daemon": {},
            "file": {},
            "plugin": {},
            "notification": {},
            "shell": {"yolo": True},
            "task_card": {},
            "vision": {"vision_service": MagicMock(spec=VisionService)},
            "web": {},
        },
    )
    try:
        assert set(agent.official_tool_plugins) == set(OFFICIAL_TOOL_PLUGIN_NAMES)
        mounted_names = [schema.name for schema in agent._tool_schemas]
        for name in OFFICIAL_TOOL_PLUGIN_NAMES:
            assert mounted_names.count(name) == 1, name
            assert name in agent._tool_handlers
    finally:
        agent.stop(timeout=1.0)


def test_official_mcp_mount_uses_controlled_host_and_real_dispatch(mcp_agent):
    """Boot registration claims the declaration and preserves existing dispatch."""
    from lingtai.tools.mcp import DECLARATION

    assert DECLARATION.requires == ("workdir", "prompt_section")
    assert mcp_agent.official_tool_plugins["mcp"] is DECLARATION
    assert [schema.name for schema in mcp_agent._tool_schemas].count("mcp") == 1

    handler = mcp_agent._tool_handlers["mcp"]
    info = handler({"action": "info", "input": {}, "reasoning": "health"})
    assert info["status"] == "ok"
    assert info["registered"][0]["name"] == "imap"
    assert "mcp_manual" not in info

    manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
    assert manual["status"] == "ok"
    assert manual["mcp_manual"]
    assert manual["manual_path"].endswith("capabilities/mcp/SKILL.md")


def test_official_vision_mount_keeps_active_provider_and_packaged_manual(tmp_path):
    """The thirteenth declared slice binds only its narrow ports and stays a real tool."""
    from lingtai.adapters.tool_plugin_host import (
        AgentActiveProviderAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.tool_plugin import HostPortError, ToolPluginHost
    from lingtai.tools.vision import DECLARATION, VisionManager

    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="vision-tool-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"vision": {"vision_service": MagicMock(spec=VisionService)}},
    )
    try:
        assert DECLARATION.requires == ("workdir", "active_provider", "configuration")
        assert agent.official_tool_plugins["vision"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("vision") == 1

        handler = agent._tool_handlers["vision"]
        assert isinstance(handler, VisionManager)
        assert not hasattr(handler, "_agent")
        manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
        assert manual["status"] == "ok"
        assert manual["action"] == "manual"
        assert manual["manual"]
        assert manual["manual_path"].endswith("capabilities/vision/SKILL.md")

        # The standard table builds only Vision's live read-through provider
        # port. The setup snapshot is granted through ``extra_ports_for`` by
        # ``setup`` alone, so a bare standard grant fails loudly rather than
        # binding a half-configured Vision; MCP never sees the provider port.
        table = agent_host_ports(agent, "vision")
        assert isinstance(table["active_provider"], AgentActiveProviderAdapter)
        assert table["active_provider"].service is agent.service
        assert "configuration" not in table
        with pytest.raises(HostPortError):
            ToolPluginHost.grant(DECLARATION, table)
        assert "active_provider" not in agent_host_ports(agent, "mcp")
    finally:
        agent.stop(timeout=1.0)


def test_official_web_mount_keeps_provider_identity_and_packaged_manual(tmp_path):
    """The fourteenth declared slice binds only its narrow ports and stays a real tool."""
    from lingtai.adapters.tool_plugin_host import (
        AgentProviderIdentityAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.tool_plugin import HostPortError, ToolPluginHost
    from lingtai.tools.web_search import DECLARATION, WebManager

    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="web-tool-plugin-declaration",
        working_dir=tmp_path / "agent",
        capabilities={"web": {}},
    )
    try:
        assert DECLARATION.requires == ("workdir", "web_runtime", "provider_identity")
        assert DECLARATION.public_actions == ("search", "browse", "settings", "manual")
        assert agent.official_tool_plugins["web"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("web") == 1

        handler = agent._tool_handlers["web"]
        manager = handler.__self__
        assert isinstance(manager, WebManager)
        assert not hasattr(manager, "_agent")
        manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
        assert manual["status"] == "ok"
        assert manual["action"] == "manual"
        assert manual["manual"]
        assert manual["manual_path"].endswith("capabilities/web/SKILL.md")

        # The standard table builds only Web's narrow read-through provider
        # label. The typed composition is granted through ``extra_ports_for``
        # by ``setup`` alone, so a bare standard grant fails loudly rather than
        # binding a half-composed Web; MCP and Vision never see the label.
        table = agent_host_ports(agent, "web")
        assert isinstance(table["provider_identity"], AgentProviderIdentityAdapter)
        assert table["provider_identity"].provider == agent.service.provider
        assert not hasattr(table["provider_identity"], "service")
        assert "web_runtime" not in table
        assert "active_provider" not in table
        with pytest.raises(HostPortError):
            ToolPluginHost.grant(DECLARATION, table)
        assert "provider_identity" not in agent_host_ports(agent, "mcp")
        assert "provider_identity" not in agent_host_ports(agent, "vision")
    finally:
        agent.stop(timeout=1.0)


def test_official_task_card_mount_keeps_the_current_agent_lifecycle(task_card_agent):
    """The twelfth declared slice mounts, retains its manager, and serves its package manual."""
    from lingtai.tools.task_card import DECLARATION, TaskCardManager

    assert DECLARATION.requires == (
        "workdir", "shutdown", "task_card_lifecycle", "task_card_notifications"
    )
    assert task_card_agent.official_tool_plugins["task_card"] is DECLARATION
    assert [schema.name for schema in task_card_agent._tool_schemas].count("task_card") == 1

    manager = task_card_agent._task_card_manager
    assert isinstance(manager, TaskCardManager)
    assert not hasattr(manager, "_agent")
    handler = task_card_agent._tool_handlers["task_card"]
    assert handler.__self__ is manager
    settings = handler({"action": "settings", "input": {}, "reasoning": "inspect"})
    assert [row["key"] for row in settings["settings"]] == [
        "interval_s", "timeout_s", "max_refreshes", "reminder_turns", "max_body_chars"
    ]
    assert not manager._config_path.exists()
    manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
    assert manual["status"] == "ok"
    assert manual["content"][0]["text"]
    assert manual["structuredContent"]["manual_path"].endswith(
        "capabilities/task_card/SKILL.md"
    )
    task_card_agent.start()
    prompt = task_card_agent._build_system_prompt()
    batches = task_card_agent._build_system_prompt_batches()
    schemas = task_card_agent._build_tool_schemas()
    assert isinstance(prompt, str) and prompt
    assert batches and all(isinstance(batch, str) for batch in batches)
    assert "task_card" in {schema.name for schema in schemas}


def test_official_task_card_manager_holds_only_the_native_notification_operations(
    task_card_agent,
):
    """The bound manager sees five closed notification operations and no publisher.

    The granted ``task_card_notifications`` port is the production
    ``AgentTaskCardNotificationsAdapter``; a foreign source/channel/field cannot
    be supplied to any of its operations, and the manager keeps only the
    family's typed view over it.
    """
    from lingtai.adapters.tool_plugin_host import (
        AgentTaskCardNotificationsAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.tool_plugin import ToolPluginHost
    from lingtai.tools.task_card import DECLARATION, TaskCardNotificationsAdapter

    table = agent_host_ports(task_card_agent, "task_card")
    host = ToolPluginHost.grant(DECLARATION, table)
    assert host.granted == DECLARATION.requires
    native = host.task_card_notifications
    assert isinstance(native, AgentTaskCardNotificationsAdapter)
    assert sorted(name for name in dir(native) if not name.startswith("_")) == [
        "clear_reminder", "publish_error", "publish_limit", "publish_recovered",
        "submit_reminder",
    ]
    assert not hasattr(native, "enqueue_system_notification")

    for foreign in ({"source": "foreign"}, {"channel": "foreign"}, {"extra": {"x": 1}}):
        with pytest.raises(TypeError):
            native.publish_error(
                watch_id="tc", body="b", code="c", retryable=True, idempotency_key="k",
                **foreign,
            )
        with pytest.raises(TypeError):
            native.publish_recovered(watch_id="tc", body="b", idempotency_key="k", **foreign)
        with pytest.raises(TypeError):
            native.publish_limit(
                watch_id="tc", body="b", idempotency_key="k", used=1, max_refreshes=1,
                **foreign,
            )

    manager = task_card_agent._task_card_manager
    view = manager._host.task_card_notifications
    assert isinstance(view, TaskCardNotificationsAdapter)
    assert not hasattr(view, "enqueue_system_notification")
    assert not hasattr(manager._host, "task_card_lifecycle")


def test_official_soul_mount_preserves_real_flow_and_packaged_manual(mcp_agent):
    """Soul uses only its earned self/runtime port, without a second public root."""
    from lingtai.tools.soul import DECLARATION

    assert DECLARATION.public_actions == (
        "inquiry", "flow", "config", "voice", "dismiss", "settings", "manual",
    )
    assert DECLARATION.settings is True
    assert DECLARATION.requires == ("workdir", "soul_runtime")
    assert mcp_agent.official_tool_plugins["soul"] is DECLARATION
    assert [schema.name for schema in mcp_agent._tool_schemas].count("soul") == 1

    handler = mcp_agent._tool_handlers["soul"]
    disabled = handler({"action": "flow", "input": {}, "reasoning": "health"})
    assert disabled["status"] == "disabled"
    assert disabled["enabled"] is False

    manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
    assert manual["status"] == "ok"
    assert manual["manual"]
    assert manual["manual_path"].endswith("capabilities/soul-manual/SKILL.md")


def test_official_notification_mount_preserves_core_state_and_packaged_manual(
    mcp_agent, monkeypatch
):
    """The Notification declaration reaches real Core state only through its port."""
    from lingtai.kernel.notifications import submit
    from lingtai.tools.notification import DECLARATION

    monkeypatch.delenv("LINGTAI_NOTIFICATION_MAX_CHARS", raising=False)
    monkeypatch.delenv("LINGTAI_NOTIFICATION_DELAY_MAX_SECONDS", raising=False)
    assert DECLARATION.public_actions == (
        "check", "dismiss_channel", "dismiss_event", "dismiss_ref", "add",
        "drop", "edit", "list", "delay", "settings", "manual",
    )
    assert DECLARATION.requires == ("workdir", "notification_state")
    assert mcp_agent.official_tool_plugins["notification"] is DECLARATION
    assert [schema.name for schema in mcp_agent._tool_schemas].count("notification") == 1

    handler = mcp_agent._tool_handlers["notification"]
    settings_path = mcp_agent.working_dir / "settings" / "system.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        '{"schema_version":2,"notification_max_chars":3000}',
        encoding="utf-8",
    )
    settings = handler(
        {"action": "settings", "input": {}, "reasoning": "effective values"}
    )
    assert [row["key"] for row in settings["settings"]] == [
        "notification.max_chars",
        "notification.delay_max_seconds",
    ]
    assert [row["current"] for row in settings["settings"]] == [3_000, 600]
    assert mcp_agent._build_system_prompt()

    check = handler({"action": "check", "input": {}, "reasoning": "probe"})
    assert check["_notification_placeholder"] is True

    manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
    assert manual["status"] == "ok"
    assert manual["notification_manual"]
    assert manual["manual_path"].endswith("capabilities/notification/SKILL.md")

    submit(mcp_agent, "system", data={"events": []}, header="dismiss me")
    dismissed = handler(
        {
            "action": "dismiss_channel",
            "input": {"channel": "system", "force": True, "reason": None},
            "reasoning": "clear the mirror only",
        }
    )
    assert dismissed == {
        "status": "ok",
        "channel": "system",
        "cleared": True,
        "forced": True,
    }
    assert not (mcp_agent.working_dir / ".notification" / "system.json").exists()


@pytest.mark.parametrize(
    "construction_kwargs",
    [
        pytest.param({"capabilities": {"notification": None}}, id="capabilities-null"),
        pytest.param({"capabilities": {}, "disable": ["notification"]}, id="disable-list"),
    ],
)
def test_notification_is_mounted_once_on_live_construction_despite_opt_out(
    tmp_path, construction_kwargs
):
    """Both capability-shaped opt-outs preserve one live official Notification mount."""
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="notification-always-on-construction",
        working_dir=tmp_path / "agent",
        **construction_kwargs,
    )
    try:
        from lingtai.tools.notification import DECLARATION

        assert agent.official_tool_plugins["notification"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("notification") == 1
        assert list(name for name in agent._tool_handlers if name == "notification") == [
            "notification"
        ]
        assert agent._tool_handlers["notification"](
            {"action": "check", "input": {}, "reasoning": "live construction"}
        )["_notification_placeholder"] is True
    finally:
        agent.stop(timeout=1.0)


@pytest.mark.parametrize(
    "manifest_overrides",
    [
        pytest.param({"capabilities": {"notification": None}}, id="refresh-capabilities-null"),
        pytest.param({"capabilities": {}, "disable": ["notification"]}, id="refresh-disable-list"),
    ],
)
def test_notification_is_remounted_once_on_live_refresh_despite_opt_out(
    tmp_path, manifest_overrides
):
    """Refresh clears/rebuilds the surface but cannot remove the official mount."""
    workdir = tmp_path / "agent"
    agent = Agent(
        service=make_gemini_mock_service(),
        agent_name="notification-always-on-refresh",
        working_dir=workdir,
        capabilities={},
    )
    try:
        manifest = {
            "agent_name": "notification-always-on-refresh",
            "language": "en",
            "llm": {
                "provider": "gemini",
                "model": "gemini-test",
                "api_key": "test-key",
                "base_url": None,
            },
            "capabilities": {},
            "soul": {"delay": 60},
            "stamina": 3600,
            "context_limit": None,
            "molt_pressure": 0.8,
            "molt_prompt": "",
            "max_turns": 100,
            "admin": {"karma": True},
            "streaming": False,
            **manifest_overrides,
        }
        (workdir / "init.json").write_text(
            json.dumps(
                {
                    "manifest": manifest,
                    "principle": "",
                    "covenant": "",
                    "pad": "",
                    "lingtai": "",
                }
            ),
            encoding="utf-8",
        )

        agent._setup_from_init()

        from lingtai.tools.notification import DECLARATION

        assert agent.official_tool_plugins["notification"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("notification") == 1
        assert list(name for name in agent._tool_handlers if name == "notification") == [
            "notification"
        ]
        assert agent._tool_handlers["notification"](
            {"action": "check", "input": {}, "reasoning": "live refresh"}
        )["_notification_placeholder"] is True
    finally:
        agent.stop(timeout=1.0)



def test_official_daemon_mount_uses_runtime_port_and_preserves_dispatch(daemon_agent):
    """Daemon keeps one real manager/tool surface without binding to an Agent.

    ``list`` exercises the unchanged manager's durable-state path (no process is
    spawned), and ``manual`` proves that the declaration's installed manual is
    the registered reserved child rather than the legacy flat manager branch.
    """
    from lingtai.tools.daemon import DECLARATION, DaemonManager

    assert DECLARATION.requires == ("workdir", "daemon_runtime")
    assert daemon_agent.official_tool_plugins["daemon"] is DECLARATION
    assert [schema.name for schema in daemon_agent._tool_schemas].count("daemon") == 1

    manager = daemon_agent._capability_managers["daemon"]
    assert isinstance(manager, DaemonManager)
    assert not hasattr(manager, "_agent")
    assert manager._runtime.service is daemon_agent.service

    handler = daemon_agent._tool_handlers["daemon"]
    listed = handler(
        {
            "action": "list",
            "input": {
                "contains": None,
                "status": None,
                "include_done": None,
                "last": None,
            },
            "reasoning": "inspect daemon state",
        }
    )
    assert listed["emanations"] == []
    assert listed["history_included"] is True

    manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
    assert manual["status"] == "ok"
    assert manual["structuredContent"]["manual_path"].endswith(
        "capabilities/daemon/SKILL.md"
    )
    assert manual["content"][0]["text"]


def test_official_daemon_manager_reads_replaced_notification_route_for_retryable_terminal_state(
    daemon_agent, monkeypatch,
):
    """The official Daemon binding must not retain a stale notification callback.

    A terminal publish runs through the manager produced by the declared-host
    registration. Replacing its host notification route after binding with a
    failure must make publication fail; once the durable claim is cleared by the
    terminal caller, the run can claim the same terminal notification again.
    """
    from lingtai.tools.daemon import DaemonManager
    from lingtai.tools.daemon.run_dir import DaemonRunDir

    manager = daemon_agent._capability_managers["daemon"]
    assert isinstance(manager, DaemonManager)
    assert daemon_agent.official_tool_plugins["daemon"].name == "daemon"

    run_dir = DaemonRunDir(
        parent_working_dir=daemon_agent.working_dir,
        handle="em-live-route",
        run_id="em-live-route",
        task="exercise live daemon notification route",
        tools=[],
        model="test-model",
        max_turns=1,
        timeout_s=1.0,
        parent_addr="daemon-plugin-declaration",
        parent_pid=0,
        system_prompt="",
    )
    run_dir.mark_done("terminal result")
    idempotency_key = run_dir.claim_terminal_notification("done")
    assert idempotency_key is not None

    def replaced_route(**_kwargs):
        raise OSError("replacement notification route failed")

    monkeypatch.setattr(daemon_agent, "_enqueue_system_notification", replaced_route)
    assert manager._publish_daemon_notification(
        "em-live-route",
        status="done",
        text="terminal result",
        run_dir=run_dir,
        idempotency_key=idempotency_key,
    ) is False

    run_dir.clear_terminal_notification_claim()
    state = run_dir.state_snapshot()
    assert state["terminal_notified"] is False
    assert state["terminal_notification_claim"] is None
    assert run_dir.claim_terminal_notification("done") == idempotency_key


def test_official_plugin_mount_uses_only_catalog_state_and_real_dispatch(plugin_agent):
    """Plugin's declaration mounts through the controlled host path unchanged."""
    from lingtai.tools.plugin import DECLARATION

    assert DECLARATION.requires == ("workdir", "prompt_section", "plugin_catalog")
    assert DECLARATION.public_actions == ("info", "settings", "manual")
    assert plugin_agent.official_tool_plugins["plugin"] is DECLARATION
    assert [schema.name for schema in plugin_agent._tool_schemas].count("plugin") == 1

    handler = plugin_agent._tool_handlers["plugin"]
    info = handler({"action": "info", "input": {}, "reasoning": "health"})
    assert info["status"] == "ok"
    assert info["registered"] == []
    assert info["discovered"] == []
    assert "plugin_manual" not in info

    manual = handler({"action": "manual", "input": {}, "reasoning": "guidance"})
    assert manual["status"] == "ok"
    assert manual["plugin_manual"]
    assert manual["manual_path"].endswith("capabilities/plugin/SKILL.md")


def test_standard_port_table_grants_each_declaration_only_its_requires(plugin_agent):
    """A standard-table port is reachable only by a declaration that names it.

    ``plugin_catalog`` and ``avatar_parent`` are built for every declaration in
    ``agent_host_ports``. This proves that placement in the full table is not a
    grant: MCP, which requires neither, cannot reach either one through its
    least-privilege facade, while Plugin reaches exactly its three.
    """
    from lingtai.adapters.tool_plugin_host import (
        AgentPluginCatalogAdapter,
        agent_host_ports,
    )
    from lingtai.kernel.tool_plugin import PluginCatalogState, ToolPluginHost
    from lingtai.tools.mcp import DECLARATION as MCP_DECLARATION
    from lingtai.tools.plugin import DECLARATION as PLUGIN_DECLARATION

    table = agent_host_ports(plugin_agent, "plugin")
    assert isinstance(table["plugin_catalog"], AgentPluginCatalogAdapter)

    plugin_host = ToolPluginHost.grant(PLUGIN_DECLARATION, table)
    assert plugin_host.granted == ("workdir", "prompt_section", "plugin_catalog")
    assert isinstance(plugin_host.plugin_catalog.read_state(), PluginCatalogState)

    mcp_host = ToolPluginHost.grant(
        MCP_DECLARATION, agent_host_ports(plugin_agent, "mcp")
    )
    assert mcp_host.granted == ("workdir", "prompt_section")
    with pytest.raises(AttributeError):
        mcp_host.plugin_catalog
    with pytest.raises(AttributeError):
        mcp_host.avatar_parent


# ---------------------------------------------------------------------------
# Kernel registrar: whole-batch name preflight, order, and name-only atomicity
# (TP002). A recording mount keeps these assertions at the owning kernel seam.
# ---------------------------------------------------------------------------


def _kernel_declaration(name, *, calls, requires=("workdir",)):
    """Return a minimal valid declaration whose bind and activate record calls."""
    from lingtai.kernel.tool_plugin import BoundToolPlugin, ToolPluginDeclaration

    def binder(host):
        calls.append(("bind", name))
        return BoundToolPlugin(
            name=name,
            schema={"properties": {"action": {"enum": ["ping", "manual"]}}},
            handler=lambda _args: {"status": "ok"},
            description=f"{name} kernel test plugin",
            activate=lambda: calls.append(("activate", name)),
        )

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    return ToolPluginDeclaration(
        name=name,
        actions=("ping",),
        input_schemas={"ping": empty},
        manual_input_schema=empty,
        manual=f"{name}-manual",
        description=f"{name} kernel test declaration",
        binder=binder,
        requires=requires,
    )


class _RecordingMount:
    def __init__(self, calls):
        self.calls = calls
        self.transactions = []

    def mount_tool(self, transaction):
        self.calls.append(("mount", transaction.declaration.name))
        self.transactions.append(transaction)


def test_registrar_refuses_unreserved_duplicate_and_claimed_names_before_any_bind_or_mount():
    """The whole batch is preflighted before bind, activation, or mount runs."""
    from lingtai.kernel.tool_plugin import (
        DuplicateToolPluginNameError,
        UnreservedToolPluginNameError,
        register_official_tool_plugins,
    )

    calls: list[tuple[str, str]] = []
    mount = _RecordingMount(calls)
    ports = {"workdir": object()}
    live = _kernel_declaration("mcp", calls=calls)
    claimed = {"mcp": live}

    refused = [
        (
            [
                _kernel_declaration("avatar", calls=calls),
                _kernel_declaration("not_official", calls=calls),
            ],
            UnreservedToolPluginNameError,
        ),
        (
            [
                _kernel_declaration("avatar", calls=calls),
                _kernel_declaration("avatar", calls=calls),
            ],
            DuplicateToolPluginNameError,
        ),
        (
            [
                _kernel_declaration("avatar", calls=calls),
                _kernel_declaration("mcp", calls=calls),
            ],
            DuplicateToolPluginNameError,
        ),
    ]
    for batch, error in refused:
        with pytest.raises(error):
            register_official_tool_plugins(
                batch, ports_for=lambda _declaration: ports, mount=mount, claimed=claimed
            )
        assert calls == []
        assert mount.transactions == []
        assert claimed == {"mcp": live}


def test_registrar_binds_then_activates_then_mounts_and_scopes_atomicity_to_names():
    """Registration is ordered, same-object repeatable, and transactional only for names."""
    from lingtai.kernel.tool_plugin import (
        BoundToolPlugin,
        HostPortError,
        ToolPluginHost,
        _OfficialMountTransaction,
        register_official_tool_plugins,
    )

    calls: list[tuple[str, str]] = []
    mount = _RecordingMount(calls)
    ports = {"workdir": object()}
    mcp = _kernel_declaration("mcp", calls=calls)
    avatar = _kernel_declaration(
        "avatar", calls=calls, requires=("workdir", "avatar_parent")
    )
    claimed: dict[str, object] = {}

    bound = mcp.bind(ToolPluginHost.grant(mcp, ports))
    assert isinstance(bound, BoundToolPlugin)
    assert calls == [("bind", "mcp")]
    assert mount.transactions == []
    assert claimed == {}

    calls.clear()
    (mounted,) = register_official_tool_plugins(
        [mcp], ports_for=lambda _declaration: ports, mount=mount, claimed=claimed
    )
    assert calls == [("bind", "mcp"), ("activate", "mcp"), ("mount", "mcp")]
    assert claimed == {"mcp": mcp}
    transaction = mount.transactions[-1]
    assert transaction.declaration is mcp
    assert transaction.plugin is mounted

    calls.clear()
    register_official_tool_plugins(
        [mcp], ports_for=lambda _declaration: ports, mount=mount, claimed=claimed
    )
    assert calls == [("bind", "mcp"), ("activate", "mcp"), ("mount", "mcp")]
    assert claimed == {"mcp": mcp}

    with pytest.raises(PermissionError):
        _OfficialMountTransaction(mcp, mounted)

    calls.clear()
    claimed.clear()
    with pytest.raises(HostPortError):
        register_official_tool_plugins(
            [mcp, avatar], ports_for=lambda _declaration: ports, mount=mount, claimed=claimed
        )
    assert calls == [("bind", "mcp"), ("activate", "mcp"), ("mount", "mcp")]
    assert claimed == {"mcp": mcp}
