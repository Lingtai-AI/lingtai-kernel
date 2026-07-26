"""Regression tests for Feishu SDK logging on MCP stdio."""
from __future__ import annotations

import logging
import sys

from lingtai.mcp_servers.feishu import account as account_module


def test_lark_sdk_logs_use_stderr_without_replacing_handlers(capsys, monkeypatch):
    sdk_logger = logging.getLogger("Lark")
    original_handlers = list(sdk_logger.handlers)
    original_level = sdk_logger.level
    original_propagate = sdk_logger.propagate
    original_disabled = sdk_logger.disabled

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(logging.Formatter("SDK %(levelname)s %(message)s"))
    preserved_handler = logging.NullHandler()
    fake_lark = object()

    try:
        sdk_logger.handlers = [stdout_handler, preserved_handler]
        sdk_logger.setLevel(logging.INFO)
        sdk_logger.propagate = True
        sdk_logger.disabled = False
        monkeypatch.setattr(account_module, "lark", fake_lark)

        assert account_module._import_lark() is fake_lark
        assert account_module._import_lark() is fake_lark

        assert sdk_logger.handlers == [stdout_handler, preserved_handler]
        assert stdout_handler.stream is sys.stderr
        assert sdk_logger.propagate is False

        sdk_logger.info("stdio invariant")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "SDK INFO stdio invariant" in captured.err
    finally:
        sdk_logger.handlers = original_handlers
        sdk_logger.setLevel(original_level)
        sdk_logger.propagate = original_propagate
        sdk_logger.disabled = original_disabled
