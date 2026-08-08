"""End-to-end smoke tests for the mcp capability + addons decompression.

Verifies the vertical slice: addons:["imap"] in init.json triggers catalog
decompression into mcp_registry.jsonl, the mcp capability renders the registry
into the system prompt, and the loader gates init.json mcp activation by
registry membership.
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import pytest

from lingtai.agent import Agent
from lingtai.services.mcp_registry import (
    REGISTRY_FILENAME,
    decompress_addons,
    read_registry,
    validate_record,
)
from tests._service_helpers import make_gemini_mock_service as make_mock_service




def _mk_agent(tmp_path: Path, *, addons=None, capabilities=None):
    workdir = tmp_path / "agent"
    return Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities=capabilities or {"mcp": {}},
        addons=addons,
    ), workdir


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def test_validator_accepts_valid_stdio_record():
    ok, err = validate_record({
        "name": "imap",
        "summary": "test",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "lingtai.mcp_servers.imap"],
        "source": "lingtai-curated",
    })
    assert ok, err


def test_validator_accepts_valid_http_record():
    ok, err = validate_record({
        "name": "remote",
        "summary": "test",
        "transport": "http",
        "url": "https://example.com/mcp",
        "source": "user",
    })
    assert ok, err


def test_validator_accepts_optional_homepage():
    ok, err = validate_record({
        "name": "imap",
        "summary": "test",
        "transport": "stdio",
        "command": "python",
        "args": [],
        "source": "lingtai-curated",
        "homepage": "https://github.com/Lingtai-AI/lingtai-imap",
    })
    assert ok, err


def test_validator_accepts_record_without_homepage():
    ok, err = validate_record({
        "name": "imap",
        "summary": "test",
        "transport": "stdio",
        "command": "python",
        "args": [],
        "source": "user",
    })
    assert ok, err


def test_validator_rejects_empty_homepage():
    ok, err = validate_record({
        "name": "imap",
        "summary": "test",
        "transport": "stdio",
        "command": "python",
        "args": [],
        "source": "user",
        "homepage": "",
    })
    assert not ok
    assert "homepage" in err


def test_validator_rejects_bad_name():
    ok, err = validate_record({
        "name": "BAD-NAME",
        "summary": "x",
        "transport": "stdio",
        "command": "a",
        "args": [],
        "source": "u",
    })
    assert not ok
    assert "invalid name" in err


def test_validator_rejects_bad_transport():
    ok, err = validate_record({
        "name": "x",
        "summary": "y",
        "transport": "smtp",
        "source": "u",
    })
    assert not ok
    assert "invalid transport" in err


def test_validator_rejects_long_summary():
    ok, err = validate_record({
        "name": "x",
        "summary": "a" * 500,
        "transport": "stdio",
        "command": "a",
        "args": [],
        "source": "u",
    })
    assert not ok
    assert "summary too long" in err


def test_nokv_workbench_registry_example_is_valid():
    skill_root = Path("src/lingtai/intrinsic_skills/nokv-workbench")
    registry_path = skill_root / "assets" / "mcp_registry.example.jsonl"
    init_path = skill_root / "assets" / "init-snippet.json"

    record = json.loads(registry_path.read_text(encoding="utf-8").strip())
    ok, err = validate_record(record)
    assert ok, err
    assert record["name"] == "nokv-workbench"
    assert record["transport"] == "stdio"
    assert record["args"] == [
        "--server-bind",
        "127.0.0.1:7777",
        "--object-backend",
        "rustfs",
        "--s3-bucket",
        "nokv-lingtai-workbench",
        "mcp",
        "--profile",
        "workbench",
        "--workbench-root",
        "/agents/{agent_id}/wb",
    ]

    init = json.loads(init_path.read_text(encoding="utf-8"))
    spec = init["mcp"]["nokv-workbench"]
    assert spec["type"] == "stdio"
    assert spec["command"] == record["command"]
    assert spec["args"] == record["args"]


def test_nokv_workbench_skill_documents_durable_restore_contract():
    skill_root = Path("src/lingtai/intrinsic_skills/nokv-workbench")
    skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    preflight = (skill_root / "assets" / "PREFLIGHT.md").read_text(encoding="utf-8")

    assert "version: 0.5.0" in skill
    assert "workbench_restore" in skill
    assert '"at_snapshot": 417' in skill
    assert "same numeric `snapshot_id`" in skill
    assert "resend the exact request" in skill
    assert "No separate LingTai restore state" in skill
    assert "metadata/restore_manifest.json" in skill
    assert "nokv.workbench.restore_manifest.v1" in skill
    assert "restored_from.snapshot_id" in skill
    assert 'Success means `state="complete"`' in skill
    assert "expired checkpoints cannot be renewed" in skill
    assert "lifecycle `state` (`alive`, `expired`, `retired`, or" in skill
    assert "lifecycle `status`" not in skill
    assert "grace window" not in skill

    for code in (
        "SnapshotNotFound",
        "SnapshotLeaseExpired",
        "SnapshotRootMismatch",
        "SnapshotBindingChanged",
        "SnapshotRenewContended",
        "NotOwner",
        "StaleOwnerEpoch",
        "InvalidOwnerEpoch",
        "LeaseExpired",
        "RestoreTransportUnavailable",
        "RestoreInProgress",
        "RestoreRootChanged",
        "RestoreBindingChanged",
        "RestoreProtocolMismatch",
        "RestoreDestinationConflict",
        "RestoreResourceLimit",
        "RestoreHardlinkUnsupported",
        "RestoreCrossShardUnsupported",
        "StalePreparedArtifactObjectGcEpoch",
        "SyncLogArchiveFailed",
        "CapabilityMismatch",
    ):
        assert code in skill

    assert "complete 18-tool restore-capable workbench surface" in preflight
    assert "The base surface has 17 tools" in preflight
    assert '"workbench_snapshot_retire"' in preflight
    assert '"required": ["id", "manifest", "content_digest_uri"]' in preflight
    assert '"metadata": {' in preflight
    assert '"required": ["id", "at_snapshot", "destination_id"]' in preflight
    assert '"additionalProperties": False' in preflight
    assert '"type": "integer", "minimum": 0' in preflight
    assert '"type": "string", "minLength": 1' in preflight
    assert "restore_to_fork_v1" in preflight
    assert "raw schema mismatch" in preflight
    assert "before Agent registration" in preflight
    assert "--profile full --require-all" in preflight
    assert "two real MCP" in preflight
    assert "hard-coded NoKV gate" in preflight


def test_nokv_workbench_docs_pin_write_read_and_lifecycle_contracts():
    skill_root = Path("src/lingtai/intrinsic_skills/nokv-workbench")
    skill = " ".join(
        (skill_root / "SKILL.md").read_text(encoding="utf-8").split()
    )
    preflight = " ".join(
        (skill_root / "assets" / "PREFLIGHT.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert (
        "`replace=false` (the default) is create-only and fails when the "
        "target exists; `replace=true` is replace-only and fails when the "
        "target is missing"
    ) in skill
    assert "It is not upsert" in skill
    assert "NoKV does not natively parse `application/x-ndjson`" in skill
    assert "does not promise NDJSON record pagination" in skill
    assert "A `.jsonl` suffix alone selects no parser" in skill
    assert (
        "write it with a `text/*` content type to receive raw `text_lines` "
        "whose `value.text` you parse yourself"
    ) in skill
    assert "use `format=\"bytes\"` for `application/x-ndjson`" in skill

    assert "`nokv.workbench.run_manifest.v1`" in skill
    assert "`content_digest_uri` before the call" in skill
    assert "different content identity conflicts even when" in skill
    assert "`reason` and `metadata` are bounded registry annotations" in skill
    assert "`SnapshotRegistryWritePartial`" in skill
    assert "Use `workbench_snapshot_retire`" in skill
    assert "returns `retired=false` and does not fabricate deletion attribution" in skill

    assert "complete 18-tool restore-capable workbench surface" in preflight
    assert "The base surface has 17 tools" in preflight
    assert '"workbench_snapshot_retire"' in preflight
    assert '"required": ["id", "manifest", "content_digest_uri"]' in preflight
    assert '"reason": {' in preflight
    assert '"metadata": {' in preflight


def _run_nokv_preflight_contract(monkeypatch, tools):
    preflight_path = Path(
        "src/lingtai/intrinsic_skills/nokv-workbench/assets/PREFLIGHT.md"
    )
    preflight = preflight_path.read_text(encoding="utf-8")
    blocks = re.findall(r"<<'PY'\n(.*?)\nPY", preflight, flags=re.DOTALL)
    code = next(block for block in blocks if "expected_restore_schema" in block)

    class FakeMCPClient:
        def __init__(self, command, args):
            self.command = command
            self.args = args

        def list_tools(self):
            return copy.deepcopy(tools)

        def close(self):
            return None

    monkeypatch.setattr("lingtai.services.mcp.MCPClient", FakeMCPClient)
    monkeypatch.setenv("NOKV_BIN", "/tmp/nokv")
    monkeypatch.setenv("NOKV_MCP_ARGS", "[]")
    exec(compile(code, str(preflight_path), "exec"), {})


def _strict_nokv_preflight_tools():
    names = {
        "workbench_create", "workbench_put_file", "workbench_append",
        "workbench_edit", "workbench_stat", "workbench_list", "workbench_read",
        "workbench_grep", "workbench_search", "workbench_aggregate",
        "workbench_catalog", "workbench_find", "workbench_commit",
        "workbench_snapshot", "workbench_snapshot_renew",
        "workbench_snapshot_retire", "workbench_snapshot_list",
        "workbench_restore",
    }
    commit_schema = {
        "type": "object",
        "required": ["id", "manifest", "content_digest_uri"],
        "properties": {
            "id": {"type": "string"},
            "manifest": {"type": "object"},
            "content_digest_uri": {
                "type": "string",
                "pattern": "^sha256:[0-9a-f]{64}$",
                "description": (
                    "Stable caller-computed digest of the committed content. "
                    "It must be known before this call and exactly match "
                    "sha256:<64 lowercase hex>."
                ),
            },
            "replace": {
                "type": "boolean",
                "description": (
                    "Explicitly replace a different or legacy commit. "
                    "Concurrent identity changes still fail closed. Defaults false."
                ),
            },
        },
        "additionalProperties": False,
    }
    snapshot_schema = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "string"},
            "name": {
                "type": ["string", "null"],
                "description": (
                    "Checkpoint alias matching [A-Za-z0-9_-]{1,64}. Resolves "
                    "to this snapshot in workbench_snapshot_renew, "
                    "workbench_snapshot_list, and at_snapshot reads."
                ),
            },
            "ttl_days": {
                "type": "integer",
                "minimum": 1,
                "maximum": 90,
                "description": (
                    "Lease length in days. Defaults to 7; values above 90 are "
                    "rejected."
                ),
            },
            "reason": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 256,
                "description": (
                    "Optional human-readable checkpoint reason. At most 256 "
                    "Unicode characters and 1024 UTF-8 bytes."
                ),
            },
            "metadata": {
                "type": ["object", "null"],
                "maxProperties": 64,
                "description": (
                    "Optional JSON annotation object. Canonical encoded size "
                    "is at most 4096 bytes, with at most 8 container levels "
                    "and 64 object keys across the complete value."
                ),
            },
        },
        "additionalProperties": False,
    }
    retire_schema = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "snapshot_id": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Snapshot id to retire. Provide exactly one of snapshot_id "
                    "or name."
                ),
            },
            "name": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Checkpoint name to retire. Provide exactly one of "
                    "snapshot_id or name."
                ),
            },
            "reason": {
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 256,
                "description": (
                    "Optional human-readable retirement reason. At most 256 "
                    "Unicode characters and 1024 UTF-8 bytes."
                ),
            },
        },
        "oneOf": [
            {"required": ["snapshot_id"]},
            {"required": ["name"]},
        ],
        "additionalProperties": False,
    }
    restore_schema = {
        "type": "object",
        "required": ["id", "at_snapshot", "destination_id"],
        "properties": {
            "id": {"type": "string", "minLength": 1},
            "at_snapshot": {
                "anyOf": [
                    {"type": "integer", "minimum": 0},
                    {"type": "string", "minLength": 1},
                ]
            },
            "destination_id": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    schemas = {
        "workbench_commit": commit_schema,
        "workbench_snapshot": snapshot_schema,
        "workbench_snapshot_retire": retire_schema,
        "workbench_restore": restore_schema,
    }
    return [
        {"name": name, "schema": schemas.get(name, {})}
        for name in sorted(names)
    ]


def test_nokv_preflight_executes_strict_raw_schema_gate(monkeypatch):
    _run_nokv_preflight_contract(monkeypatch, _strict_nokv_preflight_tools())


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("missing-surface-tool", "NoKV workbench tools missing"),
        ("capability-tool-absent", "NoKV workbench tools missing"),
        ("commit-content-identity", "workbench_commit raw schema mismatch"),
        ("snapshot-annotation", "workbench_snapshot raw schema mismatch"),
        ("retire-target", "workbench_snapshot_retire raw schema mismatch"),
        ("nullable-snapshot", "workbench_restore raw schema mismatch"),
        ("additional-properties", "workbench_restore raw schema mismatch"),
    ],
)
def test_nokv_preflight_rejects_contract_drift(monkeypatch, mutation, message):
    tools = _strict_nokv_preflight_tools()
    if mutation == "missing-surface-tool":
        tools = [tool for tool in tools if tool["name"] != "workbench_read"]
    elif mutation == "capability-tool-absent":
        tools = [tool for tool in tools if tool["name"] != "workbench_restore"]
    elif mutation == "commit-content-identity":
        commit = next(tool for tool in tools if tool["name"] == "workbench_commit")
        commit["schema"]["required"].remove("content_digest_uri")
    elif mutation == "snapshot-annotation":
        snapshot = next(
            tool for tool in tools if tool["name"] == "workbench_snapshot"
        )
        del snapshot["schema"]["properties"]["metadata"]
    elif mutation == "retire-target":
        retire = next(
            tool for tool in tools if tool["name"] == "workbench_snapshot_retire"
        )
        del retire["schema"]["oneOf"]
    else:
        restore = next(tool for tool in tools if tool["name"] == "workbench_restore")
        if mutation == "nullable-snapshot":
            restore["schema"]["properties"]["at_snapshot"]["anyOf"].append(
                {"type": "null"}
            )
        else:
            restore["schema"]["additionalProperties"] = True

    with pytest.raises(SystemExit, match=message):
        _run_nokv_preflight_contract(monkeypatch, tools)


def test_expand_agent_placeholders_scopes_workbench_root(tmp_path):
    # Per-agent root injection: a shared registry template resolves to a root
    # unique to each agent, so agents cannot address each other's workbenches.
    agent, workdir = _mk_agent(tmp_path)  # workdir.name == "agent"
    assert agent._expand_agent_placeholders("/agents/{agent_id}/wb") == "/agents/agent/wb"
    # {agent_address} is an alias for the stable working-dir name.
    assert agent._expand_agent_placeholders("/agents/{agent_address}/wb") == "/agents/agent/wb"
    # {agent_dir} expands to the absolute working directory.
    assert agent._expand_agent_placeholders("{agent_dir}/x") == f"{workdir}/x"
    # Strings without a placeholder and non-strings pass through untouched.
    assert agent._expand_agent_placeholders("--profile") == "--profile"
    assert agent._expand_agent_placeholders(None) is None


class _FakeConnectionClient:
    """Capture transport inputs without opening a subprocess or connection."""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        type(self).instances.append(self)

    def start(self):
        self.started = True

    def list_tools(self):
        return []


def test_connect_mcp_http_expands_url_and_header_values(tmp_path, monkeypatch):
    from lingtai.services import mcp

    class FakeHTTPMCPClient(_FakeConnectionClient):
        instances = []

    monkeypatch.setattr(mcp, "HTTPMCPClient", FakeHTTPMCPClient)
    agent, workdir = _mk_agent(tmp_path)

    agent.connect_mcp_http(
        "https://example.test/agents/{agent_id}/mcp",
        {
            "X-Agent-Dir": "{agent_dir}",
            "X-{agent_id}": "unchanged",
            "X-Literal": "no-placeholders",
        },
    )

    client = FakeHTTPMCPClient.instances[-1]
    assert client.started
    assert client.kwargs == {
        "url": "https://example.test/agents/agent/mcp",
        "headers": {
            "X-Agent-Dir": str(workdir),
            "X-{agent_id}": "unchanged",
            "X-Literal": "no-placeholders",
        },
    }


def test_connect_mcp_stdio_placeholder_expansion_is_unchanged(tmp_path, monkeypatch):
    from lingtai.services import mcp

    class FakeMCPClient(_FakeConnectionClient):
        instances = []

    monkeypatch.setattr(mcp, "MCPClient", FakeMCPClient)
    agent, workdir = _mk_agent(tmp_path)

    agent.connect_mcp(
        "bin/{agent_id}",
        args=["--root", "{agent_dir}", "literal"],
        env={"AGENT": "{agent_id}", "LITERAL": "unchanged"},
    )

    client = FakeMCPClient.instances[-1]
    assert client.started
    assert client.kwargs == {
        "command": "bin/agent",
        "args": ["--root", str(workdir), "literal"],
        "env": {"AGENT": "agent", "LITERAL": "unchanged"},
    }


@pytest.mark.parametrize("transport", ["stdio", "http"])
def test_mcp_boundary_keeps_reasoning_private_unless_server_schema_declares_it(
    tmp_path, monkeypatch, transport
):
    """Host-private rationale crosses only when the server declares it.

    ToolExecutor normalizes model-facing ``reasoning`` to the host-private
    ``_reasoning`` audit key. Native strict LTP-v2 ToolFamilies still receive
    their required public ``reasoning`` field, while ordinary MCP servers must
    not receive undeclared host rationale. Unknown business arguments remain
    untouched so the server can reject them against its own schema.
    """
    from lingtai.kernel.llm.base import ToolCall
    from lingtai.kernel.loop_guard import LoopGuard
    from lingtai.kernel.tool_executor import ToolExecutor
    from lingtai.mcp_servers.telegram.manager import DESCRIPTION, SCHEMA
    from lingtai.services import mcp

    class FakeMCPClient(_FakeConnectionClient):
        instances = []

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.calls: list[tuple[str, dict]] = []

        def list_tools(self):
            return [
                {"name": "telegram", "schema": SCHEMA, "description": DESCRIPTION},
                {
                    "name": "legacy_echo",
                    "schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                    "description": "Legacy flat MCP tool",
                },
                {
                    "name": "open_echo",
                    "schema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "additionalProperties": True,
                    },
                    "description": "Open MCP tool",
                },
                {
                    "name": "schemaless_echo",
                    "schema": {},
                    "description": "MCP tool without an argument schema",
                },
                {
                    "name": "private_echo",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"},
                            "_reasoning": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                    "description": "MCP tool that explicitly declares _reasoning",
                },
            ]

        def call_tool(self, name, args):
            self.calls.append((name, copy.deepcopy(args)))
            return {"status": "ok", "name": name}

    client_class = "MCPClient" if transport == "stdio" else "HTTPMCPClient"
    monkeypatch.setattr(mcp, client_class, FakeMCPClient)
    agent, _ = _mk_agent(tmp_path)
    if transport == "stdio":
        registered = agent.connect_mcp("fake-family-server")
    else:
        registered = agent.connect_mcp_http("https://example.invalid/mcp")
    assert registered == [
        "telegram",
        "legacy_echo",
        "open_echo",
        "schemaless_echo",
        "private_echo",
    ]
    client = FakeMCPClient.instances[-1]

    executor = ToolExecutor(
        dispatch_fn=agent._dispatch_tool,
        make_tool_result_fn=lambda name, result, **_: {
            "name": name,
            "result": result,
        },
        guard=LoopGuard(max_total_calls=10),
        known_tools=set(registered),
        parallel_safe_tools=set(),
    )

    def run(name, args):
        results, intercepted, _ = executor.execute(
            [ToolCall(name=name, args=args, id=f"call-{name}")]
        )
        assert not intercepted
        return results[0]["result"]

    calls = [
        (
            "telegram",
            {"action": "accounts", "input": {}, "reasoning": "inspect accounts"},
        ),
        (
            "legacy_echo",
            {
                "value": "hello",
                "unknown_business_field": "server must validate this",
                "reasoning": "kernel audit metadata",
            },
        ),
        ("open_echo", {"value": "open", "reasoning": "kernel audit metadata"}),
        (
            "schemaless_echo",
            {"value": "schemaless", "reasoning": "kernel audit metadata"},
        ),
        (
            "private_echo",
            {"value": "declared", "reasoning": "server-declared metadata"},
        ),
        ("legacy_echo", {"value": "no reasoning"}),
    ]
    original_calls = copy.deepcopy(calls)
    for name, args in calls:
        run(name, args)

    assert calls == original_calls

    assert client.calls[0] == (
        "telegram",
        {"action": "accounts", "input": {}, "reasoning": "inspect accounts"},
    )
    assert client.calls[1] == (
        "legacy_echo",
        {
            "value": "hello",
            "unknown_business_field": "server must validate this",
        },
    )
    assert client.calls[2] == ("open_echo", {"value": "open"})
    assert client.calls[3] == ("schemaless_echo", {"value": "schemaless"})
    assert client.calls[4] == (
        "private_echo",
        {"value": "declared", "_reasoning": "server-declared metadata"},
    )
    assert client.calls[5] == ("legacy_echo", {"value": "no reasoning"})


# ---------------------------------------------------------------------------
# Decompression
# ---------------------------------------------------------------------------

def test_decompress_appends_known_addon(tmp_path):
    rep = decompress_addons(tmp_path, ["imap"])
    assert rep["appended"] == ["imap"]
    assert rep["skipped"] == []
    records, problems = read_registry(tmp_path)
    assert [r["name"] for r in records] == ["imap"]
    assert problems == []


def test_decompress_is_idempotent(tmp_path):
    decompress_addons(tmp_path, ["imap"])
    rep2 = decompress_addons(tmp_path, ["imap"])
    assert rep2["appended"] == []
    assert rep2["skipped"] == ["imap"]
    records, _ = read_registry(tmp_path)
    assert len(records) == 1  # no duplicate


def test_decompress_unknown_addon_logged_not_raised(tmp_path):
    rep = decompress_addons(tmp_path, ["nonexistent"])
    assert rep["unknown"] == ["nonexistent"]
    assert rep["appended"] == []
    # Registry file may or may not exist — either is fine for unknown-only input.


def test_registry_drops_duplicates_by_name(tmp_path):
    registry = tmp_path / REGISTRY_FILENAME
    rec = {
        "name": "imap",
        "summary": "x",
        "transport": "stdio",
        "command": "a",
        "args": [],
        "source": "u",
    }
    registry.write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n")
    records, problems = read_registry(tmp_path)
    assert len(records) == 1
    assert any("duplicate" in p["error"] for p in problems)


def test_registry_drops_invalid_lines(tmp_path):
    registry = tmp_path / REGISTRY_FILENAME
    valid = json.dumps({
        "name": "imap",
        "summary": "x",
        "transport": "stdio",
        "command": "a",
        "args": [],
        "source": "u",
    })
    registry.write_text(valid + "\n" + "not-json\n" + "{}\n")
    records, problems = read_registry(tmp_path)
    assert len(records) == 1
    assert len(problems) == 2


# ---------------------------------------------------------------------------
# Capability integration
# ---------------------------------------------------------------------------

def test_addons_list_triggers_decompression(tmp_path):
    agent, workdir = _mk_agent(tmp_path, addons=["imap"])
    registry_path = workdir / REGISTRY_FILENAME
    assert registry_path.is_file()
    records, problems = read_registry(workdir)
    assert [r["name"] for r in records] == ["imap"]
    assert problems == []


def test_mcp_capability_renders_registry_into_prompt(tmp_path):
    agent, workdir = _mk_agent(tmp_path, addons=["imap"])
    section = agent._prompt_manager._sections.get("mcp")
    assert section is not None
    body = section.body if hasattr(section, "body") else str(section)
    assert "<registered_mcp>" in body
    assert "imap" in body
    # Catalog ships the imap homepage; render should surface it.
    assert "<homepage>" in body
    assert "github.com/Lingtai-AI/lingtai-imap" in body


def test_addons_dict_still_works_for_legacy(tmp_path):
    """Legacy dict shape should not break — addon load may fail without
    config but the agent must not raise."""
    # Don't actually load IMAP (no config); just ensure the dict path is taken.
    agent, workdir = _mk_agent(tmp_path, addons={})
    # Should construct fine; no decompression should have happened.
    registry_path = workdir / REGISTRY_FILENAME
    assert not registry_path.exists()


def test_mcp_show_action_returns_health_snapshot(tmp_path):
    agent, workdir = _mk_agent(tmp_path, addons=["imap"])
    handler = agent._tool_handlers.get("mcp")
    assert handler is not None
    result = handler({"action": "info", "input": {}, "reasoning": "check registry health"})
    assert result["status"] == "ok"
    assert result["registered_count"] == 1
    assert result["registered"][0]["name"] == "imap"
    assert "mcp_manual" not in result
    manual = handler({"action": "manual", "input": {}, "reasoning": "load mcp guidance"})
    assert manual["status"] == "ok"
    assert "mcp_manual" in manual and manual["mcp_manual"]  # umbrella SKILL.md body


def test_mcp_manual_preserves_tui_command_boundary():
    """The shipped MCP router must not route setup to a retired TUI surface."""
    manual_root = Path(__file__).resolve().parents[1] / "src/lingtai/tools/mcp/manual"
    router = (manual_root / "SKILL.md").read_text(encoding="utf-8")
    curated = (manual_root / "reference/curated-addons.md").read_text(encoding="utf-8")

    assert re.search(r'/addon.{0,120}(?:retired|never recommended)', router, re.I | re.S)
    assert not re.search(r'(?:use|open|run|launch|recommend)[ \t]+`?/addon', router, re.I)
    assert re.search(r'/mcp.{0,140}only current TUI command', router, re.I | re.S)
    assert re.search(r'/mcp.{0,180}read[- ]only', router, re.I | re.S)
    assert re.search(
        r"/mcp.{0,240}(?:not|isn't).{0,90}(?:guided[ \t]+)?(?:setup|configuration)",
        router,
        re.I | re.S,
    )
    assert re.search(
        r'curated addon setup.{0,220}(?:curated-addons.*contract|provider docs)',
        router,
        re.I | re.S,
    )
    assert re.search(r'explicit human authorization', router, re.I)

    # Keep the existing four-step mechanism in the owning reference while the
    # router adds only the TUI boundary and authorization rule.
    assert re.search(r'## The four-step setup', curated)
    for step in (
        r'1[.].*read.*setup docs',
        r'2[.].*init[.]json',
        r'3[.].*config file',
        r"4[.].*system[(]action=.*refresh.*[)]",
    ):
        assert re.search(step, curated, re.I | re.S)


def test_mcp_show_unknown_action_returns_error(tmp_path):
    agent, workdir = _mk_agent(tmp_path, addons=["imap"])
    handler = agent._tool_handlers.get("mcp")
    # These calls deliberately omit the LTP v2 ``input``/``reasoning`` envelope:
    # an unknown/malformed ``action`` must be rejected with mcp's exact
    # unknown-action envelope before any envelope or input validation runs, so
    # these stay shaped exactly as they were pre-ToolFamily-migration.
    result = handler({"action": "register"})  # not supported in slice
    assert result["status"] == "error"
    # Exact model-visible envelope must survive the dispatch-helper migration
    # (issue #513) and the ToolFamily migration.
    assert result == {
        "status": "error",
        "message": "unknown action: 'register', only 'info' or 'manual' is supported",
    }
    # Missing action key renders the empty-string default, not None.
    assert handler({}) == {
        "status": "error",
        "message": "unknown action: '', only 'info' or 'manual' is supported",
    }
    # Invalid JSON can make `action` unhashable (issue #513 blocker): the router
    # must render the unknown-action envelope, not raise TypeError.
    assert handler({"action": []}) == {
        "status": "error",
        "message": "unknown action: [], only 'info' or 'manual' is supported",
    }
    assert handler({"action": {}}) == {
        "status": "error",
        "message": "unknown action: {}, only 'info' or 'manual' is supported",
    }


# ---------------------------------------------------------------------------
# Loader gating
# ---------------------------------------------------------------------------

def test_loader_skips_unregistered_init_mcp(tmp_path, caplog):
    """init.json mcp entry not in registry should be skipped with a warning."""
    workdir = tmp_path / "agent"
    workdir.mkdir(parents=True)
    # Pre-create init.json with an unregistered mcp entry.
    init = {
        "mcp": {
            "rogue": {"type": "stdio", "command": "false", "args": []},
        },
    }
    (workdir / "init.json").write_text(json.dumps(init))

    Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities={"mcp": {}},
        # No addons → registry is empty → rogue should be skipped.
    )

    # We can't easily intercept the kernel logger here, but the registry stays empty
    # and no MCP client should have been added.
    # (The legacy mcp/servers.json path is also untouched.)


# ---------------------------------------------------------------------------
# Failed-MCP retry on refresh — regression for Lingtai-AI/lingtai#34
# ---------------------------------------------------------------------------

class _FakeMCPClient:
    """Minimal stand-in for MCPClient/HTTPMCPClient.

    `is_connected_value` controls health probes; tool list is empty so the
    Agent's tool registration loop is a no-op (no need to fake schemas).
    """

    def __init__(self, is_connected_value: bool):
        self._connected = is_connected_value
        self.closed = False

    def start(self):
        return None

    def is_connected(self) -> bool:
        return self._connected and not self.closed

    def list_tools(self, timeout: float = 10):
        return []

    def close(self):
        self.closed = True


def test_retry_failed_mcps_records_dead_then_recovers(tmp_path, monkeypatch):
    """A registered init.json MCP that boots dead should be retried on
    `_retry_failed_mcps()` and reported as recovered when the second attempt
    succeeds. Regression for Lingtai-AI/lingtai#34."""
    workdir = tmp_path / "agent"
    workdir.mkdir(parents=True)
    # Pre-stage registry so the init.json mcp entry passes the gate.
    (workdir / "mcp_registry.jsonl").write_text(json.dumps({
        "name": "telegram",
        "summary": "test",
        "transport": "stdio",
        "command": "/bin/true",
        "args": [],
        "source": "user",
    }) + "\n")
    init = {
        "mcp": {
            "telegram": {"type": "stdio", "command": "/bin/true", "args": []},
        },
    }
    (workdir / "init.json").write_text(json.dumps(init))

    # Patch connect_mcp on the Agent class: first call → returns dead client
    # (subprocess "exited" immediately); second call → returns live client.
    call_count = {"n": 0}

    def fake_connect_mcp(self, command, args=None, env=None):
        call_count["n"] += 1
        client = _FakeMCPClient(is_connected_value=(call_count["n"] >= 2))
        if not hasattr(self, "_mcp_clients"):
            self._mcp_clients = []
        self._mcp_clients.append(client)
        return []  # no tools to register

    monkeypatch.setattr(Agent, "connect_mcp", fake_connect_mcp)

    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities={"mcp": {}},
    )

    # Boot recorded the spec, but the tracked client is dead.
    assert "telegram" in agent._mcp_init_specs
    boot_client = agent._mcp_init_specs["telegram"]["client"]
    assert boot_client is not None
    assert not boot_client.is_connected()

    # Retry: should detect death, close+remove, respawn — second spawn
    # returns a live client → reported as recovered.
    report = agent._retry_failed_mcps()
    assert "telegram" in report["retried"]
    assert "telegram" in report["recovered"]
    assert report["still_failed"] == []
    # The dead client should have been closed and dropped.
    assert boot_client.closed
    assert boot_client not in agent._mcp_clients
    # New client tracked.
    new_client = agent._mcp_init_specs["telegram"]["client"]
    assert new_client is not None and new_client.is_connected()


