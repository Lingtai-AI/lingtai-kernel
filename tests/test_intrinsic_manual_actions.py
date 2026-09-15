"""Focused coverage for built-in tools that expose installed manual skills."""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace

from lingtai.tools import daemon as daemon_tool
from lingtai.tools import email as email_tool
from lingtai.tools import context as context_tool
from lingtai.tools import system as system_tool
from lingtai.tools import vision as vision_tool
from lingtai.tools import web_search as web_tool
from lingtai.tools import bash as shell_tool
from lingtai.tools import task_card as task_card_tool
from tests.test_task_card_controller import _FakeAgent, _task_card_host

ROOT = Path(__file__).resolve().parents[1]


class _StubAgent:
    def __init__(self, working_dir: Path):
        self._working_dir = working_dir
        self.handlers: dict[str, object] = {}

    def add_tool(self, name: str, *, handler=None, **_kwargs) -> None:
        self.handlers[name] = handler


class _OfficialHostStub(_StubAgent):
    """The smallest controlled host that satisfies the strict official registrar.

    ``vision`` mounts only through ``register_agent_tool_plugins``, whose kernel
    registrar anchors, records, mounts, and claims through BaseAgent's narrow
    hooks. This stub reuses the real BaseAgent hook implementations over its
    own private maps (the same pattern as the production Daemon preset
    collector) and repeats the canonical declaration/bind identity checks in
    its one-use mount, so no registrar check is weakened. It adds no Agent
    surface beyond ``working_dir``, a ``service`` read (``None``: no active
    provider, so the default route is manual-only), and the stub ``add_tool``.
    """

    def __init__(self, working_dir: Path):
        super().__init__(working_dir)
        self.service = None
        self._official_tool_plugins: dict[str, object] = {}
        self._official_tool_declarations: dict[str, object] = {}
        self._official_tool_bindings: dict[str, object] = {}

    @property
    def working_dir(self) -> Path:
        return self._working_dir

    @property
    def official_tool_plugins(self):
        return MappingProxyType(self._official_tool_plugins)

    def _authorize_official_tool_declaration(self, declaration) -> None:
        from lingtai.kernel.base_agent import BaseAgent

        BaseAgent._authorize_official_tool_declaration(self, declaration)

    def _record_official_tool_binding(self, declaration, plugin) -> None:
        from lingtai.kernel.base_agent import BaseAgent

        BaseAgent._record_official_tool_binding(self, declaration, plugin)

    def _claim_official_tool(self, transaction) -> None:
        from lingtai.kernel.base_agent import BaseAgent

        BaseAgent._claim_official_tool(self, transaction)

    def _mount_official_tool(self, transaction) -> None:
        from lingtai.kernel.tool_plugin import (
            OFFICIAL_TOOL_PLUGIN_NAMES,
            _OfficialMountTransaction,
        )

        if not isinstance(transaction, _OfficialMountTransaction):
            raise PermissionError(
                "official tool mounting requires a registrar transaction"
            )
        declaration = transaction.declaration
        plugin = transaction.plugin
        name = declaration.name
        if (
            name not in OFFICIAL_TOOL_PLUGIN_NAMES
            or plugin.name != name
            or self._official_tool_declarations.get(name) is not declaration
            or self._official_tool_bindings.get(name) is not plugin
        ):
            raise PermissionError(
                "official mount transaction is not the canonical declaration/bind result"
            )
        live = self._official_tool_plugins.get(name)
        if live is not None and live is not declaration:
            raise PermissionError("official mount transaction is not for the live claim")
        transaction.consume()
        self.add_tool(name, handler=plugin.handler, schema=plugin.schema)
        transaction.mark_mounted(self)


