# tests/test_deep_refresh.py
"""Tests for deep refresh (full agent reconstruct from init.json)."""
from __future__ import annotations

import ast
import json
import os
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_resolve_env_fields_resolves_env_var(monkeypatch):
    """_resolve_env_fields replaces *_env keys with env var values."""
    from lingtai.kernel.config_resolve import _resolve_env_fields

    monkeypatch.setenv("TEST_SECRET", "hunter2")
    result = _resolve_env_fields({"api_key": None, "api_key_env": "TEST_SECRET"})
    assert result == {"api_key": "hunter2"}
    assert "api_key_env" not in result


def test_resolve_capabilities_resolves_env():
    """_resolve_capabilities applies _resolve_env_fields to each capability."""
    from lingtai.kernel.config_resolve import _resolve_capabilities

    caps = {"bash": {"policy_file": "p.json"}, "vision": {}}
    result = _resolve_capabilities(caps)
    assert result == {"bash": {"policy_file": "p.json"}, "vision": {}}


def _make_init(
    capabilities: dict | None = None,
    addons: list[str] | None = None,
    provider: str = "openai",
    model: str = "gpt-4o",
    covenant: str = "",
    principle: str = "",
    memory: str = "",
    base_prompt: str | None = None,
    substrate: str | None = None,
) -> dict:
    """Build a minimal valid init.json dict."""
    data = {
        "manifest": {
            "agent_name": "test-agent",
            "language": "en",
            "llm": {
                "provider": provider,
                "model": model,
                "api_key": "test-key",
                "base_url": None,
            },
            "capabilities": capabilities or {},
            "soul": {"delay": 60},
            "stamina": 3600,
            "context_limit": None,
            "molt_pressure": 0.8,
            "molt_prompt": "",
            "max_turns": 100,
            "admin": {"karma": True},
            "streaming": False,
        },
        "principle": principle,
        "covenant": covenant,
        "pad": memory,
        "lingtai": "",
        "soul": "",
    }
    if base_prompt is not None:
        data["base_prompt"] = base_prompt
    if substrate is not None:
        data["substrate"] = substrate
    if addons:
        data["addons"] = addons
    return data


def _make_agent(
    tmp_path: Path,
    init_data: dict | None = None,
    *,
    _from_init_boot: bool = False,
):
    """Create a bare Agent with a mock LLM service in a temp working dir."""
    from lingtai.agent import Agent
    from lingtai.kernel.config import AgentConfig

    init = dict(init_data or _make_init())
    owner = {"schema_version": 1}
    for key in (
        "base_prompt", "base_prompt_file", "covenant", "covenant_file",
        "comment", "comment_file",
    ):
        if key in init:
            owner[key] = init.pop(key)
    (tmp_path / "init.json").write_text(json.dumps(init))
    (tmp_path / "settings").mkdir(exist_ok=True)
    (tmp_path / "settings" / "psyche.json").write_text(json.dumps(owner))

    service = MagicMock()
    service.provider = "openai"
    service.model = "gpt-4o"
    service._base_url = None

    agent = Agent(
        service,
        agent_name="test-agent",
        working_dir=tmp_path,
        config=AgentConfig(),
        _from_init_boot=_from_init_boot,
    )
    return agent


def _packaged_procedures_file() -> str:
    """Full packaged procedures source, including skill-style frontmatter.

    This is what the `system/procedures.md` mirror keeps on disk."""
    from importlib.resources import files

    return files("lingtai.prompts").joinpath("procedures/procedures.md").read_text(
        encoding="utf-8"
    )


def _packaged_procedures() -> str:
    """Rendered procedures body (frontmatter stripped) — what reaches the prompt."""
    from lingtai.kernel._frontmatter import strip_frontmatter

    return strip_frontmatter(_packaged_procedures_file())


def _packaged_guidance() -> dict:
    """Assembled runtime-guidance dict from the skill-style Markdown catalog.

    The kernel no longer ships guidance.json; runtime guidance is authored as
    `lingtai/prompts/meta_guidance/catalog/INDEX.md` + `<id>.md` and assembled into this dict
    shape. The derived `system/guidance.json` mirror is serialized from it."""
    from lingtai.kernel.prompt_catalog import load_guidance_catalog

    return load_guidance_catalog()

def _events(tmp_path: Path, event_type: str) -> list[dict]:
    log_path = tmp_path / "logs" / "events.jsonl"
    if not log_path.is_file():
        return []
    events = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("type") == event_type:
            events.append(event)
    return events


def test_deep_refresh_loads_new_capability(tmp_path):
    """After editing init.json to add a capability, refresh picks it up."""
    agent = _make_agent(tmp_path, _make_init(capabilities={}))
    agent._sealed = True

    mock_interface = MagicMock()
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = mock_interface
    agent._session = mock_session

    new_init = _make_init(capabilities={"file": {}})
    (tmp_path / "init.json").write_text(json.dumps(new_init))

    agent._setup_from_init()

    cap_names = [name for name, _ in agent._capabilities]
    assert "file" in cap_names
    assert agent._sealed is True


def test_deep_refresh_unavailable_vision_is_manual_only(tmp_path):
    """Unavailable vision remains registered with manual-only guidance."""
    init = _make_init(capabilities={"vision": {"provider": "not-real"}})
    agent = _make_agent(tmp_path, init)

    agent._setup_from_init()

    manifest_registered = {
        name for name, _ in agent._build_manifest().get("capabilities", [])
    }
    assert agent.has_capability("vision") is True
    assert "vision" in agent._tool_handlers
    assert "vision" in manifest_registered
    assert "manual" in agent.get_capability("vision")._manual_reason


def test_deep_refresh_no_init_json_is_noop(tmp_path):
    """If init.json is missing, refresh is a no-op (no crash)."""
    agent = _make_agent(tmp_path)
    (tmp_path / "init.json").unlink()

    agent._sealed = True
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = MagicMock()
    agent._session = mock_session

    old_caps = list(agent._capabilities)
    agent._setup_from_init()
    assert agent._capabilities == old_caps