def test_retry_failed_mcps_skips_healthy(tmp_path, monkeypatch):
    """A live MCP should be reported as `healthy`, not retried."""
    workdir = tmp_path / "agent"
    workdir.mkdir(parents=True)
    (workdir / "mcp_registry.jsonl").write_text(json.dumps({
        "name": "telegram",
        "summary": "test",
        "transport": "stdio",
        "command": "/bin/true",
        "args": [],
        "source": "user",
    }) + "\n")
    (workdir / "init.json").write_text(json.dumps({
        "mcp": {"telegram": {"type": "stdio", "command": "/bin/true"}},
    }))

    def fake_connect_mcp(self, command, args=None, env=None):
        client = _FakeMCPClient(is_connected_value=True)
        if not hasattr(self, "_mcp_clients"):
            self._mcp_clients = []
        self._mcp_clients.append(client)
        return []

    monkeypatch.setattr(Agent, "connect_mcp", fake_connect_mcp)

    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities={"mcp": {}},
    )

    report = agent._retry_failed_mcps()
    assert report["retried"] == []
    assert report["recovered"] == []
    assert report["still_failed"] == []
    assert "telegram" in report["healthy"]


def test_retry_failed_mcps_no_specs_is_noop(tmp_path):
    """An agent with no init.json mcp entries should return an empty
    report — never raise, never assume `_mcp_init_specs` exists."""
    workdir = tmp_path / "agent"
    workdir.mkdir(parents=True)
    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities={"mcp": {}},
    )
    report = agent._retry_failed_mcps()
    assert report == {"retried": [], "recovered": [],
                      "still_failed": [], "healthy": []}


