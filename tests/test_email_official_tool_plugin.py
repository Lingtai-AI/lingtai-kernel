"""Focused real-Agent proof for Email's declared host-plugin recut."""
from __future__ import annotations

import json

import pytest

from lingtai.agent import Agent
from lingtai.adapters.posix.mail import PosixFilesystemMailAdapter
from tests._service_helpers import make_mock_llm_service


@pytest.fixture
def email_agent(tmp_path):
    workdir = tmp_path / "agent"
    mail_service = PosixFilesystemMailAdapter(
        workdir,
        pseudo_agent_subscriptions=["../private-email-settings-marker"],
    )
    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="email-official-plugin",
        working_dir=workdir,
        mail_service=mail_service,
        capabilities={},
    )
    try:
        yield agent
    finally:
        agent.stop(timeout=1.0)


def _assert_no_email_capability_row(agent):
    """Check both the live manifest view and the persisted `.agent.json` row."""
    live_rows = agent._build_manifest().get("capabilities", [])
    assert "email" not in {name for name, _ in live_rows}
    persisted = json.loads(
        (agent.working_dir / ".agent.json").read_text(encoding="utf-8")
    )
    persisted_rows = persisted.get("capabilities", [])
    assert "email" not in {name for name, _ in persisted_rows}


def _write_refresh_init(workdir, *, capabilities: dict, disable: list[str] | None) -> None:
    """Supply the minimum persisted config needed to exercise real refresh."""
    init_data = {
        "manifest": {
            "agent_name": "email-opt-out",
            "language": "en",
            "llm": {
                "provider": "anthropic",
                "model": "claude-test",
                "api_key": "test-key",
                "base_url": None,
            },
            "capabilities": capabilities,
            "disable": disable or [],
            "soul": {"delay": 60},
            "context_limit": None,
            "admin": {"karma": True},
            "streaming": False,
        },
        "principle": "",
        "covenant": "",
        "pad": "",
        "lingtai": "",
        "soul": "",
    }
    (workdir / "init.json").write_text(json.dumps(init_data), encoding="utf-8")


def test_email_runtime_port_is_domain_specific_and_rejects_foreign_action():
    """The production port invokes only the live Email manager exactly once."""
    from lingtai.adapters.tool_plugin_host import AgentEmailRuntimeAdapter
    from lingtai.tools.email import EmailRuntimePort, EmailRuntimeRequest

    class Manager:
        def __init__(self):
            self.calls: list[dict] = []

        def handle(self, args: dict) -> dict:
            self.calls.append(args)
            return {"status": "ok"}

    manager = Manager()
    adapter = AgentEmailRuntimeAdapter(lambda: manager)
    assert "handle_email" in EmailRuntimePort.__dict__
    assert not hasattr(adapter, "_agent")
    assert not hasattr(adapter, "dispatch")
    assert adapter.handle_email(
        EmailRuntimeRequest("check", {"folder": "inbox"})
    ) == {"status": "ok"}
    assert manager.calls == [{"action": "check", "folder": "inbox"}]

    with pytest.raises(ValueError, match="unsupported Email runtime action"):
        adapter.handle_email(EmailRuntimeRequest("mcp", {}))
    assert manager.calls == [{"action": "check", "folder": "inbox"}]
    with pytest.raises(RuntimeError, match="snapshot is unavailable"):
        adapter.read_pseudo_agent_subscriptions()