def test_deep_refresh_at_boot_no_history(tmp_path):
    """_setup_from_init works at boot time (no session, not sealed)."""
    init = _make_init(capabilities={"file": {}})
    agent = _make_agent(tmp_path, init)
    assert agent._sealed is False

    agent._setup_from_init()

    cap_names = [name for name, _ in agent._capabilities]
    assert "file" in cap_names
    assert agent._sealed is True


def test_cli_build_agent_uses_refresh(tmp_path):
    """CLI boot reads capabilities from init and covenant from Psyche."""
    from lingtai.cli import load_init, build_agent

    init = _make_init(
        capabilities={"file": {}},
        covenant="LEGACY INIT COVENANT MUST STAY INERT",
    )
    (tmp_path / "init.json").write_text(json.dumps(init))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "psyche.json").write_text(
        json.dumps({"schema_version": 1, "covenant": "Be helpful."})
    )

    data = load_init(tmp_path)
    agent = build_agent(data, tmp_path)

    # Capabilities remain init-owned.
    cap_names = [name for name, _ in agent._capabilities]
    assert "file" in cap_names

    # Covenant is Psyche-owned and the legacy init value is inert.
    covenant_content = agent._prompt_manager.read_section("covenant")
    assert covenant_content is not None
    assert "Be helpful" in covenant_content
    assert "LEGACY INIT COVENANT" not in covenant_content

    agent._workdir_lease.release()


def test_cli_build_agent_composes_wrapper_surface_once(tmp_path, monkeypatch):
    """CLI boot binds the configured declared MCP surface and loads MCPs once."""
    from lingtai.agent import Agent
    from lingtai.adapters import tool_plugin_host
    from lingtai.cli import build_agent, load_init

    init = _make_init()
    init["mcp"] = {
        "test-server": {
            "type": "stdio",
            "command": "test-server",
            "args": [],
        },
        "unregistered-server": {
            "type": "stdio",
            "command": "unregistered-server",
            "args": [],
        },
    }
    (tmp_path / "init.json").write_text(json.dumps(init))
    (tmp_path / "mcp_registry.jsonl").write_text(json.dumps({
        "name": "test-server",
        "summary": "test MCP",
        "transport": "stdio",
        "command": "test-server",
        "args": [],
        "source": "user",
    }) + "\n")

    mcp_registrations = 0
    manual_installs = 0
    mcp_loads = 0
    mcp_launches = 0
    launched_commands: list[str] = []
    register = tool_plugin_host.register_agent_tool_plugins
    install_manuals = Agent._install_intrinsic_manuals
    load_mcps = Agent._load_mcp_from_workdir

    def count_mcp_launch(self, command, args=None, env=None):
        nonlocal mcp_launches
        mcp_launches += 1
        launched_commands.append(command)
        return []

    def count_mcp_registration(agent, declarations, **kwargs):
        nonlocal mcp_registrations
        if any(declaration.name == "mcp" for declaration in declarations):
            mcp_registrations += 1
        return register(agent, declarations, **kwargs)

    def count_manual_install(self):
        nonlocal manual_installs
        manual_installs += 1
        return install_manuals(self)

    def count_mcp_load(self):
        nonlocal mcp_loads
        mcp_loads += 1
        return load_mcps(self)

    monkeypatch.setattr(
        tool_plugin_host, "register_agent_tool_plugins", count_mcp_registration
    )
    monkeypatch.setattr(Agent, "_install_intrinsic_manuals", count_manual_install)
    monkeypatch.setattr(Agent, "_load_mcp_from_workdir", count_mcp_load)
    monkeypatch.setattr(Agent, "connect_mcp", count_mcp_launch)

    agent = build_agent(load_init(tmp_path), tmp_path)
    try:
        assert mcp_registrations == 1
        assert manual_installs == 1
        assert mcp_loads == 1
        assert mcp_launches == 1
        assert launched_commands == ["test-server"]
        assert "mcp" in agent.official_tool_plugins
    finally:
        agent._workdir_lease.release()


def test_direct_agent_still_composes_default_mcp_surface(tmp_path):
    """The private CLI shell does not change public Agent construction."""
    agent = _make_agent(tmp_path)
    shell_dir = tmp_path / "private-shell"
    shell_dir.mkdir()
    shell = _make_agent(shell_dir, _from_init_boot=True)
    try:
        assert "mcp" in agent.official_tool_plugins
        assert "mcp" in {name for name, _kwargs in agent._capabilities}
        manual_path = tmp_path / ".library" / "intrinsic" / "capabilities" / "mcp" / "SKILL.md"
        assert manual_path.is_file()
        assert agent._mcp_init_specs == {}
        assert shell._capabilities == []
        assert shell._from_init_boot is False
    finally:
        agent._workdir_lease.release()
        shell._workdir_lease.release()


def test_cli_build_agent_context_window_ignores_legacy_init_and_uses_system_policy(tmp_path):
    from lingtai.cli import build_agent, load_init
    from lingtai.llm.service import CONSERVATIVE_CONTEXT_WINDOW

    init = _make_init()
    init["manifest"]["context_limit"] = 123_456
    (tmp_path / "init.json").write_text(json.dumps(init))

    agent = build_agent(load_init(tmp_path), tmp_path)
    assert agent.service._context_window == CONSERVATIVE_CONTEXT_WINDOW
    agent._workdir_lease.release()

    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "system.json").write_text(
        json.dumps({"schema_version": 2, "context_limit": 234_567})
    )
    (tmp_path / "init.json").write_text(json.dumps(init))

    agent = build_agent(load_init(tmp_path), tmp_path)
    assert agent.service._context_window == 234_567
    agent._workdir_lease.release()


def test_deep_refresh_invalid_init_keeps_old_config(tmp_path):
    """If init.json is invalid, refresh logs error and keeps old state."""
    init = _make_init(capabilities={"file": {}})
    agent = _make_agent(tmp_path, init)
    agent._setup_from_init()  # initial setup

    agent._sealed = True
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = MagicMock()
    agent._session = mock_session

    # Write invalid init.json
    (tmp_path / "init.json").write_text("not json")

    old_caps = list(agent._capabilities)
    agent._setup_from_init()

    # Old capabilities preserved (refresh was a no-op)
    assert agent._capabilities == old_caps


