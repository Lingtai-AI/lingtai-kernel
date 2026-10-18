"""Thin tests of real shared-reader and explicit reference persistence boundaries."""
import copy
import json
from types import SimpleNamespace

import pytest

from lingtai.agent import Agent, load_preset
from lingtai.init_reader import read_init, reader_callbacks
from lingtai.kernel.config_resolve import normalize_init
from lingtai.tools.registry import BUILTIN_TOOLS, apply_core_defaults


def _read(root):
    materialize, prepare = reader_callbacks(root, load_preset=load_preset)
    return read_init(root, materialize=materialize, prepare=prepare)


@pytest.mark.parametrize("legacy", [False, True])
def test_model_switch_keeps_init_owners_and_disk_shape(tmp_path, legacy):
    for model in ("a", "b"):
        (tmp_path / f"{model}.json").write_text(json.dumps({
            "description": {"summary": model},
            "manifest": {"llm": {"provider": "openai", "model": model},
                         "capabilities": {"skills": {"paths": ["wrong"]}, "web": None}},
        }))
    policy = {"active": "./a.json", "default": "./a.json", "allowed": ["./a.json", "./b.json"]}
    document = {"agent": {"permissions": {"karma": False}}, "llm": {"preset": policy},
                "tools": {"skills": {"paths": ["mine"]}, "web": False}, "plugins": [], "mcp": {}}
    if legacy:
        document = normalize_init(document)
    path = tmp_path / "init.json"
    path.write_text(json.dumps(document))
    before = _read(tmp_path)
    assert before.ok
    # Call the actual persistence method without constructing a runtime, service,
    # or provider. The only dependency it uses is a real local preset loader.
    Agent._activate_preset(SimpleNamespace(_working_dir=tmp_path), "./b.json")
    authored = json.loads(path.read_text())
    expected = copy.deepcopy(document)
    (expected["manifest"]["preset"] if legacy else expected["llm"]["preset"])["active"] = "./b.json"
    if legacy:
        expected["manifest"].pop("llm", None)
    assert authored == expected
    after = _read(tmp_path)
    assert after.ok and after.data["manifest"]["llm"]["model"] == "b"
    assert after.data["manifest"]["capabilities"] == before.data["manifest"]["capabilities"]
    assert after.data["manifest"]["admin"] == {"karma": False}
    from lingtai.tools.avatar import AvatarManager
    child = AvatarManager._make_avatar_init(authored, "child", parent_working_dir=tmp_path)
    child_manifest = normalize_init(child)["manifest"]
    assert child_manifest["capabilities"] == normalize_init(authored)["manifest"]["capabilities"]
    assert child_manifest["preset"]["active"] == str((tmp_path / "a.json").resolve())
    assert child_manifest["admin"] == {}


def test_new_and_old_reader_roundtrip_is_read_only(tmp_path):
    route = {"provider": "openai", "model": "test"}
    for document in ({"llm": route, "tools": {"web": False}},
                     {"manifest": {"llm": route, "capabilities": {"web": None}}, "pad": "seed"}):
        raw = json.dumps(document)
        (tmp_path / "init.json").write_text(raw)
        outcome = _read(tmp_path)
        assert outcome.ok
        assert outcome.data["manifest"]["llm"] == route
        assert (tmp_path / "init.json").read_text() == raw
        enabled = apply_core_defaults(outcome.data["manifest"]["capabilities"])
        assert set(enabled) == set(BUILTIN_TOOLS) - {"web"}
    assert set(apply_core_defaults({})) == set(BUILTIN_TOOLS)


def test_conflicts_fail_read_only_and_direct_caller_keeps_widening(tmp_path):
    conflict = {"llm": {"provider": "openai", "model": "test", "preset": {}}, "tools": {}}
    path = tmp_path / "init.json"
    raw = json.dumps(conflict)
    path.write_text(raw)
    assert not _read(tmp_path).ok
    assert path.read_text() == raw
    document = {"llm": {"preset": {"active": "a.json", "default": "a.json", "allowed": ["a.json"]}}}
    raw = json.dumps(document)
    path.write_text(raw)
    from lingtai.tools.system.preset import _refresh
    agent = SimpleNamespace(_working_dir=tmp_path, _log=lambda *a, **kw: None)
    refused = _refresh(agent, {"preset": "b.json"})
    assert refused["status"] == "error" and "allowed" in refused["message"]
    assert path.read_text() == raw
    (tmp_path / "b.json").write_text(json.dumps({"name": "b", "description": {"summary": "b"},
                                                "llm": {"provider": "openai", "model": "b"}}))
    Agent._activate_preset(agent, "b.json")
    policy = json.loads(path.read_text())["llm"]["preset"]
    assert policy == {"active": "b.json", "default": "a.json", "allowed": ["a.json", "b.json"]}


