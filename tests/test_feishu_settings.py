"""Focused source-backed proofs for Feishu's read-only settings provider."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lingtai.mcp_servers.feishu._family import (
    FEISHU_ACTIONS,
    build_feishu_family,
    handle_feishu,
)
from lingtai.mcp_servers.feishu.plugin import FEISHU_PLUGIN
from lingtai.mcp_servers.feishu.service import FeishuService
from lingtai.mcp_servers.feishu.settings import (
    ACCOUNT_ALIASES,
    ACCOUNT_ALIASES_COMMENT,
    ACCOUNT_ALLOWED_USERS,
    ACCOUNT_ALLOWED_USERS_COMMENT,
    ACCOUNT_APP_IDS,
    ACCOUNT_APP_IDS_COMMENT,
    ACCOUNT_APP_SECRETS,
    ACCOUNT_APP_SECRETS_COMMENT,
    CONFIG_PATH,
    CONFIG_PATH_COMMENT,
    TASKCARD_ENABLED,
    TASKCARD_ENABLED_COMMENT,
    TASKCARD_NORMAL_ROWS,
    TASKCARD_NORMAL_ROWS_COMMENT,
    build_feishu_settings,
)

_DEFAULT_INPUT = object()


def _service(
    tmp_path: Path,
    *,
    config_source: str | None = "fixture-config-reference",
) -> FeishuService:
    return FeishuService(
        tmp_path,
        [
            {
                "alias": "primary",
                "app_id": "fixture-app-id",
                "app_secret": "fixture-app-secret",
                "allowed_users": ["fixture-user"],
            }
        ],
        lambda _alias, _data: None,
        config_source=config_source,
    )


class _Manager:
    def __init__(self, service: FeishuService) -> None:
        self._service = service
        self.calls: list[dict[str, Any]] = []

    def handle(self, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(args))
        return {"status": "ok", "action": args["action"]}


def _show(
    manager: _Manager | None,
    action_input: Any = _DEFAULT_INPUT,
) -> dict[str, Any]:
    return handle_feishu(
        manager,
        {
            "action": "settings",
            "input": {} if action_input is _DEFAULT_INPUT else action_input,
            "reasoning": "inspect Feishu settings",
        },
    )


def _rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["key"]: row for row in result["settings"]}


def test_exact_five_field_inventory_values_order_defaults_and_redaction(
    tmp_path: Path,
) -> None:
    manager = _Manager(_service(tmp_path))

    result = _show(manager)

    assert result == {
        "settings": [
            {
                "key": CONFIG_PATH,
                "current": "<redacted>",
                "default": "<redacted>",
                "configurable": True,
                "comment": CONFIG_PATH_COMMENT,
            },
            {
                "key": ACCOUNT_ALIASES,
                "current": ["primary"],
                "default": None,
                "configurable": True,
                "comment": ACCOUNT_ALIASES_COMMENT,
            },
            {
                "key": ACCOUNT_APP_IDS,
                "current": ["fixture-app-id"],
                "default": None,
                "configurable": True,
                "comment": ACCOUNT_APP_IDS_COMMENT,
            },
            {
                "key": ACCOUNT_APP_SECRETS,
                "current": "<redacted>",
                "default": "<redacted>",
                "configurable": True,
                "comment": ACCOUNT_APP_SECRETS_COMMENT,
            },
            {
                "key": ACCOUNT_ALLOWED_USERS,
                "current": "<redacted>",
                "default": "<redacted>",
                "configurable": True,
                "comment": ACCOUNT_ALLOWED_USERS_COMMENT,
            },
            {
                "key": TASKCARD_ENABLED,
                "current": True,
                "default": True,
                "configurable": True,
                "comment": TASKCARD_ENABLED_COMMENT,
            },
            {
                "key": TASKCARD_NORMAL_ROWS,
                "current": 1,
                "default": 1,
                "configurable": True,
                "comment": TASKCARD_NORMAL_ROWS_COMMENT,
            },
        ]
    }
    expected_fields = ["key", "current", "default", "configurable", "comment"]
    assert all(list(row) == expected_fields for row in result["settings"])
    assert "fixture-config-reference" not in repr(result)
    assert "fixture-app-secret" not in repr(result)
    assert "fixture-user" not in repr(result)
    assert manager.calls == []


def test_show_reads_fresh_live_task_card_truth_without_writing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.set_taskcard_enabled(False)
    service.set_taskcard_normal_rows(7)
    state_path = tmp_path / "feishu" / "taskcard.json"
    before = state_path.read_bytes()
    before_mtime = state_path.stat().st_mtime_ns

    first = _rows(_show(_Manager(service)))
    second = _rows(_show(_Manager(service)))

    assert first[TASKCARD_ENABLED]["current"] is False
    assert first[TASKCARD_ENABLED]["default"] is True
    assert first[TASKCARD_NORMAL_ROWS]["current"] == 7
    assert first[TASKCARD_NORMAL_ROWS]["default"] == 1
    assert second == first
    assert state_path.read_bytes() == before
    assert state_path.stat().st_mtime_ns == before_mtime


def test_sensitive_rows_use_only_the_private_redaction_flag(tmp_path: Path) -> None:
    rows = {row.key: row for row in build_feishu_settings(_Manager(_service(tmp_path)))}

    assert {key for key, row in rows.items() if row._sensitive} == {
        CONFIG_PATH,
        ACCOUNT_APP_SECRETS,
        ACCOUNT_ALLOWED_USERS,
    }
    assert "fixture-config-reference" not in repr(tuple(rows.values()))
    assert "fixture-app-secret" not in repr(tuple(rows.values()))
    assert "fixture-user" not in repr(tuple(rows.values()))


def test_every_comment_targets_an_exact_owner_manual_heading(tmp_path: Path) -> None:
    comments = [
        row.comment for row in build_feishu_settings(_Manager(_service(tmp_path)))
    ]
    expected = {
        CONFIG_PATH_COMMENT: "### Setting config path",
        ACCOUNT_ALIASES_COMMENT: "### Setting account aliases",
        ACCOUNT_APP_IDS_COMMENT: "### Setting account app ids",
        ACCOUNT_APP_SECRETS_COMMENT: "### Setting account app secrets",
        ACCOUNT_ALLOWED_USERS_COMMENT: "### Setting account allowed users",
        TASKCARD_ENABLED_COMMENT: "### Setting task card enabled",
        TASKCARD_NORMAL_ROWS_COMMENT: "### Setting task card normal rows",
    }

    assert comments == list(expected)
    assert all(comment.startswith("feishu-mcp-manual#") for comment in comments)
    assert all(heading in FEISHU_PLUGIN.skill_body for heading in expected.values())


def test_unavailable_or_non_json_current_is_one_fixed_no_row_failure(
    tmp_path: Path,
) -> None:
    expected = {
        "status": "failed",
        "error_code": "SETTINGS_UNAVAILABLE",
        "message": "settings inventory is unavailable",
    }
    non_json_service = _service(tmp_path / "non-json")
    non_json_service.get_account("primary")._app_id = float("nan")

    assert _show(None) == expected
    assert _show(_Manager(_service(tmp_path / "missing", config_source=None))) == expected
    assert _show(_Manager(non_json_service)) == expected


def test_complete_inventory_obeys_the_65536_byte_bound(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.get_account("primary")._app_id = "x" * 70_000

    result = _show(_Manager(service))

    assert "settings" not in result
    assert result == {
        "status": "failed",
        "error_code": "SETTINGS_RESPONSE_TOO_LARGE",
        "message": "settings inventory exceeds the 65536-byte response limit",
        "max_bytes": 65_536,
    }


def test_feishu_opts_in_and_settings_is_strict_before_manual(tmp_path: Path) -> None:
    manager = _Manager(_service(tmp_path))

    assert FEISHU_PLUGIN.settings is True
    assert build_feishu_family(manager).child_names == FEISHU_ACTIONS
    assert FEISHU_ACTIONS[-2:] == ("settings", "manual")

    for rejected_input in ({"set": TASKCARD_ENABLED}, [], None):
        rejected = _show(manager, rejected_input)
        assert rejected["status"] == "failed"
        assert rejected["error_code"] == "INVALID_ARGUMENT"
    assert manager.calls == []


def test_unchanged_accounts_action_still_dispatches_to_manager(tmp_path: Path) -> None:
    manager = _Manager(_service(tmp_path))

    result = handle_feishu(
        manager,
        {"action": "accounts", "input": {}, "reasoning": "ordinary call"},
    )

    assert result == {"status": "ok", "action": "accounts"}
    assert manager.calls == [{"action": "accounts"}]


def test_manual_preserves_taskcard_owner_timing_and_redaction():
    package = Path(__file__).parents[1] / "src/lingtai/mcp_servers/feishu"
    root = (package / "SKILL.md").read_text()
    detail = (package / "reference/message-semantics.md").read_text()
    assert "orchestrator" in root and "avatar" in root
    assert "feishu/taskcard.json" in detail
    assert "MCP relaunch" in detail
    assert "booleans are not row counts" in detail
    assert "current` and `default" in detail and "<redacted>" in detail
    assert "SETTINGS_UNAVAILABLE" in detail and "SETTINGS_RESPONSE_TOO_LARGE" in detail


def test_manual_preserves_attachment_and_progress_boundaries():
    package = Path(__file__).parents[1] / "src/lingtai/mcp_servers/feishu"
    detail = (package / "reference/message-semantics.md").read_text()
    diagnostic = (package / "reference/diagnostics.md").read_text()
    assert "current incoming" in detail and "local paths" in detail
    assert "custom card replacement" in detail
    assert "file_name" in detail
    assert "publish credentials" not in diagnostic
    assert "owner authorization" in diagnostic


def test_taskcard_file_changes_require_new_service_but_commands_are_live(tmp_path):
    service = _service(tmp_path)
    state = tmp_path / "feishu/taskcard.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({"taskcard": False, "normal_rows": 4}))
    assert service.taskcard_enabled() is True
    assert service.taskcard_normal_rows() == 1
    restarted = _service(tmp_path)
    assert restarted.taskcard_enabled() is False
    assert restarted.taskcard_normal_rows() == 4
    restarted.set_taskcard_enabled(True)
    restarted.set_taskcard_normal_rows(2)
    rows = _rows(_show(_Manager(restarted)))
    assert rows[TASKCARD_ENABLED]["current"] is True
    assert rows[TASKCARD_NORMAL_ROWS]["current"] == 2
    state.write_text(json.dumps({"taskcard": "false", "normal_rows": True}))
    invalid = _service(tmp_path)
    assert invalid.taskcard_enabled() is True
    assert invalid.taskcard_normal_rows() == 1