def _install_manual(workdir: Path, skill_name: str) -> tuple[str, Path]:
    path = (
        workdir
        / ".library"
        / "intrinsic"
        / "capabilities"
        / skill_name
        / "SKILL.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    body = f"---\nname: {skill_name}\n---\n\n# {skill_name} sentinel\n"
    path.write_text(body, encoding="utf-8")
    return body, path


def test_manual_actions_return_their_installed_skills(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    expected = {
        skill: _install_manual(tmp_path, skill)
        for skill in (
            "shell",
            "daemon",
            "email",
            "context-manual",
            "system-manual",
            "web",
            "vision",
            "task_card",
        )
    }

    shell_manager = shell_tool.ShellManager.__new__(shell_tool.ShellManager)
    shell_manager._agent = agent
    daemon_manager = daemon_tool.DaemonManager.__new__(daemon_tool.DaemonManager)
    daemon_manager._workdir = SimpleNamespace(path=tmp_path)
    # ``web`` is a declared official family too: its ``setup`` composes the
    # typed WebComposition, grants it through the strict kernel registrar, and
    # returns the manager that registrar bound and mounted.
    web_host = _OfficialHostStub(tmp_path)
    web_manager = web_tool.setup(web_host)
    assert web_host.official_tool_plugins["web"] is web_tool.DECLARATION
    assert web_host.handlers["web"] == web_manager.handle
    # ``vision`` is a declared official family: ``setup`` runs through the
    # strict kernel registrar, so it takes the controlled official host rather
    # than the bare stub. The registrar must have claimed and mounted the one
    # canonical declaration/manager before the manual assertion below runs.
    vision_host = _OfficialHostStub(tmp_path)
    vision_manager = vision_tool.setup(vision_host)
    assert vision_host.official_tool_plugins["vision"] is vision_tool.DECLARATION
    assert vision_host.handlers["vision"] is vision_manager
    task_card_manager = task_card_tool.TaskCardManager(_task_card_host(_FakeAgent(tmp_path)))

    # ``shell`` is a migrated LTP v2 family: its ``manual`` is the reserved
    # family child dispatched through the registered envelope handler, not an
    # engine branch. Build the same dispatcher ``setup`` registers.
    shell_dispatcher = shell_tool.ShellFamilyDispatcher(shell_manager, agent)

    calls = {
        "shell": (
            "shell",
            lambda: shell_dispatcher.handle(
                {"action": "manual", "input": {}, "reasoning": "load shell guidance"}
            ),
        ),
        "daemon": ("daemon", lambda: daemon_manager.handle({"action": "manual"})),
        # ``email`` and ``context`` are migrated LTP v2 families: ``manual`` is
        # the reserved family child, called through the closed action/input
        # envelope.
        "email": ("email", lambda: email_tool.handle(agent, {"action": "manual", "input": {}})),
        "context": ("context-manual", lambda: context_tool.handle(agent, {"action": "manual", "input": {}})),
        "system": ("system-manual", lambda: system_tool.handle(agent, {"action": "manual", "input": {}})),
        "web": ("web", lambda: web_manager.handle({"action": "manual", "input": {}})),
        "vision": ("vision", lambda: vision_manager.handle({"action": "manual", "input": {}})),
        "task_card": ("task_card", lambda: task_card_manager.handle(
            {"action": "manual", "input": {}, "reasoning": "load task card guidance"}
        )),
    }

    for tool_name, (skill_name, call) in calls.items():
        body, path = expected[skill_name]
        result = call()
        if tool_name == "web":
            assert result["status"] == "ok"
            assert result["action"] == "manual"
            assert result["manual"] == body
            assert result["manual_path"] == str(path)
            assert isinstance(result["current_setting"], dict)
        elif tool_name == "vision":
            # vision's family-owned manual keeps its pre-migration
            # status/action/manual shape and adds the loader's manual_path.
            assert result == {
                "status": "ok",
                "action": "manual",
                "manual": body,
                "manual_path": str(path),
            }
        elif tool_name == "shell":
            # Migrated family: the reserved ``manual`` child's canonical
            # ManualTool result is returned verbatim (no double wrap) — full
            # body at content[0].text, host-local path in structuredContent.
            assert result["status"] == "ok"
            assert result["content"][0]["text"] == body
            assert result["structuredContent"]["manual_path"] == str(path)
        elif tool_name == "task_card":
            assert result["status"] == "ok"
            assert result["content"][0]["text"] == body
            assert result["structuredContent"]["manual_path"] == str(path)
        else:
            assert result == {
                "status": "ok",
                "manual": body,
                "manual_path": str(path),
            }, tool_name


def test_manual_schemas_keep_their_closed_roots() -> None:
    modules = (
        shell_tool,
        daemon_tool,
        email_tool,
        context_tool,
        system_tool,
        web_tool,
        vision_tool,
        task_card_tool,
    )
    for module in modules:
        schema = module.get_schema()
        action = schema["properties"]["action"]
        assert "manual" in action.get("enum", ()) or "manual" in action["description"]

    # context is an LTP v2 family, so it requires the full closed root exactly
    # as web does — not a pre-migration action-only root.
    assert context_tool.get_schema()["required"] == ["action", "input", "reasoning"]
    web_schema = web_tool.get_schema()
    assert web_schema["required"] == ["action", "input", "reasoning"]
    assert len(web_schema["properties"]["input"]["anyOf"]) == 4
    vision_schema = vision_tool.get_schema()
    assert vision_schema["required"] == ["action", "input", "reasoning"]
    # analyze / check / list / settings / manual — one branch per public action.
    assert len(vision_schema["properties"]["input"]["anyOf"]) == 5
    task_card_schema = task_card_tool.get_schema()
    assert task_card_schema["required"] == ["action", "input", "reasoning"]
    assert len(task_card_schema["properties"]["input"]["anyOf"]) == 7
    # ``shell`` opts into settings immediately before its manual.
    shell_schema = shell_tool.get_schema()
    assert shell_schema["required"] == ["action", "input", "reasoning"]
    assert len(shell_schema["properties"]["input"]["anyOf"]) == 5


def test_shipped_task_card_manuals_only_document_intrinsic_file_contract() -> None:
    manuals = {
        "intrinsic": ROOT / "src/lingtai/tools/task_card/manual/SKILL.md",
        "telegram": ROOT / "src/lingtai/mcp_servers/telegram/SKILL.md",
        "telegram_retained": ROOT / "src/lingtai/mcp_servers/telegram/task_card/SKILL.md",
    }

    forbidden_active_contracts = (
        "prints exactly one json object",
        "stdout is exactly one task card json object",
        "title` is a string",
        "lines` is an array",
        "footer` is a string",
        "_lingtai_telegram_task_card",
        "controller runs",
        "public telegram-owned `task_card`",
    )

    for name, path in manuals.items():
        body = path.read_text(encoding="utf-8")
        lowered = body.lower()
        assert "src/lingtai/tools/task_card" in body or "task_card" in lowered, name
        assert "taskcard/status" in body, name
        assert "taskcard/taskcard.md" in body, name
        assert "nonempty" in lowered, name
        actions = ("start", "inspect", "retry", "stop", "remove", "manual")
        if name == "intrinsic":
            actions = (*actions[:-1], "settings", "manual")
        for action in actions:
            assert action in lowered, (name, action)
        for forbidden in forbidden_active_contracts:
            assert forbidden not in lowered, (name, forbidden)

    telegram_body = manuals["telegram"].read_text(encoding="utf-8").lower()
    retained_body = manuals["telegram_retained"].read_text(encoding="utf-8").lower()
    assert "read-only" in telegram_body
    assert "read-only" in retained_body
    assert "retained-legacy" in retained_body

    protocol_body = (
        ROOT
        / "src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/reference/mcp-protocol.md"
    ).read_text(encoding="utf-8")
    normalized_protocol = " ".join(protocol_body.lower().split())
    assert "telegram has no hidden `task_card` route" in normalized_protocol
    assert "intrinsic capability owns that tool-specific contract" in normalized_protocol

    base_agent_body = (ROOT / "src/lingtai/kernel/base_agent/__init__.py").read_text(
        encoding="utf-8"
    )
    lifecycle_body = (ROOT / "src/lingtai/kernel/base_agent/lifecycle.py").read_text(
        encoding="utf-8"
    )
    manager_body = (ROOT / "src/lingtai/mcp_servers/telegram/manager.py").read_text(
        encoding="utf-8"
    )
    assert "Retained legacy Telegram Task Card turn-local route bookkeeping" in base_agent_body
    assert "intrinsic ``task_card`` producer does not consume it" in base_agent_body
    assert "Maintain retained legacy Telegram route-capture bookkeeping" in lifecycle_body
    assert "does not consume this context" in lifecycle_body
    assert "retained legacy private-" in manager_body
    assert "Render retained legacy programmable-card JSON for compatibility tests" in manager_body
    assert "current public intrinsic instead emits a full text/Markdown" in manager_body


def test_missing_installed_manual_degrades_without_side_effects(tmp_path: Path) -> None:
    agent = _StubAgent(tmp_path)
    expected_path = (
        tmp_path
        / ".library"
        / "intrinsic"
        / "capabilities"
        / "system-manual"
        / "SKILL.md"
    )

    assert system_tool.handle(agent, {"action": "manual", "input": {}}) == {
        "status": "degraded",
        "manual": "",
        "manual_path": str(expected_path),
        "error": (
            "system-manual manual missing — initializer may have failed or "
            "capability not installed correctly"
        ),
    }
    assert not (tmp_path / ".library").exists()