# ---------------------------------------------------------------------------
# Failed retry must not leave a dead client's routes/metadata behind
# ---------------------------------------------------------------------------

class _ToolFakeMCPClient(_FakeMCPClient):
    """`_FakeMCPClient` that advertises one v2 tool record."""

    def __init__(self, is_connected_value: bool, tool_name: str, title: str):
        super().__init__(is_connected_value)
        self._tool_name = tool_name
        self._title = title

    def list_tools(self, timeout: float = 10):
        return [{
            "name": self._tool_name,
            "description": "fake",
            "schema": {"type": "object", "properties": {}},
            "title": self._title,
        }]

    def call_tool(self, name, args, **kw):
        return {"status": "success"}


def _retry_workdir(tmp_path: Path) -> Path:
    """One registry-gated stdio MCP named `telegram`, as the retry tests use."""
    workdir = tmp_path / "agent"
    workdir.mkdir(parents=True)
    (workdir / "mcp_registry.jsonl").write_text(json.dumps({
        "name": "telegram",
        "summary": "test",
        "transport": "stdio",
        "command": "/bin/true",
        "args": [],
        "source": "user",
    }) + "\n")
    (workdir / "init.json").write_text(json.dumps({
        "mcp": {"telegram": {"type": "stdio", "command": "/bin/true"}},
    }))
    return workdir