def test_email_bound_family_normalizes_before_typed_runtime_and_preserves_results(
    email_agent, tmp_path
):
    """The final-port-shaped family boundary strips nulls before the runtime call."""
    from types import SimpleNamespace

    import lingtai.tools.email as email_module
    from lingtai.tools.email import EmailRuntimeRequest

    captured: list[EmailRuntimeRequest] = []

    class CapturingEmailRuntime:
        """Final ``handle_email`` shape, backed directly by the real manager."""

        def handle_email(self, request: EmailRuntimeRequest) -> dict:
            captured.append(request)
            # This is deliberately a direct manager call, not the retained
            # intrinsic round trip. It proves the typed request is sufficient
            # to preserve the manager's exact action result contracts.
            return email_agent._email_manager.handle(
                {"action": request.action, **dict(request.input)}
            )

    runtime = CapturingEmailRuntime()
    host = SimpleNamespace(
        email_runtime=runtime,
        workdir=tmp_path / "manual-workdir",
    )
    family = email_module._build_bound_family(host)

    check_input = {
        "folder": None,
        "n": None,
        "filter": {
            "sort": None,
            "from": None,
            "subject": None,
            "contains": None,
            "after": None,
            "before": None,
            "unread_only": None,
            "has_attachments": None,
            "truncate": None,
        },
    }
    assert family.handle({"action": "check", "input": check_input}) == {
        "status": "ok",
        "total": 0,
        "showing": 0,
        "emails": [],
    }
    assert captured == [EmailRuntimeRequest("check", {"filter": {}})]

    assert family.handle(
        {
            "action": "add_contact",
            "input": {"address": "peer", "name": "Peer Name", "note": "initial"},
        }
    ) == {
        "status": "added",
        "contact": {"address": "peer", "name": "Peer Name", "note": "initial"},
    }
    assert family.handle(
        {
            "action": "edit_contact",
            "input": {"address": "peer", "name": None, "note": "updated"},
        }
    ) == {
        "status": "updated",
        "contact": {"address": "peer", "name": "Peer Name", "note": "updated"},
    }
    assert captured[-1] == EmailRuntimeRequest(
        "edit_contact", {"address": "peer", "note": "updated"}
    )


def test_official_email_mount_keeps_real_agent_runtime_and_package_manual(email_agent):
    """The official handler uses the existing manager, not a synthetic stand-in."""
    from lingtai.tools.email import DECLARATION
    from lingtai.tools.email._family_schema import ACTION_ORDER

    assert DECLARATION.actions == ACTION_ORDER[:-1]
    assert DECLARATION.requires == ("workdir", "email_runtime")
    assert email_agent.official_tool_plugins["email"] is DECLARATION
    # The official intrinsic is not a dynamic capability, so neither runtime
    # bookkeeping nor the persisted manifest grows an Email capability row.
    assert "email" not in {name for name, _ in email_agent._capabilities}
    _assert_no_email_capability_row(email_agent)
    # The kernel retains a transport shim for inbound hooks, while the
    # model-facing handler is mounted only by the official registrar.
    assert "email" in email_agent._intrinsics
    assert "email" in email_agent._intrinsic_modules
    assert email_agent._intrinsics["email"] is not email_agent._tool_handlers["email"]
    assert [schema.name for schema in email_agent._tool_schemas].count("email") == 1
    assert [schema.name for schema in email_agent._build_tool_schemas()].count("email") == 1

    handler = email_agent._tool_handlers["email"]
    assert handler({"action": "check", "input": {}, "reasoning": "inspect"}) == {
        "status": "ok", "total": 0, "showing": 0, "emails": []
    }

    settings = handler(
        {"action": "settings", "input": {}, "reasoning": "inventory policy"}
    )
    pseudo_row = settings["settings"][-1]
    assert pseudo_row == {
        "key": "manifest.pseudo_agent_subscriptions",
        "current": "<redacted>",
        "default": "<redacted>",
        "configurable": True,
        "comment": "email-manual#pseudo-agent-subscriptions",
    }
    assert "private-email-settings-marker" not in repr(settings)

    manual = handler({"action": "manual", "input": {}, "reasoning": "procedure"})
    assert manual["status"] == "ok"
    assert "# Email Manual" in manual["manual"]
    assert manual["manual_path"].endswith("capabilities/email/SKILL.md")


def test_email_official_adapter_reads_replaced_manager_after_refresh(email_agent):
    """Refresh replaces EmailManager and a bound port reads the current one live."""
    _write_refresh_init(email_agent.working_dir, capabilities={}, disable=None)
    original_manager = email_agent._email_manager
    email_agent._setup_from_init()
    assert email_agent._email_manager is not original_manager

    class ReplacementManager:
        def __init__(self):
            self.calls: list[dict] = []

        def handle(self, args: dict) -> dict:
            self.calls.append(args)
            return {"status": "replacement"}

    replacement = ReplacementManager()
    handler = email_agent._tool_handlers["email"]
    email_agent._email_manager = replacement
    assert handler(
        {"action": "check", "input": {"folder": None}, "reasoning": "refresh"}
    ) == {"status": "replacement"}
    assert replacement.calls == [{"action": "check"}]


