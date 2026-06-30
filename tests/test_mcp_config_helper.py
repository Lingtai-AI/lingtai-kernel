from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from lingtai.mcp_servers import _config


def _write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_resolve_config_path_relative_to_agent_dir(tmp_path, monkeypatch):
    config_path = _write_json(tmp_path / "configs" / "addon.json", {"ok": True})
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LINGTAI_TEST_CONFIG", "configs/addon.json")

    assert (
        _config.resolve_config_path("LINGTAI_TEST_CONFIG", label="Test")
        == config_path
    )


def test_resolve_config_path_absolute_passes_through(tmp_path, monkeypatch):
    config_path = _write_json(tmp_path / "absolute.json", {"ok": True})
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path / "ignored"))
    monkeypatch.setenv("LINGTAI_TEST_CONFIG", str(config_path))

    assert (
        _config.resolve_config_path("LINGTAI_TEST_CONFIG", label="Test")
        == config_path
    )


def test_resolve_config_path_cwd_fallback(tmp_path, monkeypatch):
    config_path = _write_json(tmp_path / "cwd.json", {"ok": True})
    monkeypatch.delenv("LINGTAI_AGENT_DIR", raising=False)
    monkeypatch.setenv("LINGTAI_TEST_CONFIG", "cwd.json")
    monkeypatch.chdir(tmp_path)

    assert (
        _config.resolve_config_path("LINGTAI_TEST_CONFIG", label="Test")
        == config_path
    )


def test_resolve_config_path_expands_home(tmp_path, monkeypatch):
    config_path = _write_json(tmp_path / "home.json", {"ok": True})
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path / "ignored"))
    monkeypatch.setenv("LINGTAI_TEST_CONFIG", "~/home.json")

    assert (
        _config.resolve_config_path("LINGTAI_TEST_CONFIG", label="Test")
        == config_path
    )


def test_resolve_config_path_missing_env_default_message(monkeypatch):
    monkeypatch.delenv("LINGTAI_TEST_CONFIG", raising=False)

    with pytest.raises(ValueError) as excinfo:
        _config.resolve_config_path("LINGTAI_TEST_CONFIG", label="Test")

    assert (
        str(excinfo.value)
        == "LINGTAI_TEST_CONFIG env var not set — point it at your Test "
        "config JSON file"
    )


def test_resolve_config_path_missing_env_override(monkeypatch):
    monkeypatch.delenv("LINGTAI_TEST_CONFIG", raising=False)

    with pytest.raises(ValueError) as excinfo:
        _config.resolve_config_path(
            "LINGTAI_TEST_CONFIG",
            label="Test",
            missing_env_msg="custom missing message",
        )

    assert str(excinfo.value) == "custom missing message"


def test_resolve_config_path_missing_file_message(tmp_path, monkeypatch):
    expected = tmp_path / "missing.json"
    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv("LINGTAI_TEST_CONFIG", "missing.json")

    with pytest.raises(FileNotFoundError) as excinfo:
        _config.resolve_config_path("LINGTAI_TEST_CONFIG", label="Test")

    assert str(excinfo.value) == f"Test config not found: {expected}"


def test_load_config_file_returns_parsed_dict_and_path(tmp_path, monkeypatch):
    payload = {"accounts": [{"alias": "a"}]}
    config_path = _write_json(tmp_path / "addon.json", payload)
    monkeypatch.setenv("LINGTAI_TEST_CONFIG", str(config_path))

    assert _config.load_config_file("LINGTAI_TEST_CONFIG", label="Test") == (
        payload,
        config_path,
    )


@pytest.mark.parametrize(
    ("module_name", "env_name", "label", "missing_env_msg", "returns_path"),
    [
        (
            "lingtai.mcp_servers.imap.server",
            "LINGTAI_IMAP_CONFIG",
            "IMAP",
            "LINGTAI_IMAP_CONFIG env var not set — point it at your IMAP "
            "config JSON file",
            False,
        ),
        (
            "lingtai.mcp_servers.feishu.server",
            "LINGTAI_FEISHU_CONFIG",
            "Feishu",
            "LINGTAI_FEISHU_CONFIG env var not set — point it at your "
            "Feishu config JSON file",
            False,
        ),
        (
            "lingtai.mcp_servers.telegram.server",
            "LINGTAI_TELEGRAM_CONFIG",
            "Telegram",
            "LINGTAI_TELEGRAM_CONFIG env var not set — point it at your "
            "Telegram config JSON file",
            False,
        ),
        (
            "lingtai.mcp_servers.cloud_mail.server",
            "LINGTAI_CLOUD_MAIL_CONFIG",
            "Cloud Mail",
            "LINGTAI_CLOUD_MAIL_CONFIG env var not set — point it at your "
            "Cloud Mail config JSON file",
            False,
        ),
        (
            "lingtai.mcp_servers.whatsapp.server",
            "LINGTAI_WHATSAPP_CONFIG",
            "WhatsApp",
            "LINGTAI_WHATSAPP_CONFIG env var not set",
            True,
        ),
    ],
)
def test_addon_load_config_preserves_messages_and_return_shape(
    tmp_path,
    monkeypatch,
    module_name,
    env_name,
    label,
    missing_env_msg,
    returns_path,
):
    module = importlib.import_module(module_name)
    payload = {"accounts": [{"alias": "a"}]}
    config_path = _write_json(tmp_path / "config.json", payload)

    monkeypatch.delenv(env_name, raising=False)
    with pytest.raises(ValueError) as excinfo:
        module.load_config()
    assert str(excinfo.value) == missing_env_msg

    monkeypatch.setenv("LINGTAI_AGENT_DIR", str(tmp_path))
    monkeypatch.setenv(env_name, "missing.json")
    with pytest.raises(FileNotFoundError) as excinfo:
        module.load_config()
    assert (
        str(excinfo.value)
        == f"{label} config not found: {tmp_path / 'missing.json'}"
    )

    monkeypatch.setenv(env_name, "config.json")
    loaded = module.load_config()
    if returns_path:
        assert loaded == (payload, config_path)
    else:
        assert loaded == payload