def _agent_with_unrelated_mcp_tool(workdir, monkeypatch, *, boot_alive: bool):
    """Boot an agent whose `telegram` MCP registers one real tool.

    Registration goes through the genuine `connect_mcp`, so the reverse-route
    and metadata sidecars are populated the way production populates them. A
    second, unrelated client is then connected to prove pruning is scoped.
    """
    import lingtai.services.mcp as mcp_mod

    monkeypatch.setattr(
        mcp_mod, "MCPClient",
        lambda **kw: _ToolFakeMCPClient(boot_alive, "tg_tool", "Telegram Tool"),
    )
    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities={"mcp": {}},
    )

    monkeypatch.setattr(
        mcp_mod, "MCPClient",
        lambda **kw: _ToolFakeMCPClient(True, "other_tool", "Other Tool"),
    )
    agent.connect_mcp("/bin/true")
    return agent


def test_failed_retry_clears_dead_client_metadata_and_route(tmp_path, monkeypatch):
    """A retry that fails must not leave the closed client looking alive."""
    workdir = _retry_workdir(tmp_path)
    agent = _agent_with_unrelated_mcp_tool(workdir, monkeypatch, boot_alive=False)

    assert agent.mcp_tool_metadata("tg_tool") == {"title": "Telegram Tool"}
    dead = agent._mcp_init_specs["telegram"]["client"]
    assert agent._mcp_clients_by_tool["tg_tool"] is dead

    # Reconnect raises, so the retry ends in the `still_failed` branch.
    def _boom(self, command, args=None, env=None):
        raise RuntimeError("spawn refused")

    monkeypatch.setattr(Agent, "connect_mcp", _boom)
    report = agent._retry_failed_mcps()

    assert report["still_failed"] == ["telegram"]
    assert dead.closed
    assert agent.mcp_tool_metadata("tg_tool") is None
    assert "tg_tool" not in agent._mcp_clients_by_tool