def test_project_seed_publishes_pad_separately_and_preserves_init_owners(tmp_path):
    from lingtai.kernel.project.configuration import (
        ConfigurationOptions, ConfigureMemberRequest, MemberDocuments, configuration_seed,
    )
    from lingtai.adapters.project_configuration import FilesystemProjectConfigurationAdapter

    preset = tmp_path / "model.json"
    preset.write_text(json.dumps({"name": "fixture", "description": {"summary": "fixture"},
                                  "llm": {"provider": "openai", "model": "fixture"}}))
    request = ConfigureMemberRequest("agent", "Name", str(preset), False,
                                     {"llm": {"provider": "openai", "model": "fixture"},
                                      "capabilities": {"skills": {"paths": ["not-owned"]}}},
                                     ConfigurationOptions(pad="creation seed"))
    seed = configuration_seed(request, MemberDocuments("agent", False, None, False, None))
    assert seed.init_document["tools"]["skills"]["paths"] == ["../.library_shared", "~/.lingtai-tui/utilities"]
    assert set(seed.init_document) == {"agent", "llm", "tools", "plugins", "mcp"}
    assert set(seed.init_document["tools"]) == {"skills"} and seed.pad_seed == "creation seed"
    assert "pad" not in seed.init_document and "manifest" not in seed.init_document
    adapter = FilesystemProjectConfigurationAdapter(tmp_path, load_preset=load_preset)
    receipt = adapter.save_configured_member(seed)
    assert receipt.init_written and not receipt.code
    pad = tmp_path / ".lingtai" / "agent" / "system" / "pad.md"
    assert pad.read_text() == "creation seed"
    pad.write_text("durable existing memory")
    tools = {"skills": {"paths": ["mine"]}, "web": False}
    previous_doc = copy.deepcopy(seed.init_document)
    previous_doc["tools"] = tools
    updated = configuration_seed(request, MemberDocuments("agent", True, previous_doc, True, seed.agent_manifest))
    assert updated.init_document["tools"] == tools
    assert not adapter.save_configured_member(updated).code
    assert pad.read_text() == "durable existing memory"


def test_core_owner_fields_and_explicit_mcp_reader_gate(tmp_path):
    document = {"agent": {"time_awareness": False, "timezone_awareness": False},
                "llm": {"provider": "openai", "model": "fixture"},
                "tools": {"email": {"pseudo_agent_subscriptions": ["../human"]},
                          "context": {"summarize_notification_threshold": 0}, "system": False},
                "mcp": {"imap": {}, "custom": {"type": "stdio", "command": "fixture-only"}},
                "plugins": []}
    path = tmp_path / "init.json"
    raw = json.dumps(document)
    path.write_text(raw)
    result = _read(tmp_path)
    assert result.ok
    manifest = result.data["manifest"]
    assert manifest["pseudo_agent_subscriptions"] == ["../human"]
    assert manifest["summarize_notification_threshold"] == 0
    assert manifest["time_awareness"] is False and manifest["timezone_awareness"] is False
    assert not {"email", "context", "system"}.intersection(manifest["capabilities"])
    assert not (tmp_path / "mcp_registry.jsonl").exists()  # validation is not registration/launch
    (tmp_path / "mcp").mkdir()
    (tmp_path / "mcp" / "servers.json").write_text('{"old": {"command": "fixture-only"}}')
    refused = _read(tmp_path)
    assert not refused.ok and refused.stage == "CONFLICT"
    assert "explicitly move" in refused.safe_excerpt and path.read_text() == raw


