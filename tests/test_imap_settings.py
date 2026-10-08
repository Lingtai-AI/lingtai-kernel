"""Source-backed proofs for IMAP's read-only five-field settings SHOW."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from lingtai.mcp_servers.imap import server as imap_server
from lingtai.mcp_servers.imap._family import (
    IMAP_ACTIONS,
    build_imap_family,
    handle_imap,
)
from lingtai.mcp_servers.imap.manager import IMAPMailManager
from lingtai.mcp_servers.imap.plugin import IMAP_PLUGIN
from lingtai.mcp_servers.imap.service import IMAPMailService
from lingtai.mcp_servers.imap.settings import IMAP_CONFIG_ENV, imap_setting_rows


_ROW_KEYS = [
    "config_reference",
    "account_addresses",
    "credentials",
    "imap_endpoints",
    "smtp_endpoints",
    "oauth_configuration",
]
_COMMENTS = [f"imap-mcp-manual#{key.replace('_', '-')}" for key in _ROW_KEYS]
_UNAVAILABLE = {
    "status": "failed",
    "error_code": "SETTINGS_UNAVAILABLE",
    "message": "settings inventory is unavailable",
}


def _write_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    accounts: list[dict],
    *,
    relative: bool = False,
) -> Path:
    path = tmp_path / "imap-owner.json"
    path.write_text(json.dumps({"accounts": accounts}), encoding="utf-8")
    if relative:
        monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
        monkeypatch.setenv(IMAP_CONFIG_ENV, path.name)
    else:
        monkeypatch.setenv(IMAP_CONFIG_ENV, str(path))
    return path


def _manager(
    tmp_path: Path,
    accounts: list[dict],
    *,
    config_path: Path | None,
) -> IMAPMailManager:
    return IMAPMailManager(
        IMAPMailService(accounts, working_dir=tmp_path),
        working_dir=tmp_path,
        tcp_alias="imap-test",
        on_inbound=lambda _event: None,
        config_path=config_path,
    )


def _settings(manager: object | None, action_input: object = None) -> dict:
    return handle_imap(
        manager,
        {
            "action": "settings",
            "input": {} if action_input is None else action_input,
            "reasoning": "verify IMAP owner settings",
        },
    )


def test_provider_rows_are_the_exact_applied_manager_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    accounts = [
        {
            "email_address": "primary-private@example.test",
            "email_password": "primary-secret",
        },
        {
            "email_address": "secondary-private@example.test",
            "imap_host": "private.imap.example.test",
            "imap_port": 1993,
            "smtp_host": "private.smtp.example.test",
            "smtp_port": 1587,
            "auth": {
                "type": "microsoft_oauth2",
                "client_id": "private-client-id",
                "token_cache": "private/token.cache",
            },
        },
    ]
    config_path = _write_config(monkeypatch, tmp_path, accounts, relative=True)
    manager = _manager(tmp_path, accounts, config_path=config_path)

    # Later source/environment changes are prospective until relaunch. SHOW
    # continues to describe the manager that was actually constructed.
    accounts[0]["email_address"] = "prospective@example.test"
    prospective = tmp_path / "prospective.json"
    prospective.write_text('{"accounts":[]}', encoding="utf-8")
    monkeypatch.setenv(IMAP_CONFIG_ENV, prospective.name)

    rows = imap_setting_rows(manager)
    assert [row.key for row in rows] == _ROW_KEYS
    assert [row.current for row in rows] == [
        str(config_path),
        ["primary-private@example.test", "secondary-private@example.test"],
        ["password-configured", "oauth-configured"],
        ["imap.gmail.com:993", "private.imap.example.test:1993"],
        ["smtp.gmail.com:587", "private.smtp.example.test:1587"],
        [
            "not-configured",
            "type=microsoft_oauth2;client_id=configured;token_cache=configured",
        ],
    ]
    assert [row.default for row in rows] == [
        None,
        None,
        None,
        ["imap.gmail.com:993"],
        ["smtp.gmail.com:587"],
        [],
    ]
    assert [row.comment for row in rows] == _COMMENTS
    assert all(row.configurable is True and row._sensitive is True for row in rows)


def test_production_binding_redacts_exact_five_fields_and_never_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    accounts = [
        {
            "email_address": "private@example.test",
            "email_password": "private-password",
            "imap_host": "private.imap.example.test",
            "smtp_host": "private.smtp.example.test",
        }
    ]
    config_path = _write_config(monkeypatch, tmp_path, accounts, relative=True)
    before = config_path.read_bytes()
    env_reference = config_path.name

    manager, _bridge, _working_dir = imap_server.build_manager()
    assert imap_setting_rows(manager)[0].current == str(config_path)
    result = _settings(manager)

    assert list(result) == ["settings"]
    assert [row["key"] for row in result["settings"]] == _ROW_KEYS
    for row, comment in zip(result["settings"], _COMMENTS, strict=True):
        assert list(row) == [
            "key",
            "current",
            "default",
            "configurable",
            "comment",
        ]
        assert row["current"] == row["default"] == "<redacted>"
        assert row["configurable"] is True
        assert row["comment"] == comment

    rendered = json.dumps(result, sort_keys=True)
    for private in (
        str(config_path),
        "private@example.test",
        "private-password",
        "private.imap.example.test",
        "private.smtp.example.test",
        "imap.gmail.com:993",
        "smtp.gmail.com:587",
    ):
        assert private not in rendered
    assert config_path.read_bytes() == before
    assert os.environ[IMAP_CONFIG_ENV] == env_reference


def test_unavailable_or_incoherent_truth_is_one_fixed_no_row_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    accounts = [{"email_address": "owner@example.test"}]
    config_path = _write_config(monkeypatch, tmp_path, accounts)

    assert _settings(None) == _UNAVAILABLE
    assert _settings(_manager(tmp_path, accounts, config_path=None)) == _UNAVAILABLE
    assert _settings(_manager(tmp_path, [], config_path=config_path)) == _UNAVAILABLE
    malformed = [{"email_address": "owner@example.test", "auth": ["not-an-object"]}]
    assert _settings(
        _manager(tmp_path, malformed, config_path=config_path)
    ) == _UNAVAILABLE

    # A falsy legacy auth value follows the runtime's password branch.
    falsy_auth = [{"email_address": "owner@example.test", "auth": []}]
    assert imap_setting_rows(
        _manager(tmp_path, falsy_auth, config_path=config_path)
    )[2].current == ["unconfigured"]

    # Ambient environment is not current runtime truth after construction.
    manager = _manager(tmp_path, accounts, config_path=config_path)
    monkeypatch.delenv(IMAP_CONFIG_ENV)
    assert "settings" in _settings(manager)


def test_family_opt_in_order_and_empty_input_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    accounts = [{"email_address": "owner@example.test"}]
    config_path = _write_config(monkeypatch, tmp_path, accounts)
    manager = _manager(tmp_path, accounts, config_path=config_path)
    family = build_imap_family(manager)

    assert IMAP_PLUGIN.settings is True
    assert IMAP_ACTIONS[-2:] == ("settings", "manual")
    assert family.child_names == IMAP_ACTIONS
    settings_schema = family.build_schema()["allOf"][-2]["then"]["properties"]["input"]
    assert settings_schema == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
    assert _settings(manager, {"set": "config_reference"}) == {
        "status": "failed",
        "error_code": "INVALID_ARGUMENT",
        "message": "invalid imap input",
    }


def test_every_comment_targets_an_exact_owned_manual_heading():
    manual = (
        Path(__file__).parents[1]
        / "src"
        / "lingtai"
        / "mcp_servers"
        / "imap"
        / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "name: imap-mcp-manual" in manual
    for heading in (
        "Config reference",
        "Account addresses",
        "Credentials",
        "IMAP endpoints",
        "SMTP endpoints",
        "OAuth configuration",
    ):
        assert f"### {heading}\n" in manual


def test_mcp_configuration_and_onboarding_twins_state_runtime_truth():
    configuration = imap_server._configuration_markdown()
    troubleshooting = imap_server._troubleshooting_markdown()
    onboarding = imap_server._onboarding_markdown()
    onboarding_html = imap_server._onboarding_html_template()

    assert "strict JSON and the outer account shape only" in configuration
    assert "not applied by the current listener" in configuration
    assert (
        'imap(action="settings", input={}, reasoning="verify applied IMAP settings")'
        in configuration
    )
    assert 'imap(action="settings", input={})' not in configuration
    assert "not enforced" in troubleshooting
    assert onboarding.count("imap(action=") == 4
    for example in (
        'imap(action="settings", input={}, reasoning="verify applied IMAP settings")',
        'imap(action="accounts", input={}, reasoning="verify account and listener status")',
        'imap(action="check", input={"n": 5}, reasoning="verify inbound IMAP delivery")',
        'imap(action="send", input={"address": "recipient@example.com", '
        '"message": "SMTP setup test"}, reasoning="verify outbound SMTP delivery")',
    ):
        assert example in onboarding
    assert onboarding_html.count("imap(action=&quot;") == 3
    for example in (
        "imap(action=&quot;accounts&quot;, input={}, "
        "reasoning=&quot;verify account and listener status&quot;)",
        "imap(action=&quot;check&quot;, input={&quot;n&quot;: 5}, "
        "reasoning=&quot;verify inbound IMAP delivery&quot;)",
        "imap(action=&quot;send&quot;, input={&quot;address&quot;: "
        "&quot;recipient@example.com&quot;, &quot;message&quot;: "
        "&quot;SMTP setup test&quot;}, reasoning=&quot;verify outbound SMTP delivery&quot;)",
    ):
        assert example in onboarding_html
    for retired_example in (
        'imap(action="settings", input={})',
        'imap(action="accounts")',
        'imap(action="check", n=5)',
        'imap(action="send", ...)',
    ):
        assert retired_example not in onboarding
    assert "deployment owner relaunch" in onboarding
    assert "deployment owner relaunch" in onboarding_html
    assert "allowed sender" not in onboarding.lower()
    assert "refresh the host agent" not in onboarding.lower()


def test_accounts_basic_action_result_is_unchanged():
    class RecordingManager:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def handle(self, args: dict) -> dict:
            self.calls.append(dict(args))
            return {
                "accounts": [
                    {"address": "first@example.test", "listening": True},
                    {"address": "second@example.test", "listening": False},
                ]
            }

    manager = RecordingManager()
    result = handle_imap(
        manager,
        {"action": "accounts", "input": {}, "reasoning": "ordinary action"},
    )
    assert manager.calls == [{"action": "accounts"}]
    assert result == {
        "accounts": [
            {"address": "first@example.test", "listening": True},
            {"address": "second@example.test", "listening": False},
        ]
    }


def test_manual_has_one_action_table_and_complete_private_settings_guidance():
    root = Path(__file__).parents[1] / "src/lingtai/mcp_servers/imap"
    entry = (root / "SKILL.md").read_text()
    detail = (root / "reference/operation-contract.md").read_text()
    assert "before the first outbound" not in entry
    assert "orchestrator" in entry and "avatars" in entry
    assert "| `send` |" in entry and "| `send` |" not in detail
    assert "SMTP still uses password login" in detail
    assert "client ID itself is not displayed" in entry
    assert "allowed_senders" in detail and "not enforced" in detail


def test_manual_distinguishes_delivery_flags_guard_and_expunge_risk():
    root = Path(__file__).parents[1] / "src/lingtai/mcp_servers/imap"
    detail = (root / "reference/operation-contract.md").read_text()
    assert "Answered" in detail and "SMTP error" in detail
    assert "refusal map" in detail and "blocked" in detail
    assert "other already-deleted messages" in detail
    assert "booleans" in detail and "truthiness" in detail