def test_failed_retry_preserves_an_unrelated_clients_metadata_and_route(
    tmp_path, monkeypatch,
):
    """Pruning is scoped to the dead client; other servers stay usable."""
    workdir = _retry_workdir(tmp_path)
    agent = _agent_with_unrelated_mcp_tool(workdir, monkeypatch, boot_alive=False)
    other_client = agent._mcp_clients_by_tool["other_tool"]

    def _boom(self, command, args=None, env=None):
        raise RuntimeError("spawn refused")

    monkeypatch.setattr(Agent, "connect_mcp", _boom)
    agent._retry_failed_mcps()

    assert agent.mcp_tool_metadata("other_tool") == {"title": "Other Tool"}
    assert agent._mcp_clients_by_tool["other_tool"] is other_client


def test_successful_retry_publishes_replacement_metadata(tmp_path, monkeypatch):
    """A recovered server's metadata is the new client's, not the dead one's."""
    workdir = _retry_workdir(tmp_path)
    agent = _agent_with_unrelated_mcp_tool(workdir, monkeypatch, boot_alive=False)
    dead = agent._mcp_init_specs["telegram"]["client"]

    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(
        mcp_mod, "MCPClient",
        lambda **kw: _ToolFakeMCPClient(True, "tg_tool", "Telegram Tool v2"),
    )
    report = agent._retry_failed_mcps()

    assert report["recovered"] == ["telegram"]
    assert agent.mcp_tool_metadata("tg_tool") == {"title": "Telegram Tool v2"}
    assert agent._mcp_clients_by_tool["tg_tool"] is not dead
    # The unrelated server is untouched by the recovery.
    assert agent.mcp_tool_metadata("other_tool") == {"title": "Other Tool"}