@pytest.mark.parametrize("legacy_source", [False, True])
@pytest.mark.parametrize("use_preset", [False, True])
def test_inspect_init_subprocess_is_read_only_and_secret_free(tmp_path, legacy_source, use_preset):
    import os
    from pathlib import Path
    import subprocess
    import sys

    route = {"provider": "openai", "model": "inspection-model", "wire_api": "responses",
             "api_key": "SECRET-KEY-SENTINEL", "base_url": "https://SECRET-URL.invalid",
             "default_headers": {"Authorization": "SECRET-HEADER-SENTINEL"}}
    preset = {"description": {"summary": "inspection"}}
    preset.update({"manifest": {"llm": {**route, "context_limit": 8192},
                                 "context_limit": "SECRET-IGNORED-CONTEXT-SENTINEL"}}
                  if legacy_source else {"llm": route})
    (tmp_path / "chosen.json").write_text(json.dumps(preset))
    (tmp_path / "private.env").write_text("SECRET-ENV-SENTINEL=do-not-load\n")
    policy = {"active": "missing.json", "default": "chosen.json",
              "allowed": ["missing.json", "chosen.json"]}
    document = {"agent": {"name": "inspection", "language": "en"},
                "llm": {"preset": policy} if use_preset else route,
                "host": {"env_file": "private.env", "venv_path": "fixture-venv"},
                "tools": {"shell": {"opaque": "SECRET-TOOL-SENTINEL"}}, "mcp": {}}
    if not use_preset and legacy_source:
        document = normalize_init(document)
    else:
        # Actual read_plugins warning contains this configured path unless the
        # inspection boundary suppresses reader diagnostics as well as JSON.
        document["plugins"] = ["SECRET-MISSING-PLUGIN-SENTINEL"]
    (tmp_path / "init.json").write_text(json.dumps(document))
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
           "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    command = [sys.executable, "-B", "-m", "lingtai.cli", "inspect-init", "--agent-dir", str(tmp_path)]
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["schema"] == "lingtai.init.inspection/v1"
    assert payload["reader_status"] in {"FULLY_EFFECTIVE", "READ_OK_WITH_IGNORED_FIELDS"}
    assert payload["failure_stage"] is None
    assert payload["llm"] == {key: route[key] for key in ("provider", "model", "wire_api")}
    assert payload["agent"] == {"name": "inspection", "language": "en"}
    assert payload["host"] == {"env_file": str(tmp_path / "private.env"),
                               "venv_path": str(tmp_path / "fixture-venv")}
    assert payload["llm_source"] == {
        "kind": "preset" if use_preset else "init",
        "path": str(tmp_path / ("chosen.json" if use_preset else "init.json")),
        "pointer": "/manifest/llm" if legacy_source else "/llm",
    }
    assert "SECRET-" not in result.stdout + result.stderr
    assert set(payload) == {"schema", "reader_status", "shape_decision", "failure_stage", "next_step",
                            "agent", "llm", "llm_source", "host"}
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert not (tmp_path / "system/manifest.resolved.json").exists()
    (tmp_path / "init.json").write_text('{"llm": SECRET-BROKEN-JSON}')
    failed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert failed.returncode == 1
    failure = json.loads(failed.stdout)
    assert failure["reader_status"] == "READ_FAILED" and failure["failure_stage"] == "JSON_PARSE"
    assert failure["next_step"] and "llm_source" not in failure and "llm" not in failure
    assert "SECRET-" not in failed.stdout + failed.stderr