@pytest.mark.parametrize(
    "owner_failure",
    [
        "malformed",
        "oversized",
        "duplicate_key",
        "invalid_utf8",
        "symlink",
        "race",
        "read_failure",
    ],
)
def test_invalid_psyche_owner_aborts_live_refresh_before_teardown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    owner_failure: str,
) -> None:
    """Owner validation failure preserves the complete last-good live runtime."""
    from lingtai.tools.psyche import settings as psyche_settings
    from lingtai.tools.psyche.settings import PsycheSettingsError

    agent = _make_agent(
        tmp_path,
        _make_init(
            capabilities={"file": {}},
            base_prompt="APPLIED BASE",
            covenant="APPLIED COVENANT",
        ),
    )
    try:
        agent._setup_from_init()
        owner_path = tmp_path / "settings" / "psyche.json"

        if owner_failure == "malformed":
            owner_path.write_text("{", encoding="utf-8")
        elif owner_failure == "oversized":
            owner_path.write_bytes(b" " * (64 * 1024 + 1))
        elif owner_failure == "duplicate_key":
            owner_path.write_text(
                '{"schema_version":1,"comment":"a","comment":"b"}',
                encoding="utf-8",
            )
        elif owner_failure == "invalid_utf8":
            owner_path.write_bytes(b"\xff")
        elif owner_failure == "symlink":
            target = tmp_path / "owner-target.json"
            target.write_text('{"schema_version":1}', encoding="utf-8")
            owner_path.unlink()
            try:
                owner_path.symlink_to(target)
            except OSError:
                pytest.skip("the platform does not permit symlink creation")
        elif owner_failure == "race":
            real_lstat = Path.lstat
            calls = 0

            def unstable_lstat(candidate: Path):
                nonlocal calls
                result = real_lstat(candidate)
                if candidate == owner_path:
                    calls += 1
                    if calls == 2:
                        return SimpleNamespace(
                            st_dev=result.st_dev,
                            st_ino=result.st_ino,
                            st_mode=result.st_mode,
                            st_size=result.st_size,
                            st_mtime_ns=result.st_mtime_ns + 1,
                            st_ctime_ns=result.st_ctime_ns,
                        )
                return result

            monkeypatch.setattr(Path, "lstat", unstable_lstat)
        elif owner_failure == "read_failure":
            real_open = os.open

            def unreadable(candidate, flags, *args, **kwargs):
                if Path(candidate) == owner_path:
                    raise OSError("owner became unreadable")
                return real_open(candidate, flags, *args, **kwargs)

            monkeypatch.setattr(psyche_settings.os, "open", unreadable)

        client = MagicMock(name="last_good_mcp_client")
        agent._mcp_clients = [client]
        agent._mcp_clients_by_tool = {"last_good_tool": client}
        agent._mcp_tool_collisions = {"last_good_collision"}
        agent._mcp_tool_metadata = {"last_good_tool": {"title": "Last good"}}
        agent._mcp_init_specs = {"last_good": {"command": "kept"}}

        state = {
            "sealed": agent._sealed,
            "service": agent.service,
            "session": agent._session,
            "sections": deepcopy(agent._prompt_manager._sections),
            "base_prompt": agent._base_prompt,
            "handlers": dict(agent._tool_handlers),
            "schemas": list(agent._tool_schemas),
            "official_plugins": dict(agent._official_tool_plugins),
            "official_bindings": dict(agent._official_tool_bindings),
            "capabilities": list(agent._capabilities),
            "capability_managers": dict(agent._capability_managers),
            "intrinsics": dict(agent._intrinsics),
            "intrinsic_modules": dict(agent._intrinsic_modules),
            "post_molt_hooks": list(agent._post_molt_hooks),
            "snapshot": agent._psyche_settings_snapshot,
            "base_mirror": (tmp_path / "system/base_prompt.md").read_bytes(),
            "covenant_mirror": (tmp_path / "system/covenant.md").read_bytes(),
            "system_mirror": (tmp_path / "system/system.md").read_bytes(),
        }
        teardown_calls: list[str] = []
        monkeypatch.setattr(
            agent,
            "_cancel_soul_timer",
            lambda: teardown_calls.append("cancel_soul_timer"),
        )
        real_read = psyche_settings.read_resolved_prompt_inputs
        owner_reads: list[Path] = []

        def counted_read(root: Path):
            owner_reads.append(Path(root))
            return real_read(root)

        monkeypatch.setattr(
            psyche_settings,
            "read_resolved_prompt_inputs",
            counted_read,
        )

        with pytest.raises(PsycheSettingsError):
            agent._setup_from_init()

        assert owner_reads == [tmp_path]
        assert teardown_calls == []
        assert agent._sealed is state["sealed"] is True
        assert agent.service is state["service"]
        assert agent._session is state["session"]
        assert agent._prompt_manager._sections == state["sections"]
        assert agent._base_prompt == state["base_prompt"]
        assert agent._tool_handlers == state["handlers"]
        assert agent._tool_schemas == state["schemas"]
        assert agent._official_tool_plugins == state["official_plugins"]
        assert agent._official_tool_bindings == state["official_bindings"]
        assert agent._capabilities == state["capabilities"]
        assert agent._capability_managers == state["capability_managers"]
        assert agent._intrinsics == state["intrinsics"]
        assert agent._intrinsic_modules == state["intrinsic_modules"]
        assert agent._post_molt_hooks == state["post_molt_hooks"]
        assert agent._psyche_settings_snapshot is state["snapshot"]
        assert agent._mcp_clients == [client]
        assert agent._mcp_clients_by_tool == {"last_good_tool": client}
        assert agent._mcp_tool_collisions == {"last_good_collision"}
        assert agent._mcp_tool_metadata == {
            "last_good_tool": {"title": "Last good"}
        }
        assert agent._mcp_init_specs == {"last_good": {"command": "kept"}}
        assert (tmp_path / "system/base_prompt.md").read_bytes() == state["base_mirror"]
        assert (tmp_path / "system/covenant.md").read_bytes() == state["covenant_mirror"]
        assert (tmp_path / "system/system.md").read_bytes() == state["system_mirror"]
        client.close.assert_not_called()
    finally:
        agent.stop(timeout=1.0)


