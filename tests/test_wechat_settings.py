"""Focused source-backed proofs for the read-only WeChat settings owner."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lingtai.mcp_servers.wechat import api, server
from lingtai.mcp_servers.wechat import _family as wechat_family
from lingtai.mcp_servers.wechat.plugin import WECHAT_ACTIONS, WECHAT_PLUGIN
from lingtai.mcp_servers.wechat.settings import (
    ALLOWED_USERS_KEY,
    BASE_URL_KEY,
    BOT_TOKEN_KEY,
    COMMENT_BY_KEY,
    CONFIG_PATH_KEY,
    POLL_INTERVAL_KEY,
    SETTING_KEYS,
    USER_ID_KEY,
    wechat_settings,
)

_FIELDS = ("key", "current", "default", "configurable", "comment")
_SENSITIVE_KEYS = {
    CONFIG_PATH_KEY,
    BASE_URL_KEY,
    ALLOWED_USERS_KEY,
    BOT_TOKEN_KEY,
    USER_ID_KEY,
}
_UNAVAILABLE = {
    "status": "failed",
    "error_code": "SETTINGS_UNAVAILABLE",
    "message": "settings inventory is unavailable",
}


def _build_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    config: dict | None = None,
    credentials: dict | None = None,
    config_name: str = "config.json",
):
    agent_dir = tmp_path / "project" / ".lingtai" / "agent"
    config_dir = agent_dir / ".secrets" / "wechat"
    config_dir.mkdir(parents=True)
    config_path = config_dir / config_name
    credentials_path = config_dir / "credentials.json"
    config_path.write_text(
        json.dumps(config if config is not None else {}),
        encoding="utf-8",
    )
    credentials_path.write_text(
        json.dumps(
            credentials
            if credentials is not None
            else {"bot_token": "fixture-token", "user_id": "fixture-user"}
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(agent_dir))
    monkeypatch.setenv("LINGTAI_WECHAT_CONFIG", f".secrets/wechat/{config_name}")
    manager, _working_dir = server.build_manager()
    return manager, config_path, credentials_path


def _show(manager, action_input: dict) -> dict:
    return wechat_family.handle_wechat(
        manager,
        {
            "action": "settings",
            "input": action_input,
            "reasoning": "focused settings contract proof",
        },
    )


def test_provider_projects_exact_manager_snapshot_and_full_redaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manager, config_path, credentials_path = _build_manager(
        tmp_path,
        monkeypatch,
        config={
            "base_url": "https://config-endpoint.invalid",
            "cdn_base_url": "https://inert-cdn.invalid",
            "poll_interval": "2.5",
            "allowed_users": ["fixture-allowed"],
        },
        credentials={
            "bot_token": "fixture-token",
            "user_id": "fixture-user",
            "base_url": "https://credential-endpoint.invalid",
        },
        config_name="owner-settings.json",
    )

    supplied = wechat_settings(manager)
    assert tuple(row.key for row in supplied) == SETTING_KEYS == (
        "config_path",
        "base_url",
        "poll_interval",
        "allowed_users",
        "bot_token",
        "user_id",
    )
    by_key = {row.key: row for row in supplied}
    assert by_key[CONFIG_PATH_KEY].current == str(config_path)
    assert by_key[CONFIG_PATH_KEY].default is None
    assert by_key[BASE_URL_KEY].current == "https://credential-endpoint.invalid"
    assert by_key[BASE_URL_KEY].default == api.DEFAULT_BASE_URL
    assert by_key[POLL_INTERVAL_KEY].current == 2.5
    assert by_key[POLL_INTERVAL_KEY].default == 1.0
    assert by_key[ALLOWED_USERS_KEY].current == {"fixture-allowed"}
    assert by_key[ALLOWED_USERS_KEY].default is None
    assert by_key[BOT_TOKEN_KEY].current == "fixture-token"
    assert by_key[BOT_TOKEN_KEY].default is None
    assert by_key[USER_ID_KEY].current == "fixture-user"
    assert by_key[USER_ID_KEY].default is None
    assert all(row.configurable is True for row in supplied)
    assert {row.key: row.comment for row in supplied} == COMMENT_BY_KEY
    assert "cdn_base_url" not in by_key

    result = _show(manager, {})
    assert tuple(result) == ("settings",)
    assert tuple(row["key"] for row in result["settings"]) == SETTING_KEYS
    assert all(tuple(row) == _FIELDS for row in result["settings"])
    projected = {row["key"]: row for row in result["settings"]}
    assert projected[POLL_INTERVAL_KEY] == {
        "key": POLL_INTERVAL_KEY,
        "current": 2.5,
        "default": 1.0,
        "configurable": True,
        "comment": COMMENT_BY_KEY[POLL_INTERVAL_KEY],
    }
    for key in _SENSITIVE_KEYS:
        assert projected[key]["current"] == "<redacted>"
        assert projected[key]["default"] == "<redacted>"

    rendered = json.dumps(result)
    for private in (
        str(config_path),
        str(credentials_path),
        "fixture-token",
        "fixture-user",
        "fixture-allowed",
        "config-endpoint.invalid",
        "credential-endpoint.invalid",
        "inert-cdn.invalid",
    ):
        assert private not in rendered


def test_show_uses_active_startup_snapshot_and_performs_no_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manager, config_path, credentials_path = _build_manager(
        tmp_path,
        monkeypatch,
        config={"poll_interval": 3, "allowed_users": ["active-user"]},
        credentials={
            "bot_token": "active-token",
            "user_id": "active-identity",
            "base_url": "https://active-endpoint.invalid",
        },
    )

    config_path.write_text(
        json.dumps({"poll_interval": 9, "allowed_users": ["prospective-user"]}),
        encoding="utf-8",
    )
    credentials_path.write_text(
        json.dumps(
            {
                "bot_token": "prospective-token",
                "user_id": "prospective-identity",
                "base_url": "https://prospective-endpoint.invalid",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LINGTAI_WECHAT_CONFIG", "prospective/config.json")
    files_before = (config_path.read_bytes(), credentials_path.read_bytes())
    environment_before = {
        name: os.environ.get(name)
        for name in ("LINGTAI_WECHAT_CONFIG", "LINGTAI_AGENT_DIR")
    }

    supplied = {row.key: row for row in wechat_settings(manager)}
    assert supplied[CONFIG_PATH_KEY].current == str(config_path)
    assert supplied[BASE_URL_KEY].current == "https://active-endpoint.invalid"
    assert supplied[POLL_INTERVAL_KEY].current == 3.0
    assert supplied[ALLOWED_USERS_KEY].current == {"active-user"}
    assert supplied[BOT_TOKEN_KEY].current == "active-token"
    assert supplied[USER_ID_KEY].current == "active-identity"
    assert _show(manager, {})["settings"][2]["current"] == 3.0

    assert (config_path.read_bytes(), credentials_path.read_bytes()) == files_before
    assert {
        name: os.environ.get(name)
        for name in ("LINGTAI_WECHAT_CONFIG", "LINGTAI_AGENT_DIR")
    } == environment_before


@pytest.mark.parametrize("poll_interval", [0, -2.5])
def test_runtime_accepted_zero_and_negative_poll_intervals_remain_visible(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    poll_interval: float,
):
    manager, _config_path, _credentials_path = _build_manager(
        tmp_path,
        monkeypatch,
        config={"poll_interval": poll_interval},
    )
    assert _show(manager, {})["settings"][2]["current"] == float(poll_interval)


def test_unavailable_or_nonfinite_snapshot_is_one_fixed_whole_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    assert _show(None, {}) == _UNAVAILABLE

    manager, _config_path, _credentials_path = _build_manager(
        tmp_path,
        monkeypatch,
        config={"poll_interval": "nan"},
    )
    assert _show(manager, {}) == _UNAVAILABLE


def test_manual_anchors_strict_input_order_and_other_actions_are_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    manager, config_path, credentials_path = _build_manager(tmp_path, monkeypatch)
    headings = {
        line.removeprefix("## ")
        for line in WECHAT_PLUGIN.skill_body.splitlines()
        if line.startswith("## ")
    }
    for pointer in COMMENT_BY_KEY.values():
        owner, fragment = pointer.split("#", 1)
        assert owner == WECHAT_PLUGIN.skill_frontmatter["name"]
        assert fragment in headings

    assert WECHAT_PLUGIN.settings is True
    assert WECHAT_ACTIONS[-2:] == ("settings", "manual")
    before = (config_path.read_bytes(), credentials_path.read_bytes())
    invalid = _show(manager, {"set": POLL_INTERVAL_KEY, "value": 2})
    assert invalid["status"] == "failed"
    assert "settings" not in invalid
    assert (config_path.read_bytes(), credentials_path.read_bytes()) == before

    def provider_must_not_run(_manager):
        raise AssertionError("settings provider reached by accounts")

    monkeypatch.setattr(wechat_family, "wechat_settings", provider_must_not_run)
    result = wechat_family.handle_wechat(
        manager,
        {"action": "accounts", "input": {}, "reasoning": "non-regression"},
    )
    assert result["status"] == "ok"
    assert result["accounts"] == ["default"]


def test_manual_routes_each_show_anchor_without_routine_send_gate():
    body = WECHAT_PLUGIN.skill_body
    assert "Use the schema for routine calls" in body
    assert "Avatar sessions must not reconfigure" in body
    for key in SETTING_KEYS:
        section = body.split(f"## setting-{key.replace('_', '-')}\n", 1)[1]
        section = section.split("\n## ", 1)[0]
        assert f"reference/setup.md#setting-{key.replace('_', '-')}" in section
    setup = (Path(WECHAT_PLUGIN.skill_path).parent / "reference/setup.md").read_text()
    assert "positive finite" in setup
    assert "SETTINGS_UNAVAILABLE" in setup
    assert "from lingtai.mcp_servers.wechat.login import cli_login" in setup


def test_manual_distinguishes_unknown_media_from_delivery_evidence():
    root = Path(WECHAT_PLUGIN.skill_path).parent
    media = " ".join((root / "reference/media.md").read_text().split())
    operations = " ".join((root / "reference/operations.md").read_text().split())
    assert "`unknown`" in media and "no warning" in media
    assert "Unknown/unreadable bytes fail" not in media
    assert "after symlink" in media
    assert "missing sent record" in operations
    assert "later text chunk" in operations
    assert "booleans" in operations