def test_vision_canonical_allowlist_borrows_llm_not_preset_tools(tmp_path, monkeypatch):
    from lingtai.tools import vision

    preset = {"description": {"summary": "borrow"}, "manifest": {
        "llm": {"provider": "openai", "model": "borrowed", "api_key": "fixture-key"},
        "capabilities": {"vision": {"provider": "poison", "model": "wrong"}},
    }}
    (tmp_path / "borrow.json").write_text(json.dumps(preset))
    document = {"llm": {"preset": {"active": "borrow.json", "default": "borrow.json",
                                    "allowed": ["borrow.json"]}},
                "tools": {"vision": {"provider": "inherit", "model": "init-owned"}}}
    raw = "// authored JSONC\n" + json.dumps(document)
    (tmp_path / "init.json").write_text(raw)
    manager = SimpleNamespace(_workdir=SimpleNamespace(path=tmp_path), _vision_service=None,
                              _active_provider=SimpleNamespace(service=SimpleNamespace(provider="anthropic", _model="active")))
    rows = vision.VisionManager._dispatch_list(manager, {})
    assert rows["count"] == 1 and rows["presets"][0]["provider"] == "openai"
    assert rows["presets"][0]["model"] == "init-owned"
    observed = {}
    def resolve(workdir, active, provider, **kwargs):
        observed.update(provider=provider, **kwargs)
        return "fixture-service", ""
    monkeypatch.setattr(vision, "_resolve_direct_service", resolve)
    service, reason, identity = vision.VisionManager._build_service_from_preset(manager, "borrow.json")
    assert service == "fixture-service" and not reason
    assert observed["provider"] == "openai" and observed["model"] == "init-owned"
    assert observed["api_key"] is None and identity["model"] == "borrowed"
    assert observed["identity_service"].api_key == "fixture-key"
    denied, _, _ = vision.VisionManager._build_service_from_preset(manager, "unlisted.json")
    assert denied is None
    assert (tmp_path / "init.json").read_text() == raw
    assert json.loads((tmp_path / "borrow.json").read_text()) == preset


@pytest.mark.parametrize("credential", ["inline", "env"])
def test_vision_borrowed_key_keeps_endpoint_and_provider_guard(tmp_path, monkeypatch, credential):
    from lingtai.services import vision as services
    from lingtai.tools import vision

    # Recording factory only: these are nonsecret fixture values, no clients.
    monkeypatch.setenv("VISION_BORROW_FIXTURE", "borrow-fixture")
    monkeypatch.setenv("VISION_OWN_FIXTURE", "own-fixture")
    key = ({"api_key": "borrow-fixture"} if credential == "inline" else
           {"api_key_env": "VISION_BORROW_FIXTURE"})
    (tmp_path / "borrow.json").write_text(json.dumps({"description": {"summary": "borrow"},
        "llm": {"provider": "openai", "model": "fixture-model",
                "base_url": "https://borrow.invalid/v1", **key}}))
    calls = []
    service = object()
    def factory(provider, **kwargs):
        calls.append((provider, kwargs))
        return service
    monkeypatch.setattr(services, "create_vision_service", factory)
    manager = SimpleNamespace(_workdir=SimpleNamespace(path=tmp_path),
        _active_provider=SimpleNamespace(service=SimpleNamespace(provider="anthropic", api_key="not-borrowed")))
    policy = {"active": "borrow.json", "default": "borrow.json", "allowed": ["borrow.json"]}
    cases = [
        ({"provider": "inherit"}, True, "borrow-fixture"),
        ({"provider": "openai", "base_url": "https://borrow.invalid/v1"}, True, "borrow-fixture"),
        ({"provider": "openai", "base_url": "https://other.invalid/v1"}, False, None),
        ({"provider": "anthropic", "model": "fixture-model"}, False, None),
        ({"provider": "inherit", "base_url": "https://other.invalid/v1", "api_key": "own-fixture"}, True, "own-fixture"),
        ({"provider": "openai", "base_url": "https://other.invalid/v1", "api_key_env": "VISION_OWN_FIXTURE"}, True, "own-fixture"),
    ]
    for kwargs, succeeds, expected_key in cases:
        (tmp_path / "init.json").write_text(json.dumps({"llm": {"preset": policy}, "tools": {"vision": kwargs}}))
        calls.clear()
        result, reason, _ = vision.VisionManager._build_service_from_preset(manager, "borrow.json")
        if succeeds:
            assert result is service and not reason
            assert len(calls) == 1 and calls[0][0] == "openai"
            assert calls[0][1]["api_key"] == expected_key
            assert calls[0][1]["base_url"] == kwargs.get("base_url", "https://borrow.invalid/v1")
        else:
            assert result is None and "manual" in reason and calls == []


@pytest.mark.parametrize("raises", [False, True])
def test_inspect_init_restores_logging_boundary(tmp_path, monkeypatch, raises):
    import logging
    from lingtai import cli

    previous = logging.root.manager.disable
    def reader(_root):
        assert logging.root.manager.disable == logging.CRITICAL
        if raises:
            raise RuntimeError("nonsecret-fixture")
        return {"schema": "fixture"}, 0
    monkeypatch.setattr(cli, "_inspect_init", reader)
    if raises:
        with pytest.raises(RuntimeError, match="nonsecret-fixture"):
            cli.inspect_init(tmp_path)
    else:
        assert cli.inspect_init(tmp_path) == ({"schema": "fixture"}, 0)
    assert logging.root.manager.disable == previous