def test_deep_refresh_removes_old_capabilities(tmp_path):
    """Capabilities removed from init.json are gone after refresh.

    Tested against opt-in (non-core) capabilities so the assertion is about
    the refresh path, not about the core-defaults floor. Core capabilities
    persist across refresh regardless of init.json — that is by design;
    `manifest.disable` is the opt-out channel for those.
    """
    init = _make_init(capabilities={"web": {"provider": "duckduckgo"}})
    agent = _make_agent(tmp_path, init)
    agent._setup_from_init()  # initial setup

    agent._sealed = True
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = MagicMock()
    agent._session = mock_session

    cap_names_before = {name for name, _ in agent._capabilities}
    assert "web" in cap_names_before

    # Drop web from init.json
    new_init = _make_init(capabilities={})
    (tmp_path / "init.json").write_text(json.dumps(new_init))

    agent._setup_from_init()

    cap_names_after = {name for name, _ in agent._capabilities}
    assert "web" not in cap_names_after


def test_deep_refresh_drops_and_reclaims_web_official_surface(tmp_path):
    """An opt-in Web refresh leaves claims, schemas, and handlers in sync."""
    from lingtai.tools.web_search import DECLARATION

    agent = _make_agent(tmp_path, _make_init(capabilities={"web": {}}))
    try:
        agent._setup_from_init()
        assert agent.official_tool_plugins["web"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("web") == 1
        assert "web" in agent._tool_handlers

        (tmp_path / "init.json").write_text(json.dumps(_make_init(capabilities={})))
        agent._setup_from_init()
        assert "web" not in agent.official_tool_plugins
        assert "web" not in [schema.name for schema in agent._tool_schemas]
        assert "web" not in agent._tool_handlers

        (tmp_path / "init.json").write_text(
            json.dumps(_make_init(capabilities={"web": {}}))
        )
        agent._setup_from_init()
        assert agent.official_tool_plugins["web"] is DECLARATION
        assert [schema.name for schema in agent._tool_schemas].count("web") == 1
        assert "web" in agent._tool_handlers
    finally:
        agent._workdir_lease.release()


def test_deep_refresh_preserves_chat_history(tmp_path):
    """ChatInterface is passed through to _rebuild_session after refresh."""
    agent = _make_agent(tmp_path, _make_init())
    agent._sealed = True

    mock_interface = MagicMock()
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = mock_interface
    agent._session = mock_session

    agent._setup_from_init()

    mock_session._rebuild_session.assert_called_once_with(mock_interface)


def test_deep_refresh_clears_stale_prompt_sections(tmp_path):
    """Prompt sections from old capabilities don't survive refresh."""
    agent = _make_agent(tmp_path, _make_init())

    # Simulate a stale prompt section from a removed capability
    agent._prompt_manager.write_section("some_old_section", "stale content")
    assert agent._prompt_manager.read_section("some_old_section") is not None

    agent._setup_from_init()

    # Stale section should be gone
    assert agent._prompt_manager.read_section("some_old_section") is None


def test_deep_refresh_reseals(tmp_path):
    """Tool surface is re-sealed after refresh completes."""
    agent = _make_agent(tmp_path, _make_init())
    agent._sealed = True
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = MagicMock()
    agent._session = mock_session

    agent._setup_from_init()

    assert agent._sealed is True


@patch("lingtai.agent.LLMService")
def test_refresh_logs_env_resolve_warning(mock_llm_service, tmp_path, monkeypatch):
    """A refresh whose api_key_env no longer resolves warns instead of dying.

    A live agent must not be killed by a hot-edited env_file that transiently
    loses the named variable; the diagnostic lands in the agent log.
    """
    init = _make_init()
    init["manifest"]["llm"]["api_key"] = None
    init["manifest"]["llm"]["api_key_env"] = "TEST_REFRESH_MISSING_KEY"
    env_file = tmp_path / ".env"
    env_file.write_text("UNRELATED=1\n")
    init["env_file"] = str(env_file)
    agent = _make_agent(tmp_path, init)

    logged = []
    monkeypatch.setattr(
        agent, "_log", lambda *args, **kwargs: logged.append((args, kwargs))
    )
    monkeypatch.delenv("TEST_REFRESH_MISSING_KEY", raising=False)

    agent._setup_from_init()

    assert any(
        args and args[0] == "env_resolve_warning"
        and "TEST_REFRESH_MISSING_KEY" in str(kwargs.get("message", ""))
        for args, kwargs in logged
    )


def test_live_refresh_rebuilds_service_from_system_context_window(tmp_path, monkeypatch):
    from lingtai.llm.service import CONSERVATIVE_CONTEXT_WINDOW

    init = _make_init()
    init["manifest"]["context_limit"] = 123_456
    agent = _make_agent(tmp_path, init)
    agent.service._context_window = 100_000

    mock_interface = MagicMock()
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = mock_interface
    agent._session = mock_session

    constructed = []

    class FakeService:
        def __init__(self, **kwargs):
            constructed.append(kwargs)
            self.provider = kwargs["provider"]
            self.model = kwargs["model"]
            self._base_url = kwargs.get("base_url")
            self._context_window = kwargs["context_window"]
            self._provider_defaults = kwargs.get("provider_defaults") or {}

    monkeypatch.setattr("lingtai.agent.LLMService", FakeService)

    agent._setup_from_init()
    assert constructed[-1]["context_window"] == CONSERVATIVE_CONTEXT_WINDOW
    assert agent.service._context_window == CONSERVATIVE_CONTEXT_WINDOW
    assert agent._session._llm_service is agent.service

    (tmp_path / "settings").mkdir(exist_ok=True)
    (tmp_path / "settings" / "system.json").write_text(
        json.dumps({"schema_version": 2, "context_limit": 234_567})
    )
    (tmp_path / "init.json").write_text(json.dumps(init))

    agent._setup_from_init()
    assert constructed[-1]["context_window"] == 234_567
    assert agent.service._context_window == 234_567


def test_init_procedures_override_is_migrated_not_prompted(tmp_path):
    """Legacy init.json procedures content is archived, removed, and ignored."""
    legacy = "LEGACY-PROCEDURES-OVERRIDE"
    init = _make_init()
    init["procedures"] = legacy
    agent = _make_agent(tmp_path, init)

    agent._setup_from_init()

    packaged = _packaged_procedures()
    prompt = agent._prompt_manager.render()
    assert legacy not in prompt
    assert packaged in prompt
    data = json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))
    # Compatibility is diagnosed/read-only; the reader never rewrites input or
    # creates migration archives/progress.
    assert data["procedures"] == legacy
    assert not (tmp_path / "system" / "migrations").exists()
    assert not _events(tmp_path, "init_procedures_override_migrated")

    agent._setup_from_init()
    assert json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))["procedures"] == legacy


