"""Shared contract and production-composition tests for structured events."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lingtai.adapters.posix.event_journal import PosixJsonlEventJournalAdapter
from lingtai.agent import Agent
from lingtai.kernel import BaseAgent
from lingtai.kernel.config import AgentConfig
from lingtai.kernel.event_journal import EventJournalPort, JournalPosition
from lingtai.kernel.services.logging import JSONLLoggingService, SQLiteEventIndex
from lingtai.tools.registry import CORE_DEFAULTS, INTRINSICS as _TEST_INTRINSICS
from tests._service_helpers import make_tool_result_mock_service as make_mock_service
from tests._workdir_lease_helpers import make_test_lease


PRODUCTION_ADAPTER_FACTORIES = [
    pytest.param(
        lambda working_dir, **kwargs: PosixJsonlEventJournalAdapter(
            working_dir, **kwargs
        ),
        id="posix-jsonl-sqlite",
    )
]


@pytest.fixture(params=PRODUCTION_ADAPTER_FACTORIES)
def journal_factory(request):
    return request.param


def _events_path(working_dir: Path) -> Path:
    return working_dir / "logs" / "events.jsonl"


def _sqlite_rows(working_dir: Path, sql: str) -> list[tuple]:
    with sqlite3.connect(working_dir / "logs" / "log.sqlite") as conn:
        return list(conn.execute(sql))


def test_production_adapter_append_order_offsets_and_flush_visibility(
    tmp_path, journal_factory
):
    journal: EventJournalPort = journal_factory(tmp_path, ensure_ascii=False)
    first = {"type": "first", "ts": 1, "value": "雪"}
    second = {"type": "second", "ts": 2, "value": 2}

    first_position = journal.append(first)
    assert first_position == JournalPosition(
        source_file=str(_events_path(tmp_path)), source_offset=0
    )
    first_bytes = _events_path(tmp_path).read_bytes()
    assert json.loads(first_bytes) == first

    second_position = journal.append(second)
    assert second_position == JournalPosition(
        source_file=str(_events_path(tmp_path)), source_offset=len(first_bytes)
    )
    records = [json.loads(line) for line in _events_path(tmp_path).read_bytes().splitlines()]
    assert records == [first, second]
    assert _sqlite_rows(
        tmp_path, "SELECT type, source_offset FROM events ORDER BY source_offset"
    ) == [("first", 0), ("second", len(first_bytes))]
    journal.close()


def test_production_adapter_close_is_idempotent_and_append_after_close_is_inert(
    tmp_path, journal_factory
):
    journal: EventJournalPort = journal_factory(tmp_path)
    assert journal.append({"type": "before_close", "ts": 1}) is not None
    journal.close()
    journal.close()
    primary_before = _events_path(tmp_path).read_bytes()
    sidecar_before = _sqlite_rows(tmp_path, "SELECT COUNT(*) FROM events")

    assert journal.append({"type": "after_close", "ts": 2}) is None
    assert _events_path(tmp_path).read_bytes() == primary_before
    assert _sqlite_rows(tmp_path, "SELECT COUNT(*) FROM events") == sidecar_before


def test_production_adapter_redacts_before_primary_and_sidecar_storage(
    tmp_path, journal_factory
):
    journal: EventJournalPort = journal_factory(tmp_path)
    secret = "correct-horse-battery-staple"
    journal.append(
        {
            "type": "tool_result",
            "ts": 1,
            "tool_args": {"password": secret},
        }
    )
    journal.close()

    primary = _events_path(tmp_path).read_text(encoding="utf-8")
    assert secret not in primary
    assert json.loads(primary)["tool_args"]["password"] == "<REDACTED:secret>"
    [(fields_json,)] = _sqlite_rows(
        tmp_path, "SELECT fields_json FROM events WHERE type='tool_result'"
    )
    assert secret not in fields_json
    assert json.loads(fields_json)["tool_args"]["password"] == "<REDACTED:secret>"


def test_primary_failure_propagates_without_sidecar_only_fact(
    tmp_path, journal_factory, monkeypatch
):
    sidecar_calls: list[dict] = []

    def fail_primary(self, event):
        raise OSError("primary unavailable")

    def observe_sidecar(self, event, **kwargs):
        sidecar_calls.append(event)

    monkeypatch.setattr(JSONLLoggingService, "log", fail_primary)
    monkeypatch.setattr(SQLiteEventIndex, "log_event", observe_sidecar)
    journal: EventJournalPort = journal_factory(tmp_path)

    with pytest.raises(OSError, match="primary unavailable"):
        journal.append({"type": "must_not_index", "ts": 1})
    assert sidecar_calls == []
    assert _events_path(tmp_path).read_bytes() == b""
    assert not (tmp_path / "logs" / "log.sqlite").exists()
    journal.close()


def test_sqlite_failure_disables_sidecar_while_jsonl_continues(
    tmp_path, journal_factory, monkeypatch
):
    journal: EventJournalPort = journal_factory(tmp_path)

    def fail_sqlite(self, **kwargs):
        raise sqlite3.OperationalError("sidecar unavailable")

    monkeypatch.setattr(SQLiteEventIndex, "_ensure_open", fail_sqlite)
    first = journal.append({"type": "first", "ts": 1})
    second = journal.append({"type": "second", "ts": 2})
    journal.close()

    assert first is not None
    assert second is not None
    assert [
        json.loads(line)["type"]
        for line in _events_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ] == ["first", "second"]


def test_raw_base_agent_has_no_hidden_posix_journal(tmp_path):
    agent = BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        agent_name="core-only",
        working_dir=tmp_path,
        workdir_lease=make_test_lease(),
    )
    try:
        assert agent._event_journal is None
        agent._log("core_event")
        assert not _events_path(tmp_path).exists()
    finally:
        agent._workdir_lease.release()


def test_base_agent_uses_none_as_the_only_disabled_journal_sentinel(tmp_path):
    class FalseyJournal:
        def __init__(self):
            self.events: list[dict] = []

        def __bool__(self):
            return False

        def append(self, event):
            self.events.append(event)
            return None

        def close(self):
            return None

    journal = FalseyJournal()
    agent = BaseAgent(
        intrinsics=_TEST_INTRINSICS,
        service=make_mock_service(),
        working_dir=tmp_path,
        event_journal=journal,
        workdir_lease=make_test_lease(),
    )
    journal.events.clear()
    try:
        agent._log("falsey_journal")
        assert [event["type"] for event in journal.events] == ["falsey_journal"]
    finally:
        agent._workdir_lease.release()


def test_outer_agent_composes_posix_journal_for_keyword_working_dir(tmp_path):
    config = AgentConfig(ensure_ascii=True)
    agent = Agent(
        service=make_mock_service(),
        working_dir=tmp_path,
        capabilities={},
        disable=list(CORE_DEFAULTS),
        file_io=MagicMock(),
        config=config,
    )
    try:
        assert isinstance(agent._event_journal, PosixJsonlEventJournalAdapter)
        agent._log("unicode", value="雪")
        assert "\\u96ea" in _events_path(tmp_path).read_text(encoding="utf-8")
    finally:
        agent._event_journal.close()
        agent._workdir_lease.release()


def test_cli_build_agent_explicitly_injects_production_adapter(tmp_path, monkeypatch):
    import lingtai.cli as cli

    captured: dict = {}

    class FakeAgent:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self._molt_count = 0

        def _setup_from_init(self):
            return None

    monkeypatch.setattr(cli, "Agent", FakeAgent)
    monkeypatch.setattr(cli, "LLMService", lambda **kwargs: object())
    monkeypatch.setattr(cli, "PosixFilesystemMailAdapter", lambda **kwargs: object())
    monkeypatch.setattr(
        cli, "build_provider_defaults_from_manifest_llm", lambda *args, **kwargs: {}
    )
    data = {
        "manifest": {
            "llm": {"provider": "test", "model": "test-model"},
            "agent_name": "cli-agent",
        }
    }

    cli.build_agent(data, tmp_path)
    journal = captured["event_journal"]
    try:
        assert isinstance(journal, PosixJsonlEventJournalAdapter)
        journal.append({"type": "unicode", "value": "雪"})
        assert "雪" in _events_path(tmp_path).read_text(encoding="utf-8")
    finally:
        journal.close()


def test_base_agent_source_is_concrete_storage_free_and_uses_port_methods():
    source = Path(__import__("lingtai.kernel.base_agent", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "PosixJsonlEventJournalAdapter",
        "CompositeLoggingService",
        "JSONLLoggingService",
        "SQLiteEventIndex",
    ):
        assert forbidden not in source
    assert "self._event_journal.append(" in source
    assert "self._event_journal = event_journal" in source