def test_plugin_override_preflight_preserves_old_runtime_and_valid_metadata_activates(tmp_path):
    from unittest.mock import Mock
    from lingtai.services.plugin_registry import MCP_SCHEMA_URL, PLUGIN_SCHEMA_URL

    plugin = tmp_path / "fixture-plugin"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(json.dumps({"$schema": PLUGIN_SCHEMA_URL, "name": "fixture"}))
    (plugin / "mcp.json").write_text(json.dumps({"$schema": MCP_SCHEMA_URL, "mcpServers": {
        "hello": {"type": "stdio", "command": "fixture-only", "args": ["metadata"]}}}))
    document = {"llm": {"provider": "openai", "model": "fixture"},
                "plugins": [str(plugin)], "mcp": {"hello": {}}}
    path = tmp_path / "init.json"
    old_client = Mock()
    old_tools = {"old": object()}
    runtime = SimpleNamespace(_working_dir=tmp_path, _log=Mock(), _mcp_clients=[old_client],
                              _tool_handlers=old_tools, _sealed=True)
    runtime._read_init = lambda: Agent._read_init(runtime)
    for override in ({"type": "http"}, {"command": 17}, {"args": "not-a-list"}):
        document["mcp"]["hello"] = override
        raw = json.dumps(document)
        path.write_text(raw)
        assert not _read(tmp_path).ok
        # Real reconstruction entry and real reader: invalid launcher must
        # return before touching old clients, tools or sealing.
        Agent._setup_from_init(runtime)
        assert runtime._last_init_read_outcome.status.value == "READ_FAILED"
        assert runtime._last_init_read_outcome.behavior == "KEEP_PREVIOUS_EFFECTIVE"
        assert runtime._mcp_clients == [old_client] and runtime._tool_handlers is old_tools
        assert runtime._sealed and path.read_text() == raw
        old_client.close.assert_not_called()
        assert not (tmp_path / "mcp_registry.jsonl").exists()
    document["mcp"]["hello"] = {}
    path.write_text(json.dumps(document))
    assert _read(tmp_path).ok
    calls = []
    client = object()
    mount = SimpleNamespace(_working_dir=tmp_path, _forced_disable=set(), agent_name="fixture", _mcp_clients=[])
    def connect(**kwargs):
        calls.append(kwargs)
        mount._mcp_clients.append(client)
        return []
    mount.connect_mcp = connect
    Agent._load_mcp_from_workdir(mount)
    assert len(calls) == 1 and calls[0]["command"] == "fixture-only"
    assert calls[0]["args"] == ["metadata"]
    assert mount._mcp_init_specs["hello"]["client"] is client


def test_vision_borrow_codex_keeps_own_auth_identity(tmp_path, monkeypatch):
    from lingtai.services import vision as services
    from lingtai.tools import vision

    token_path = str(tmp_path / "nonsecret-not-created.json")
    (tmp_path / "borrow.json").write_text(json.dumps({"description": {"summary": "codex"},
        "llm": {"provider": "codex", "model": "fixture", "codex_auth_path": token_path}}))
    (tmp_path / "init.json").write_text(json.dumps({"llm": {"preset": {
        "active": "borrow.json", "default": "borrow.json", "allowed": ["borrow.json"]}}}))
    calls = []
    marker = object()
    def factory(provider, **kwargs):
        calls.append((provider, kwargs))
        return marker
    monkeypatch.setattr(services, "create_vision_service", factory)
    manager = SimpleNamespace(_workdir=SimpleNamespace(path=tmp_path),
        _active_provider=SimpleNamespace(service=SimpleNamespace(provider="openai")))
    result, reason, _ = vision.VisionManager._build_service_from_preset(manager, "borrow.json")
    assert result is marker and not reason
    assert calls == [("codex", {"api_key": None, "model": "fixture", "token_path": token_path})]
    assert not (tmp_path / "nonsecret-not-created.json").exists()