def test_single_space_procedures_no_longer_opts_out(tmp_path):
    """A single-space legacy procedures value is migrated, not treated as opt-out."""
    init = _make_init()
    init["procedures"] = " "
    agent = _make_agent(tmp_path, init)

    agent._setup_from_init()

    packaged = _packaged_procedures()
    procedures = agent._prompt_manager.read_section("procedures") or ""
    assert procedures == packaged
    data = json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))
    assert data["procedures"] == " "
    assert not (tmp_path / "system" / "migrations").exists()



def test_custom_procedures_file_is_removed_not_prompted(tmp_path):
    """Legacy procedures_file overrides are removed even for custom paths."""
    custom = tmp_path / "custom-procedures.md"
    custom.write_text("CUSTOM-PROCEDURES-FILE", encoding="utf-8")
    init = _make_init()
    init["procedures_file"] = str(custom)
    agent = _make_agent(tmp_path, init)

    agent._setup_from_init()

    packaged = _packaged_procedures()
    prompt = agent._prompt_manager.render()
    assert "CUSTOM-PROCEDURES-FILE" not in prompt
    assert agent._prompt_manager.read_section("procedures") == packaged
    data = json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))
    assert data["procedures_file"] == str(custom)
    assert not (tmp_path / "system" / "migrations").exists()


def test_system_procedures_is_overwritten_by_packaged_default(tmp_path):
    """Manual system/procedures.md edits are replaced by the packaged default."""
    agent = _make_agent(tmp_path, _make_init())
    system_dir = tmp_path / "system"
    system_dir.mkdir(exist_ok=True)
    (system_dir / "procedures.md").write_text("MANUAL-PROCEDURES-EDIT", encoding="utf-8")

    agent._setup_from_init()

    # The mirror keeps the full skill-style source (frontmatter + body); the
    # rendered section is the body only.
    assert (system_dir / "procedures.md").read_text(encoding="utf-8") == _packaged_procedures_file()
    assert agent._prompt_manager.read_section("procedures") == _packaged_procedures()


def test_system_guidance_is_overwritten_by_packaged_default(tmp_path):
    """Manual system/guidance.json edits are replaced by packaged guidance."""
    agent = _make_agent(tmp_path, _make_init())
    system_dir = tmp_path / "system"
    system_dir.mkdir(exist_ok=True)
    (system_dir / "guidance.json").write_text('{"stale": true}\n', encoding="utf-8")

    agent._setup_from_init()

    guidance_path = system_dir / "guidance.json"
    assert json.loads(guidance_path.read_text(encoding="utf-8")) == _packaged_guidance()
    assert guidance_path.read_text(encoding="utf-8").endswith("\n")

def _packaged_substrate_file() -> str:
    """Full packaged substrate source, including skill-style frontmatter."""
    from importlib.resources import files

    return files("lingtai.prompts").joinpath("substrate/substrate.md").read_text(encoding="utf-8")


def _packaged_substrate() -> str:
    """Rendered substrate body (frontmatter stripped) — what reaches the prompt."""
    from lingtai.kernel._frontmatter import strip_frontmatter

    return strip_frontmatter(_packaged_substrate_file())


def test_psyche_base_prompt_reaches_rendered_prompt_via_boot(tmp_path):
    """Psyche base prompt renders after principle and before covenant."""
    from lingtai.cli import load_init, build_agent

    init = _make_init(
        capabilities={"file": {}},
        covenant="LEGACY INIT COVENANT",
        base_prompt="LEGACY INIT BASE",
    )
    (tmp_path / "init.json").write_text(json.dumps(init))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "psyche.json").write_text(json.dumps({
        "schema_version": 1,
        "base_prompt": "Recipe-injected base prompt.",
        "covenant": "Be helpful.",
    }))

    data = load_init(tmp_path)
    agent = build_agent(data, tmp_path)
    try:
        assert agent._base_prompt == "Recipe-injected base prompt."
        prompt = agent._build_system_prompt()
        assert "Recipe-injected base prompt." in prompt
        assert "LEGACY INIT BASE" not in prompt
        assert "LEGACY INIT COVENANT" not in prompt
        assert prompt.index("Recipe-injected base prompt.") < prompt.index("Be helpful.")
    finally:
        agent._workdir_lease.release()


def test_psyche_base_prompt_file_reaches_rendered_prompt_via_boot(tmp_path):
    """Psyche base_prompt_file preserves the third-party file injection point."""
    from lingtai.cli import load_init, build_agent

    (tmp_path / "recipe-base-prompt.md").write_text(
        "Recipe base prompt from file.", encoding="utf-8"
    )
    (tmp_path / "ignored-base-prompt.md").write_text(
        "LEGACY INIT BASE FILE", encoding="utf-8"
    )
    init = _make_init(capabilities={"file": {}}, covenant="LEGACY INIT COVENANT")
    init["base_prompt_file"] = "ignored-base-prompt.md"
    (tmp_path / "init.json").write_text(json.dumps(init))
    (tmp_path / "settings").mkdir()
    (tmp_path / "settings" / "psyche.json").write_text(json.dumps({
        "schema_version": 1,
        "base_prompt_file": "recipe-base-prompt.md",
        "covenant": "Be helpful.",
    }))

    data = load_init(tmp_path)
    agent = build_agent(data, tmp_path)
    try:
        assert agent._base_prompt == "Recipe base prompt from file."
        prompt = agent._build_system_prompt()
        assert "Recipe base prompt from file." in prompt
        assert "LEGACY INIT BASE FILE" not in prompt
        assert "LEGACY INIT COVENANT" not in prompt
        assert prompt.index("Recipe base prompt from file.") < prompt.index("Be helpful.")
    finally:
        agent._workdir_lease.release()