def test_unhealthy_replacement_is_torn_down_not_left_published(
    tmp_path, monkeypatch,
):
    """A retry that registers a replacement then fails its health test.

    `_ToolFakeMCPClient` already separates `is_connected_value` from
    `list_tools`, so a replacement can register tools/metadata and still report
    unhealthy — the exact branch where the spawn returns without raising but
    the client is dead. That replacement must not stay published.
    """
    workdir = _retry_workdir(tmp_path)
    agent = _agent_with_unrelated_mcp_tool(workdir, monkeypatch, boot_alive=False)
    other_client = agent._mcp_clients_by_tool["other_tool"]

    replacements: list = []

    def _unhealthy_replacement(**kw):
        client = _ToolFakeMCPClient(False, "tg_tool", "Telegram Tool v2")
        replacements.append(client)
        return client

    import lingtai.services.mcp as mcp_mod
    monkeypatch.setattr(mcp_mod, "MCPClient", _unhealthy_replacement)

    report = agent._retry_failed_mcps()

    assert report["still_failed"] == ["telegram"]
    assert report["recovered"] == []
    # The replacement registered tools, then failed the health test: it must be
    # closed, de-listed, and unpublished rather than left looking alive.
    replacement = replacements[-1]
    assert replacement.closed
    assert replacement not in agent._mcp_clients
    assert agent.mcp_tool_metadata("tg_tool") is None
    assert "tg_tool" not in agent._mcp_clients_by_tool
    assert agent._mcp_init_specs["telegram"]["client"] is None
    # An unrelated server is untouched by this teardown.
    assert agent.mcp_tool_metadata("other_tool") == {"title": "Other Tool"}
    assert agent._mcp_clients_by_tool["other_tool"] is other_client


