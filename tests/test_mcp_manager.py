"""Tests for the minimal 3-action mcp control-plane manager.

Jason rejected the thick 8-action manager. The surviving surface is exactly
``list`` / ``add`` / ``remove``:

- ``list``   — read-only: registry summary + init.json activation summary,
               secrets redacted, no manual body.
- ``add``    — register an MCP AND activate it in init.json in one step.
- ``remove`` — deregister an MCP AND strip its init.json activation in one step.

``add``/``remove`` edit desired state only and return ``needs_refresh`` with an
explicit ``system(action="refresh")`` reminder. Refresh belongs to the
``system`` tool, not ``mcp``. The runtime loader / transport / seal model are
untouched. ``show``/``diagnose``/``validate``/``enable``/``disable`` are gone.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lingtai.agent import Agent
from lingtai.core.mcp import REGISTRY_FILENAME, get_schema, read_registry


def make_mock_service():
    svc = MagicMock()
    svc.get_adapter.return_value = MagicMock()
    svc.provider = "gemini"
    svc.model = "gemini-test"
    return svc


def _mk_agent(tmp_path: Path, *, addons=None):
    workdir = tmp_path / "agent"
    agent = Agent(
        service=make_mock_service(),
        agent_name="test",
        working_dir=workdir,
        capabilities={"mcp": {}},
        addons=addons,
    )
    return agent, workdir


def _handler(agent):
    h = agent._tool_handlers.get("mcp")
    assert h is not None
    return h


def _write_registry(workdir: Path, *records: dict) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / REGISTRY_FILENAME
    path.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )


def _stdio_record(name="srv", **over):
    rec = {
        "name": name,
        "summary": "test server",
        "transport": "stdio",
        "command": "/bin/true",
        "args": [],
        "source": "user",
    }
    rec.update(over)
    return rec


def _http_record(name="remote", **over):
    rec = {
        "name": name,
        "summary": "remote server",
        "transport": "http",
        "url": "https://example.com/mcp",
        "source": "user",
    }
    rec.update(over)
    return rec


def _refresh_reminder_present(message: str) -> bool:
    return 'system(action="refresh")' in message


# ---------------------------------------------------------------------------
# Schema — exactly list/add/remove, nothing else.
# ---------------------------------------------------------------------------

def test_schema_enum_is_exactly_list_add_remove():
    enum = get_schema()["properties"]["action"]["enum"]
    assert enum == ["list", "add", "remove"]


@pytest.mark.parametrize("gone", ["show", "diagnose", "validate", "enable", "disable"])
def test_deleted_actions_not_in_schema(gone):
    enum = get_schema()["properties"]["action"]["enum"]
    assert gone not in enum


@pytest.mark.parametrize(
    "gone", ["show", "diagnose", "validate", "enable", "disable", "apply_refresh", "frobnicate"]
)
def test_deleted_action_returns_error(tmp_path, gone):
    agent, _ = _mk_agent(tmp_path)
    result = _handler(agent)({"action": gone})
    assert result["status"] == "error"
    assert "unknown action" in result["message"]


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

def test_list_reports_registry_and_activation(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    (workdir / "init.json").write_text(json.dumps({
        "mcp": {"srv": {"type": "stdio", "command": "x"}},
    }))
    result = _handler(agent)({"action": "list"})
    assert result["status"] == "ok"
    names = [r["name"] for r in result["registry"]]
    assert "srv" in names
    # activation summary present and cross-references init.json
    assert "activation" in result
    assert "srv" in result["activation"]["enabled"]


def test_list_has_no_manual_body(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    result = _handler(agent)({"action": "list"})
    assert "mcp_manual" not in result


def test_list_redacts_secrets(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    (workdir / "init.json").write_text(json.dumps({
        "mcp": {
            "srv": {
                "type": "stdio",
                "command": "x",
                "env": {"API_KEY": "super-secret-value"},
                "token": "tok-secret",
            },
        },
    }))
    result = _handler(agent)({"action": "list"})
    blob = json.dumps(result)
    assert "super-secret-value" not in blob
    assert "tok-secret" not in blob
    assert "<redacted>" in blob


def test_list_does_not_echo_raw_problem_lines(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / REGISTRY_FILENAME).write_text(
        (
            '{"name":"bad","summary":"x","transport":"stdio",'
            '"command":"x","source":"s","env":{"API_KEY":"LEAK_RAW"}}\n'
            '{not json with LEAK_RAW_TOO}\n'
        ),
        encoding="utf-8",
    )
    result = _handler(agent)({"action": "list"})
    blob = json.dumps(result)
    assert "LEAK_RAW" not in blob
    assert "LEAK_RAW_TOO" not in blob
    assert '"raw"' not in blob  # the raw problem-line key is never echoed


def test_list_does_not_echo_non_object_activation_value(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    (workdir / "init.json").write_text(json.dumps({
        "mcp": {"srv": "SECRET_STRING_SHOULD_NOT_LEAK"},
    }))
    result = _handler(agent)({"action": "list"})
    blob = json.dumps(result)
    assert "SECRET_STRING_SHOULD_NOT_LEAK" not in blob
    assert "activation config is not an object" in blob


# ---------------------------------------------------------------------------
# add — registers registry record + writes init.json activation in one step.
# ---------------------------------------------------------------------------

def test_add_from_catalog_writes_registry_and_init(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    result = _handler(agent)({"action": "add", "name": "imap"})
    assert result["status"] == "ok"
    assert result["needs_refresh"] is True
    assert _refresh_reminder_present(result["message"])

    # registry got the record
    records, _ = read_registry(workdir)
    assert "imap" in [r["name"] for r in records]
    imap = next(r for r in records if r["name"] == "imap")
    # {python} substitution must have happened (catalog uses {python})
    assert "{python}" not in imap["command"]

    # init.json got the activation in the same action
    init = json.loads((workdir / "init.json").read_text())
    assert "imap" in init["mcp"]
    assert init["mcp"]["imap"]["type"] == "stdio"


def test_add_explicit_record_derives_init_config(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    rec = _stdio_record("srv", command="/usr/bin/server", args=["--flag"])
    result = _handler(agent)({"action": "add", "record": rec})
    assert result["status"] == "ok"
    assert result["needs_refresh"] is True
    assert _refresh_reminder_present(result["message"])

    records, _ = read_registry(workdir)
    assert "srv" in [r["name"] for r in records]

    init = json.loads((workdir / "init.json").read_text())
    entry = init["mcp"]["srv"]
    assert entry["type"] == "stdio"
    assert entry["command"] == "/usr/bin/server"
    assert entry["args"] == ["--flag"]


def test_add_http_record_derives_url(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    result = _handler(agent)({"action": "add", "record": _http_record("remote")})
    assert result["status"] == "ok"
    init = json.loads((workdir / "init.json").read_text())
    entry = init["mcp"]["remote"]
    assert entry["type"] == "http"
    assert entry["url"] == "https://example.com/mcp"


def test_add_with_explicit_config_overrides_derived(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    cfg = {"type": "stdio", "command": "/custom", "args": ["-x"], "env": {"K": "v"}}
    result = _handler(agent)({"action": "add", "record": _stdio_record("srv"), "config": cfg})
    assert result["status"] == "ok"
    init = json.loads((workdir / "init.json").read_text())
    assert init["mcp"]["srv"]["command"] == "/custom"


def test_add_preserves_other_init_keys(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "init.json").write_text(json.dumps({
        "provider": "anthropic",
        "mcp": {"other": {"type": "stdio", "command": "x"}},
    }))
    result = _handler(agent)({"action": "add", "record": _stdio_record("srv")})
    assert result["status"] == "ok"
    init = json.loads((workdir / "init.json").read_text())
    assert init["provider"] == "anthropic"
    assert "other" in init["mcp"]
    assert "srv" in init["mcp"]


def test_add_rejects_duplicate(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    result = _handler(agent)({"action": "add", "record": _stdio_record("srv")})
    assert result["status"] == "error"
    assert "duplicate" in result["message"].lower() or "exists" in result["message"].lower()
    records, _ = read_registry(workdir)
    assert len(records) == 1
    # no init.json activation should have leaked in
    assert not (workdir / "init.json").exists() or \
        "srv" not in json.loads((workdir / "init.json").read_text()).get("mcp", {})


def test_add_invalid_record_rejected(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    result = _handler(agent)({"action": "add", "record": _stdio_record("BAD-NAME")})
    assert result["status"] == "error"
    records, _ = read_registry(workdir)
    assert records == []
    assert not (workdir / "init.json").exists()


def test_add_unknown_catalog_name_rejected(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    result = _handler(agent)({"action": "add", "name": "no-such-catalog-entry"})
    assert result["status"] == "error"
    records, _ = read_registry(workdir)
    assert records == []


def test_add_rejects_invalid_init_json_without_overwriting(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "init.json").write_text("{not json", encoding="utf-8")
    result = _handler(agent)({"action": "add", "record": _stdio_record("srv")})
    assert result["status"] == "error"
    assert "invalid init.json" in result["message"]
    # init.json untouched AND registry untouched — no partial write
    assert (workdir / "init.json").read_text(encoding="utf-8") == "{not json"
    records, _ = read_registry(workdir)
    assert records == []


# ---------------------------------------------------------------------------
# remove — drops registry record + strips init.json activation in one step.
# ---------------------------------------------------------------------------

def test_remove_drops_registry_and_init_activation(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"), _stdio_record("keep"))
    (workdir / "init.json").write_text(json.dumps({
        "provider": "anthropic",
        "mcp": {
            "srv": {"type": "stdio", "command": "x"},
            "keep": {"type": "stdio", "command": "y"},
        },
    }))
    result = _handler(agent)({"action": "remove", "name": "srv"})
    assert result["status"] == "ok"
    assert result["needs_refresh"] is True
    assert _refresh_reminder_present(result["message"])

    records, _ = read_registry(workdir)
    names = [r["name"] for r in records]
    assert "srv" not in names
    assert "keep" in names

    init = json.loads((workdir / "init.json").read_text())
    assert "srv" not in init["mcp"]
    assert "keep" in init["mcp"]
    assert init["provider"] == "anthropic"


def test_remove_also_strips_addons_entry(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("imap"))
    (workdir / "init.json").write_text(json.dumps({
        "addons": ["imap", "telegram"],
        "mcp": {"imap": {"type": "stdio", "command": "x"}},
    }))
    result = _handler(agent)({"action": "remove", "name": "imap"})
    assert result["status"] == "ok"
    init = json.loads((workdir / "init.json").read_text())
    assert "imap" not in init.get("addons", [])
    assert "telegram" in init["addons"]
    assert "imap" not in init.get("mcp", {})


def test_remove_works_when_no_init_activation(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    result = _handler(agent)({"action": "remove", "name": "srv"})
    assert result["status"] == "ok"
    assert result["needs_refresh"] is True
    records, _ = read_registry(workdir)
    assert "srv" not in [r["name"] for r in records]


def test_remove_missing_name_errors_cleanly(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("keep"))
    result = _handler(agent)({"action": "remove", "name": "ghost"})
    assert result["status"] == "error"
    # registry untouched
    records, _ = read_registry(workdir)
    assert [r["name"] for r in records] == ["keep"]


def test_remove_blank_name_errors(tmp_path):
    agent, _ = _mk_agent(tmp_path)
    result = _handler(agent)({"action": "remove", "name": ""})
    assert result["status"] == "error"


def test_remove_rejects_invalid_init_json_without_overwriting(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    _write_registry(workdir, _stdio_record("srv"))
    (workdir / "init.json").write_text("{not json", encoding="utf-8")
    result = _handler(agent)({"action": "remove", "name": "srv"})
    assert result["status"] == "error"
    assert "invalid init.json" in result["message"]
    # init.json untouched AND registry record still present — no partial write
    assert (workdir / "init.json").read_text(encoding="utf-8") == "{not json"
    records, _ = read_registry(workdir)
    assert "srv" in [r["name"] for r in records]


# ---------------------------------------------------------------------------
# Audit logging — mutations logged, secrets never logged.
# ---------------------------------------------------------------------------

def test_mutating_actions_are_logged(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    events = []
    agent._log = lambda event_type, **f: events.append((event_type, f))  # type: ignore
    _handler(agent)({"action": "add", "record": _stdio_record("srv")})
    logged = [e for e in events if e[0] == "mcp_manager_action"]
    assert logged
    assert logged[0][1]["action"] == "add"
    assert logged[0][1]["name"] == "srv"


def test_logging_does_not_leak_secrets(tmp_path):
    agent, workdir = _mk_agent(tmp_path)
    events = []
    agent._log = lambda event_type, **f: events.append((event_type, f))  # type: ignore
    cfg = {"type": "stdio", "command": "x", "env": {"API_KEY": "secret-xyz"}}
    _handler(agent)({"action": "add", "record": _stdio_record("srv"), "config": cfg})
    blob = json.dumps(events)
    assert "secret-xyz" not in blob