def test_psyche_base_prompt_post_molt_reload_keeps_injection(tmp_path):
    """Post-molt reconstruction re-reads Psyche's prompt owner."""
    agent = _make_agent(tmp_path, _make_init())
    (tmp_path / "settings" / "psyche.json").write_text(json.dumps({
        "schema_version": 1,
        "base_prompt": "Recipe base prompt.",
    }))
    agent._setup_from_init()

    for cb in getattr(agent, "_post_molt_hooks", []):
        cb()

    assert agent._base_prompt == "Recipe base prompt."
    assert "Recipe base prompt." in agent._build_system_prompt()


def test_init_substrate_override_is_migrated_not_prompted(tmp_path):
    """Legacy init.json substrate content is archived, removed, and ignored;
    the packaged substrate renders instead."""
    legacy = "LEGACY-SUBSTRATE-OVERRIDE"
    agent = _make_agent(tmp_path, _make_init(substrate=legacy))

    agent._setup_from_init()

    prompt = agent._prompt_manager.render()
    assert legacy not in prompt
    assert _packaged_substrate() in prompt
    data = json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))
    assert data["substrate"] == legacy
    assert not (tmp_path / "system" / "migrations").exists()
    assert not _events(tmp_path, "init_prompt_contract_migrated")

    # A second setup reads the same compatibility input without changing it.
    agent._setup_from_init()
    assert json.loads((tmp_path / "init.json").read_text(encoding="utf-8"))["substrate"] == legacy


def test_procedures_falls_back_to_system_file_when_packaged_missing(tmp_path):
    """If the packaged default cannot be read, system/procedures.md is fallback."""
    fallback = "FALLBACK-PROCEDURES"
    agent = _make_agent(tmp_path, _make_init())
    system_dir = tmp_path / "system"
    system_dir.mkdir(exist_ok=True)
    (system_dir / "procedures.md").write_text(fallback, encoding="utf-8")

    with patch("importlib.resources.files", side_effect=FileNotFoundError):
        agent._setup_from_init()

    assert agent._prompt_manager.read_section("procedures") == fallback
    assert (system_dir / "procedures.md").read_text(encoding="utf-8") == fallback


# ---------------------------------------------------------------------------
# Prompt-section reconstruction: covenant vs character separation + molt
# (regression for the "lingtai.md folded into covenant, dropped after molt"
# bug — these fail on main and pass after the single-writer fix).
# ---------------------------------------------------------------------------


def test_reload_keeps_covenant_and_character_separate(tmp_path):
    """Boot/refresh-style reload: covenant.md → `covenant`, lingtai.md →
    `character`. The character text must never be folded into covenant."""
    agent = _make_agent(tmp_path, _make_init(covenant="The operator contract."))
    # Author the durable character file as the agent would via file.write/edit.
    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    (system_dir / "lingtai.md").write_text("I am a meticulous archivist.")

    agent._setup_from_init()

    covenant = agent._prompt_manager.read_section("covenant") or ""
    character = agent._prompt_manager.read_section("character") or ""

    assert "The operator contract." in covenant
    assert "I am a meticulous archivist." in character
    # Separation: neither section bleeds into the other.
    assert "I am a meticulous archivist." not in covenant
    assert "The operator contract." not in character

    # The passive refresh contract publishes only after capabilities, MCP,
    # manuals, and identity have been rebuilt. Its durable mirror must therefore
    # be byte-identical to the final builder state used for session replay.
    assert (system_dir / "system.md").read_text(encoding="utf-8") == agent._build_system_prompt()


def test_post_molt_preserves_character_section(tmp_path):
    """Firing the post-molt hooks (as _molt.py does) must leave the
    `character` section intact — not overwritten with covenant-only content.

    On main, _reload_prompt_sections runs last and overwrites covenant with
    covenant.md-only, silently dropping the character until process restart.
    With the single-writer fix both hooks produce identical complete output,
    so order no longer matters."""
    agent = _make_agent(tmp_path, _make_init(covenant="The operator contract."))
    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    (system_dir / "lingtai.md").write_text("I am a meticulous archivist.")

    # Boot/refresh registers exactly one canonical full reconstruction hook.
    agent._setup_from_init()
    assert agent._post_molt_hooks == [agent._reconstruct_context]

    # Mirror the molt path: invoke the registered reconstruction hook.
    for cb in getattr(agent, "_post_molt_hooks", []):
        cb()

    character = agent._prompt_manager.read_section("character") or ""
    covenant = agent._prompt_manager.read_section("covenant") or ""
    assert "I am a meticulous archivist." in character
    assert "I am a meticulous archivist." not in covenant


def test_post_molt_preserves_pad_append_pinned_reference(tmp_path):
    """Firing the post-molt hooks must keep the `pad_append.json` pinned
    reference in the `pad` section. On main the pad.md-only writer runs last
    and drops the appended reference; the single-writer fix routes both
    hooks through `_pad_load`, which always composes pad.md + appends."""
    agent = _make_agent(tmp_path, _make_init())
    system_dir = agent._working_dir / "system"
    system_dir.mkdir(exist_ok=True)
    (system_dir / "pad.md").write_text("Working notes line.")

    # Pin a reference file via pad_append.json (what pad(action='append') writes).
    ref = agent._working_dir / "reference.md"
    ref.write_text("PINNED-REFERENCE-MARKER")
    (system_dir / "pad_append.json").write_text(json.dumps(["reference.md"]))

    agent._setup_from_init()

    assert agent._post_molt_hooks == [agent._reconstruct_context]
    # Mirror the molt path: invoke the registered reconstruction hook.
    for cb in getattr(agent, "_post_molt_hooks", []):
        cb()

    pad = agent._prompt_manager.read_section("pad") or ""
    assert "Working notes line." in pad
    assert "PINNED-REFERENCE-MARKER" in pad