def test_curated_catalog_includes_whatsapp(tmp_path: Path):
    rep = decompress_addons(tmp_path, ["whatsapp"])
    assert rep["appended"] == ["whatsapp"]
    records, problems = read_registry(tmp_path)
    assert problems == []
    assert records[0]["name"] == "whatsapp"
    assert records[0]["args"] == ["-m", "lingtai.mcp_servers.whatsapp"]
    assert records[0]["homepage"] == "https://github.com/Lingtai-AI/lingtai-whatsapp"


def test_curated_mcp_modules_ship_inside_lingtai_distribution():
    """Curated MCPs ship from the canonical kernel distribution package."""
    import importlib
    from importlib import resources

    modules = {
        "imap": "lingtai.mcp_servers.imap",
        "telegram": "lingtai.mcp_servers.telegram",
        "feishu": "lingtai.mcp_servers.feishu",
        "wechat": "lingtai.mcp_servers.wechat",
        "whatsapp": "lingtai.mcp_servers.whatsapp",
        "cloud_mail": "lingtai.mcp_servers.cloud_mail",
    }
    for module in modules.values():
        imported = importlib.import_module(module)
        assert imported is not None

    for module in (
        "lingtai.mcp_servers.telegram",
        "lingtai.mcp_servers.feishu",
        "lingtai.mcp_servers.wechat",
        "lingtai.mcp_servers.whatsapp",
    ):
        header = resources.files(module).joinpath("notification_header.md")
        assert header.is_file()
        assert header.read_text(encoding="utf-8").strip()


def test_curated_mcp_catalog_launches_embedded_modules(tmp_path: Path):
    modules = {
        "imap": "lingtai.mcp_servers.imap",
        "telegram": "lingtai.mcp_servers.telegram",
        "feishu": "lingtai.mcp_servers.feishu",
        "wechat": "lingtai.mcp_servers.wechat",
        "whatsapp": "lingtai.mcp_servers.whatsapp",
    }
    rep = decompress_addons(tmp_path, list(modules))
    assert rep["appended"] == list(modules)
    records, problems = read_registry(tmp_path)
    assert problems == []
    by_name = {r["name"]: r for r in records}
    for name, module in modules.items():
        assert by_name[name]["command"] == sys.executable
        assert by_name[name]["args"] == ["-m", module]
        assert by_name[name]["source"] == "lingtai-curated"