def test_email_official_boot_runs_exactly_once_per_construction_and_refresh(
    tmp_path, monkeypatch
):
    """Each official intrinsic boots once per phase; Email registers once.

    Counts every ``official_plugin`` module's ``boot`` (the seam
    ``BaseAgent._boot_official_intrinsics`` invokes) plus Email's registrar
    call and ``EmailManager`` construction, on construction and again on a
    real ``_setup_from_init`` refresh. A second boot repeats the
    manager/grant/bind/mount work while the final surface still looks
    singular, so only the counts expose it.
    """
    import lingtai.tools.email as email_module
    from lingtai.adapters import tool_plugin_host
    from lingtai.tools.email import DECLARATION
    from lingtai.tools.registry import INTRINSICS

    boots: dict[str, int] = {}
    email_work = {"register": 0, "manager": 0}

    def _counted_boot(name, real_boot):
        def boot(agent):
            boots[name] = boots.get(name, 0) + 1
            real_boot(agent)

        return boot

    official = [name for name, info in INTRINSICS.items() if info.get("official_plugin")]
    assert "email" in official
    for name in official:
        module = INTRINSICS[name]["module"]
        monkeypatch.setattr(module, "boot", _counted_boot(name, module.boot))

    real_register = tool_plugin_host.register_agent_tool_plugins

    def counted_register(agent, declarations, **kwargs):
        if any(declaration is DECLARATION for declaration in declarations):
            email_work["register"] += 1
        return real_register(agent, declarations, **kwargs)

    monkeypatch.setattr(tool_plugin_host, "register_agent_tool_plugins", counted_register)

    class CountedEmailManager(email_module.EmailManager):
        def __init__(self, agent):
            email_work["manager"] += 1
            super().__init__(agent)

    monkeypatch.setattr(email_module, "EmailManager", CountedEmailManager)

    workdir = tmp_path / "agent"
    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="email-exact-once",
        working_dir=workdir,
        capabilities={},
    )
    try:
        _write_refresh_init(workdir, capabilities={}, disable=None)
        for phase in ("construction", "refresh"):
            if phase == "refresh":
                boots.clear()
                email_work["register"] = email_work["manager"] = 0
                agent._setup_from_init()
            assert boots == {name: 1 for name in official}, phase
            assert email_work == {"register": 1, "manager": 1}, phase
            assert isinstance(agent._email_manager, CountedEmailManager)
            assert agent.official_tool_plugins["email"] is DECLARATION
            assert [schema.name for schema in agent._tool_schemas].count("email") == 1
    finally:
        agent.stop(timeout=1.0)


@pytest.mark.parametrize(
    ("capabilities", "disable"),
    [
        ({"email": None}, None),
        ({}, ["email"]),
    ],
    ids=["null-capability", "disable-list"],
)
def test_email_opt_out_forms_keep_one_official_surface_on_construction_and_refresh(
    tmp_path, capabilities, disable
):
    """Email's former mandatory surface cannot fall back to a generic intrinsic."""
    workdir = tmp_path / "agent"
    agent = Agent(
        service=make_mock_llm_service(),
        agent_name="email-opt-out",
        working_dir=workdir,
        capabilities=capabilities,
        disable=disable,
    )
    try:
        _write_refresh_init(workdir, capabilities=capabilities, disable=disable)

        from lingtai.tools.email import DECLARATION

        for phase in ("construction", "refresh"):
            if phase == "refresh":
                agent._setup_from_init()
            assert agent.official_tool_plugins["email"] is DECLARATION
            assert "email" not in {name for name, _ in agent._capabilities}
            _assert_no_email_capability_row(agent)
            assert [schema.name for schema in agent._tool_schemas].count("email") == 1
            assert [schema.name for schema in agent._build_tool_schemas()].count("email") == 1
            assert agent._tool_handlers["email"] is not agent._intrinsics["email"]
    finally:
        agent.stop(timeout=1.0)