# ---------------------------------------------------------------------------
# Codex cache-affinity rebuild on refresh.
#
# A live refresh (re)builds the Codex adapter while preserving chat history. The
# cache-affinity id is a pure hash of the agent path (no epoch / no clock), so
# the rebuilt adapter MUST keep the byte-identical id — the agent stays pinned to
# the same sticky-warm backend cache slot across refresh. These tests prove the
# refresh rebuilds the Codex service/adapter and the id is stable across it.
# ---------------------------------------------------------------------------


def _codex_agent(tmp_path: Path, epoch: float):
    """Build a real Agent backed by a real Codex LLMService.

    ``time.time`` is patched during construction only to keep any incidental
    timestamps deterministic; the Codex id does NOT depend on the clock (it is a
    pure hash of the agent path), so the patched value never affects it. The
    returned agent's ``service`` is a genuine ``LLMService`` (not a mock), so
    ``_setup_from_init`` exercises the real Codex rebuild path.
    """
    from unittest.mock import patch as _patch

    from lingtai.agent import Agent
    from lingtai.llm.service import (
        LLMService,
        build_provider_defaults_from_manifest_llm,
    )
    from lingtai.kernel.config import AgentConfig
    import lingtai  # noqa: F401  (registers the codex adapter factory)

    init = _make_init(provider="codex", model="gpt-5.5")
    # Pin max_rpm so the provider-defaults bucket is byte-identical before and
    # after refresh — otherwise an incidental max_rpm change (default 60 on
    # refresh) would rebuild the service for the wrong reason and mask the bug.
    init["manifest"]["max_rpm"] = 60
    (tmp_path / "init.json").write_text(json.dumps(init))

    llm = init["manifest"]["llm"]
    provider_defaults = build_provider_defaults_from_manifest_llm(
        llm, max_rpm=60, working_dir=tmp_path
    )
    with _patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, _patch(
        "time.time", return_value=epoch
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        service = LLMService(
            provider="codex",
            model="gpt-5.5",
            api_key="fake",
            provider_defaults=provider_defaults,
        )
        agent = Agent(
            service,
            agent_name="test-agent",
            working_dir=tmp_path,
            config=AgentConfig(),
        )
    return agent


def test_refresh_rebuilds_codex_adapter_with_stable_id(tmp_path):
    """A live Codex refresh rebuilds the adapter but KEEPS the same affinity id.

    The Codex cache-affinity id is a hash of the agent path + current molt_count
    (no epoch, no time dependence). At a fixed molt_count a refresh — even at a
    different wall-clock — must yield a fresh adapter instance whose id is
    byte-identical to the pre-refresh id. This is the whole point of removing the
    epoch-stamp / rotation: within a molt segment the agent keeps routing to the
    same sticky-warm backend cache slot across restarts. (The id intentionally
    moves only at a molt boundary, which a refresh is not.)
    """
    from unittest.mock import patch

    agent = _codex_agent(tmp_path, epoch=1_700_000_000)
    agent._sealed = True

    old_adapter = agent.service.get_adapter("codex")
    old_id, _ = old_adapter._resolve_codex_ids("gpt-5.5")
    assert old_id is not None  # per-agent identity is wired by default

    # A later refresh at a DIFFERENT wall-clock must NOT change the id.
    mock_interface = MagicMock()
    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = mock_interface
    # The real Session._rebuild_session would call create_session; we only need
    # to confirm refresh hands it the preserved interface and a fresh service.
    agent._session = mock_session

    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=1_700_000_500
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        agent._setup_from_init()

    new_adapter = agent.service.get_adapter("codex")
    new_id, _ = new_adapter._resolve_codex_ids("gpt-5.5")

    # 1. A genuinely fresh adapter instance (not the cached boot one).
    assert new_adapter is not old_adapter
    # 2. The id is STABLE across refresh despite the different clock.
    assert new_id == old_id
    assert new_id is not None
    # 3. The id is the per-agent hash of (anchor, molt_count). No real
    #    .agent.json here, so molt_count is 0 (no epoch, no clock).
    from lingtai.llm.openai.adapter import _codex_session_id

    anchor = str((tmp_path / "init.json").resolve())
    assert new_id == _codex_session_id(anchor, 0)
    # 4. The new service object is wired into the session that rebuilds history.
    assert agent._session._llm_service is agent.service
    # 5. Chat history is preserved: the saved interface is replayed.
    mock_session._rebuild_session.assert_called_once_with(mock_interface)


def test_refresh_codex_adapter_keeps_per_agent_anchor(tmp_path):
    """The rebuilt adapter still anchors on the same agent path (identity).

    Both the old and new ids derive from the same ``init.json`` anchor (a pure
    hash of it), so the rebuilt adapter remains a per-agent identity (not a
    shared model-only key) and the id is byte-identical across refresh.
    """
    from unittest.mock import patch

    agent = _codex_agent(tmp_path, epoch=1_700_000_000)
    agent._sealed = True

    old_anchor = agent.service.get_adapter("codex")._codex_session_anchor

    mock_session = MagicMock()
    mock_session.chat = MagicMock()
    mock_session.chat.interface = MagicMock()
    agent._session = mock_session

    with patch("lingtai.auth.codex.CodexTokenManager") as mgr_cls, patch(
        "time.time", return_value=1_700_000_500
    ):
        mgr_cls.return_value.get_access_token.return_value = "fake-token"
        agent._setup_from_init()

    new_adapter = agent.service.get_adapter("codex")
    assert new_adapter._codex_session_anchor == old_anchor
    assert new_adapter._codex_session_anchor == str((tmp_path / "init.json").resolve())


def test_refresh_watcher_receives_parent_captured_runtime_identity(tmp_path):
    """The generated watcher structurally reuses only parent-captured identity."""
    from lingtai.kernel.base_agent.lifecycle import _perform_refresh

    captured_identity = {
        "kernel_version": "captured-version",
        "kernel_runtime_stamp": "captured-stamp",
        "kernel_runtime": {"version": "captured-version", "stamp": "captured-stamp"},
    }
    agent = _make_agent(tmp_path)
    agent._runtime_identity_event_fields = captured_identity
    agent._build_launch_cmd = MagicMock(
        return_value=["lingtai-agent", "run", str(tmp_path)]
    )

    from tests._refresh_watcher_helpers import make_test_refresh_watcher

    agent._refresh_watcher = make_test_refresh_watcher()
    _perform_refresh(
        agent,
        skip_chat_history_save=True,
        skip_save_reason="identity-handoff-test",
    )

    script = agent._refresh_watcher.last_script
    tree = ast.parse(script)

    identity_assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "identity_fields"
            for target in node.targets
        )
    ]
    assert len(identity_assignments) == 1
    assert ast.literal_eval(identity_assignments[0].value) == captured_identity

    logger = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "log"
    )
    entry_assignment = next(
        node
        for node in ast.walk(logger)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "entry"
            for target in node.targets
        )
        and isinstance(node.value, ast.Dict)
    )
    expanded_names = [
        value.id
        for key, value in zip(
            entry_assignment.value.keys, entry_assignment.value.values
        )
        if key is None and isinstance(value, ast.Name)
    ]
    assert expanded_names == ["identity_fields", "kw"]

    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imported_symbols = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert all("revision" not in imported.lower() for imported in imports)
    assert all("revision" not in symbol.lower() for symbol in imported_symbols)
    assert all("lingtai.adapters" not in imported for imported in imports)
    assert all(
        "revision" not in node.name.lower()
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert {"rev-parse", "current_revision", "is_dirty"}.isdisjoint(
        string_literals
    )
    assert {
        "PosixGitCliAdapter",
        "SourceRevisionPort",
        "current_revision",
        "is_dirty",
        "runtime_identity_event_fields",
    }.isdisjoint(names | attributes | imported_symbols)
    agent._workdir_lease.release()


def test_deep_refresh_keeps_intrinsic_task_card_watch_without_telegram(tmp_path):
    """The intrinsic ``task_card`` manager is agent state and survives refresh."""
    from lingtai.agent import Agent
    from lingtai.kernel.config import AgentConfig

    (tmp_path / "init.json").write_text(json.dumps(_make_init()))
    service = MagicMock()
    service.provider = "openai"
    service.model = "gpt-4o"
    service._base_url = None
    agent = Agent(
        service,
        agent_name="test-agent",
        working_dir=tmp_path,
        config=AgentConfig(),
    )
    try:
        old_manager = agent._task_card_manager
        renderer = tmp_path / "task-card-renderer.py"
        renderer.write_text("print('# refresh')", encoding="utf-8")
        started = agent._tool_handlers["task_card"]({
            "action": "start",
            "input": {
                "renderer_path": str(renderer),
                "interval_s": 3600,
                "timeout_s": 3,
            },
            "reasoning": "exercise intrinsic refresh lifecycle",
        })
        watch = old_manager._watch
        assert watch is not None
        assert watch.thread is not None and watch.thread.is_alive()

        agent._setup_from_init()

        assert agent._task_card_manager is old_manager
        assert agent._task_card_manager._watch is watch
        assert watch.thread is not None and watch.thread.is_alive()
        assert [s.name for s in agent._build_tool_schemas()].count("task_card") == 1
        assert [s.name for s in agent._tool_schemas].count("task_card") == 1
        inspected = agent._tool_handlers["task_card"]({
            "action": "inspect",
            "input": {"watch_id": started["watch_id"]},
            "reasoning": "inspect retained watch",
        })
        assert inspected["state"] == "watching"
        assert (tmp_path / "taskcard" / "status").read_text(encoding="utf-8") == "active"
    finally:
        manager = getattr(agent, "_task_card_manager", None)
        if manager is not None:
            manager.shutdown_for_agent_stop(reason="test_cleanup")
        agent._workdir_lease.release()


def test_deep_refresh_telegram_mcp_does_not_replace_intrinsic_task_card(
    tmp_path, monkeypatch
):
    from lingtai.agent import Agent
    from lingtai.kernel.config import AgentConfig
    from lingtai.mcp_servers.telegram.manager import (
        DESCRIPTION as telegram_description,
        SCHEMA as telegram_schema,
    )
    from lingtai.services import mcp as mcp_module

    class _FakeMCPClient:
        instances: list["_FakeMCPClient"] = []

        def __init__(self, command, args=None, env=None):
            self.command = command
            self.args = args
            self.env = env
            self.closed = False
            type(self).instances.append(self)

        def start(self):
            pass

        def close(self):
            self.closed = True

        def list_tools(self):
            return [{
                "name": "telegram",
                "schema": telegram_schema,
                "description": telegram_description,
            }]

        def call_tool(self, name, args, timeout=None):
            return {"status": "error", "name": name}

    monkeypatch.setattr(mcp_module, "MCPClient", _FakeMCPClient)
    (tmp_path / "init.json").write_text(json.dumps(_make_init()))
    mcp_dir = tmp_path / "mcp"
    mcp_dir.mkdir()
    (mcp_dir / "servers.json").write_text(json.dumps({
        "telegram": {"command": "fake-telegram"},
    }))

    service = MagicMock()
    service.provider = "openai"
    service.model = "gpt-4o"
    service._base_url = None
    agent = Agent(
        service,
        agent_name="test-agent",
        working_dir=tmp_path,
        config=AgentConfig(),
    )
    try:
        old_manager = agent._task_card_manager
        task_card_handler = agent._tool_handlers["task_card"]
        assert "telegram" in agent._tool_handlers
        assert not hasattr(agent, "_task_card_controller")

        agent._setup_from_init()

        assert _FakeMCPClient.instances[0].closed is True
        assert len(_FakeMCPClient.instances) == 2
        assert agent._task_card_manager is old_manager
        assert getattr(agent._tool_handlers["task_card"], "__self__", None) is old_manager
        assert getattr(task_card_handler, "__self__", None) is old_manager
        assert not hasattr(agent, "_task_card_controller")
        assert [s.name for s in agent._build_tool_schemas()].count("task_card") == 1
    finally:
        manager = getattr(agent, "_task_card_manager", None)
        if manager is not None:
            manager.shutdown_for_agent_stop(reason="test_cleanup")
        agent._workdir_lease.release()
