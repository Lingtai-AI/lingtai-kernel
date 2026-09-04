"""Focused contract tests for the channel-neutral local command core."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lingtai.mcp_servers.local_commands import (
    DEFAULT_COMMANDS,
    HIDDEN_COMMANDS,
    LocalCommandCore,
    TaskCardSettingsPort,
)
from lingtai.mcp_servers.telegram.service import TelegramService


def _taskcard_settings(
    state: dict[str, object],
    *,
    fail: bool = False,
) -> TaskCardSettingsPort:
    def set_enabled(value: bool) -> None:
        if fail:
            raise OSError("persist failed")
        state["enabled"] = value

    def set_normal_rows(value: int) -> None:
        if fail:
            raise OSError("persist failed")
        state["normal_rows"] = value

    return TaskCardSettingsPort(
        enabled=lambda: bool(state["enabled"]),
        set_enabled=set_enabled,
        normal_rows=lambda: int(state["normal_rows"]),
        set_normal_rows=set_normal_rows,
    )


def test_command_catalog_preserves_telegram_menu_and_hidden_order() -> None:
    assert [item["command"] for item in DEFAULT_COMMANDS] == [
        "help",
        "kanban",
        "taskcard",
        "refresh",
        "sleep",
        "clear",
    ]
    assert [item["command"] for item in HIDDEN_COMMANDS] == [
        "status",
        "system",
    ]


def test_taskcard_parser_updates_semantics_without_rendering(tmp_path: Path) -> None:
    core = LocalCommandCore(tmp_path)
    state: dict[str, object] = {"enabled": True, "normal_rows": 1}
    settings = _taskcard_settings(state)

    result = core.apply_taskcard("/taskcard@SomeBot off", settings)
    assert result.status == "ok"
    assert result.enabled is False
    assert result.normal_rows == 1

    result = core.apply_taskcard("/taskcard 7", settings)
    assert result.status == "ok"
    assert result.enabled is False
    assert result.normal_rows == 7

    for text in (
        "/taskcard help",
        "/taskcard 0",
        "/taskcard 11",
        "/taskcard ２",
        "/taskcard on extra",
    ):
        assert core.apply_taskcard(text, settings).status == "usage"
    assert state == {"enabled": False, "normal_rows": 7}


def test_taskcard_parser_reports_injected_persistence_failure(tmp_path: Path) -> None:
    core = LocalCommandCore(tmp_path)
    state: dict[str, object] = {"enabled": True, "normal_rows": 1}

    result = core.apply_taskcard(
        "/taskcard off",
        _taskcard_settings(state, fail=True),
    )

    assert result.status == "update_failed"
    assert state == {"enabled": True, "normal_rows": 1}


def test_signal_results_preserve_existing_filesystem_protocol(tmp_path: Path) -> None:
    core = LocalCommandCore(tmp_path)
    stale = tmp_path / ".refresh.taken"
    stale.write_text("old", encoding="utf-8")

    assert core.send_signal("refresh", source="telegram").status == "sent"
    assert (tmp_path / ".refresh").read_text(encoding="utf-8") == ""
    assert not stale.exists()

    stale.write_text("old", encoding="utf-8")
    assert core.send_signal("refresh", source="telegram").status == "pending"
    assert not stale.exists()

    assert core.send_signal("sleep", source="telegram").status == "sent"
    assert (tmp_path / ".sleep").read_text(encoding="utf-8") == ""
    assert core.send_signal("clear", source="telegram").status == "sent"
    assert (tmp_path / ".clear").read_text(encoding="utf-8") == "telegram\n"


def test_signal_and_reads_report_missing_agent_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LINGTAI_AGENT_DIR", raising=False)
    core = LocalCommandCore()

    assert core.send_signal("refresh", source="telegram").status == "agent_dir_missing"
    assert core.system_documents().status == "agent_dir_missing"
    assert core.collect_kanban_data() is None


def test_system_documents_return_neutral_listing_and_filtered_content(
    tmp_path: Path,
) -> None:
    core = LocalCommandCore(tmp_path)
    assert core.system_documents().status == "directory_missing"
    system = tmp_path / "system"
    system.mkdir()
    assert core.system_documents().status == "empty"
    (system / "principle.md").write_text("principle body", encoding="utf-8")
    (system / "system.md").write_text("system body", encoding="utf-8")

    listing = core.system_documents()
    assert listing.status == "ok"
    assert [(item.name, item.content) for item in listing.documents] == [
        ("principle", None),
        ("system", None),
    ]

    selected = core.system_documents("PRINC")
    assert selected.status == "ok"
    assert [(item.name, item.content) for item in selected.documents] == [
        ("principle", "principle body"),
    ]
    assert core.system_documents("missing").status == "no_match"


def test_kanban_snapshot_preserves_shared_dashboard_values(tmp_path: Path) -> None:
    (tmp_path / "init.json").write_text(
        json.dumps(
            {
                "addons": ["telegram", "feishu"],
                "manifest": {
                    "llm": {"model": "test-model", "provider": "test-provider"},
                    "context_limit": 4096,
                    "language": "zh",
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".agent.json").write_text(
        json.dumps({"agent_id": "agent-1", "molt_count": 2}),
        encoding="utf-8",
    )
    (tmp_path / "system").mkdir(exist_ok=True)
    (tmp_path / "system" / "agent_record.json").write_text(
        json.dumps(
            {
                "schema": "lingtai.agent_record/v1", "schema_version": 1,
                "generated_at": "2026-08-20T00:00:00Z",
                "session": {"state": "active", "uptime_seconds": 125},
                "usage": {"context_used_tokens": 100, "context_usage_pct": 2.5},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".secrets").mkdir()
    (tmp_path / ".secrets" / "feishu.json").write_text("{}", encoding="utf-8")

    data = LocalCommandCore(tmp_path).collect_kanban_data()

    assert data is not None
    assert data["current_agent"] == tmp_path.name
    assert data["current_model"] == "test-model"
    assert data["current_provider"] == "test-provider"
    assert data["context_limit"] == 0
    assert data["agent_state"] == "active"
    assert data["fmt"](1200) == "1.2K"
    assert data["fmt_duration"](125) == "2m"
    assert data["addon_status"] == {"telegram": False, "feishu": True}

    (tmp_path / ".agent.json").write_text(
        json.dumps({"agent_id": "agent-1", "llm": {"context_limit": 8192}}),
        encoding="utf-8",
    )
    assert LocalCommandCore(tmp_path).collect_kanban_data()["context_limit"] == 8192


def test_kanban_snapshot_survives_sibling_with_non_utf8_agent_json(
    tmp_path: Path,
) -> None:
    # A sibling agent dir under the same lingtai root can carry a
    # present-but-non-UTF-8-decodable .agent.json (e.g. mid-write, or
    # corrupted). Kanban collection walks every sibling for the "all
    # agents" roster and must degrade that one entry rather than crash.
    (tmp_path / "init.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".agent.json").write_text(
        json.dumps({"agent_id": "agent-1"}), encoding="utf-8"
    )
    sibling = tmp_path.parent / "sibling-agent"
    sibling.mkdir()
    (sibling / ".agent.json").write_bytes(b"\xff\xfe{")

    try:
        data = LocalCommandCore(tmp_path).collect_kanban_data()

        assert data is not None
        assert data["current_agent"] == tmp_path.name
        assert data["all_agents"]["sibling-agent"]["state"] == "?"
        assert data["all_agents"]["sibling-agent"]["model"] == "?"
    finally:
        (sibling / ".agent.json").unlink()
        sibling.rmdir()


def test_service_injects_one_workdir_bound_core_for_every_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path / "wrong-agent"))
    (tmp_path / "init.json").write_text("{}", encoding="utf-8")
    service = TelegramService(
        tmp_path,
        [
            {"alias": "one", "bot_token": "token-one"},
            {"alias": "two", "bot_token": "token-two"},
        ],
        lambda _alias, _update: None,
    )
    one = service.get_account("one")._local_commands
    two = service.get_account("two")._local_commands

    assert one is two
    data = one.collect_kanban_data()
    assert data is not None
    assert data["current_agent"] == tmp_path.name
