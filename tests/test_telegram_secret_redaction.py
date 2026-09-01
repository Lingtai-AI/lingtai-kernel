"""Secret-safety gates for Telegram HTTP logs and public MCP errors."""
from __future__ import annotations

import io
import json
import logging
import sys

import anyio
import mcp.types as types
import pytest
from mcp import Client

from lingtai.mcp_servers.telegram import security as security_module
from lingtai.mcp_servers.telegram import server
from lingtai.mcp_servers.telegram.manager import TelegramManager
from lingtai.mcp_servers.telegram.security import (
    TelegramSecretRedactingFilter,
    install_telegram_logging_safety,
    restore_telegram_logging_safety,
    safe_telegram_error,
)
from tests._notification_store_helpers import FakeNotificationStore

_FAKE_TOKEN = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcd_1234567890"
_FAKE_URL = f"https://api.telegram.org/bot{_FAKE_TOKEN}/getMe"
_PLACEHOLDER = "<REDACTED:telegram_bot_token>"


def _payload(result):
    assert result.content
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)


def test_safe_error_and_compatibility_filter_redact_message_and_traceback():
    rendered = safe_telegram_error(RuntimeError(f"POST {_FAKE_URL} failed"))
    assert _FAKE_TOKEN not in rendered
    assert _PLACEHOLDER in rendered

    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1,
        "HTTP Request: POST %s 500", (_FAKE_URL,), None,
    )
    assert TelegramSecretRedactingFilter().filter(record) is True
    assert _FAKE_TOKEN not in record.getMessage()
    assert _PLACEHOLDER in record.getMessage()

    try:
        raise RuntimeError(f"download failed at {_FAKE_URL}")
    except RuntimeError:
        traced = logging.LogRecord(
            "lingtai.mcp_servers.telegram", logging.ERROR, __file__, 1,
            "request failed", (), sys.exc_info(),
        )
    assert TelegramSecretRedactingFilter().filter(traced) is True
    assert traced.exc_info is None
    assert traced.exc_text is not None
    assert _FAKE_TOKEN not in traced.exc_text
    assert _PLACEHOLDER in traced.exc_text
    assert "RuntimeError" in traced.exc_text


def test_process_factory_precedes_named_handlers_and_later_root_replacement():
    root = logging.getLogger()
    named = logging.getLogger("lingtai.telegram.nonpropagating")
    root_stream = io.StringIO()
    named_stream = io.StringIO()
    late_root = logging.StreamHandler(root_stream)
    named_handler = logging.StreamHandler(named_stream)
    old_root_handlers = list(root.handlers)
    old_root_level = root.level
    old_named_handlers = list(named.handlers)
    old_named_propagate = named.propagate
    old_named_level = named.level
    previous_factory = logging.getLogRecordFactory()
    old_levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    try:
        install_telegram_logging_safety()
        first_factory = logging.getLogRecordFactory()
        install_telegram_logging_safety()
        assert logging.getLogRecordFactory() is first_factory
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING

        # Both a non-propagating named handler and a root handler installed only
        # after the boundary must receive an already-sanitized LogRecord.
        named.handlers[:] = [named_handler]
        named.propagate = False
        named.setLevel(logging.INFO)
        root.handlers[:] = [late_root]
        root.setLevel(logging.INFO)
        named.info("named POST %s", _FAKE_URL)
        logging.getLogger("lingtai.telegram.propagating").warning("root POST %s", _FAKE_URL)
        assert _FAKE_TOKEN not in named_stream.getvalue()
        assert _PLACEHOLDER in named_stream.getvalue()
        assert _FAKE_TOKEN not in root_stream.getvalue()
        assert _PLACEHOLDER in root_stream.getvalue()
    finally:
        restore_telegram_logging_safety()
        root.handlers[:] = old_root_handlers
        root.setLevel(old_root_level)
        named.handlers[:] = old_named_handlers
        named.propagate = old_named_propagate
        named.setLevel(old_named_level)
    assert logging.getLogRecordFactory() is previous_factory
    assert {name: logging.getLogger(name).level for name in old_levels} == old_levels


def test_process_factory_preserves_structured_args_and_sanitizes_traceback():
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("lingtai.telegram.capture")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    try:
        install_telegram_logging_safety()
        logger.handlers[:] = [Capture()]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info(
            "structured %(url)s %(items)s",
            {"url": _FAKE_URL, "items": [_FAKE_URL, "safe"]},
        )
        try:
            raise RuntimeError(f"traceback {_FAKE_URL}")
        except RuntimeError:
            logger.exception("failed %s", _FAKE_URL)
    finally:
        restore_telegram_logging_safety()
        logger.handlers[:] = old_handlers
        logger.level = old_level
        logger.propagate = old_propagate

    structured, traced = records
    assert isinstance(structured.args, dict)
    assert isinstance(structured.args["items"], list)
    assert _FAKE_TOKEN not in structured.getMessage()
    assert _PLACEHOLDER in structured.getMessage()
    assert traced.exc_info is None
    assert traced.exc_text is not None
    assert _FAKE_TOKEN not in traced.getMessage() + traced.exc_text
    assert _PLACEHOLDER in traced.getMessage()
    assert _PLACEHOLDER in traced.exc_text


def test_standard_logging_extra_fields_are_sanitized_before_late_handlers():
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("lingtai.telegram.extra")
    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    try:
        install_telegram_logging_safety()
        # Normal logging copies `extra` only after LogRecordFactory returns.
        logger.handlers[:] = [Capture()]
        logger.propagate = False
        logger.setLevel(logging.INFO)
        logger.info(
            "safe message",
            extra={
                "provider_url": _FAKE_URL,
                "nested": {"urls": [_FAKE_URL, "safe"], "count": 2},
                "opaque": 7,
            },
        )
    finally:
        restore_telegram_logging_safety()
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate

    [record] = records
    assert record.getMessage() == "safe message"
    assert record.args == ()
    safe_url = f"https://api.telegram.org/bot{_PLACEHOLDER}/getMe"
    assert record.provider_url == safe_url
    assert record.nested == {"urls": [safe_url, "safe"], "count": 2}
    assert record.opaque == 7
    assert _FAKE_TOKEN not in repr(record.__dict__)


def test_pre_handler_boundary_chains_prior_override_and_restores_exactly():
    original_handle = logging.Logger.handle
    calls: list[str] = []

    def prior(logger, record):
        calls.append(logger.name)
        # This enrichment occurs after the installed handle wrapper's first
        # sanitation pass. Final standard dispatch must still sanitize it.
        record.prior_custom = _FAKE_URL
        return original_handle(logger, record)

    logging.Logger.handle = prior
    logger = logging.getLogger("lingtai.telegram.chained")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers[:] = [_Capture()]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        install_telegram_logging_safety()
        installed = logging.Logger.handle
        assert installed is not prior
        logger.info("hello %s", "world", extra={"normal_extra": _FAKE_URL})
        assert calls == ["lingtai.telegram.chained"]
        [record] = records
        assert record.getMessage() == "hello world"
        assert record.args == ("world",)
        assert _FAKE_TOKEN not in record.prior_custom
        assert _PLACEHOLDER in record.prior_custom
        assert _FAKE_TOKEN not in record.normal_extra
        assert len(records) == 1
        restore_telegram_logging_safety()
        assert logging.Logger.handle is prior
    finally:
        restore_telegram_logging_safety()
        logging.Logger.handle = original_handle
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def test_later_nonchaining_make_record_replacement_cannot_bypass_extra_redaction():
    original_make_record = logging.Logger.makeRecord
    logger = logging.getLogger("lingtai.telegram.later-make-record")
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    old_handlers = list(logger.handlers)
    old_level = logger.level
    old_propagate = logger.propagate
    logger.handlers[:] = [_Capture()]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    install_telegram_logging_safety()

    def later(logger_self, *args, **kwargs):
        return original_make_record(logger_self, *args, **kwargs)

    try:
        logging.Logger.makeRecord = later
        logger.info("safe", extra={"url": _FAKE_URL})
        [record] = records
        assert _FAKE_TOKEN not in record.url
    finally:
        logging.Logger.makeRecord = original_make_record
        restore_telegram_logging_safety()
        logger.handlers[:] = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate


def test_restore_does_not_clobber_later_logger_handle_replacement():
    original_handle = logging.Logger.handle
    previous_factory = logging.getLogRecordFactory()
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    install_telegram_logging_safety()
    installed = logging.Logger.handle

    def later(logger, record):
        return installed(logger, record)

    try:
        logging.Logger.handle = later
        restore_telegram_logging_safety()
        assert logging.Logger.handle is later
    finally:
        logging.Logger.handle = original_handle
        logging.setLogRecordFactory(previous_factory)
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        security_module._installed_factory = None
        security_module._previous_factory = None
        security_module._installed_logger_handle = None
        security_module._previous_logger_handle = None
        security_module._previous_transport_levels = None


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore"])
@pytest.mark.parametrize(
    "later_level",
    [logging.CRITICAL, logging.ERROR, logging.INFO, logging.NOTSET],
)
def test_restore_preserves_later_transport_level_owner(logger_name, later_level):
    logger = logging.getLogger(logger_name)
    original_level = logger.level
    try:
        logger.setLevel(logging.DEBUG)
        install_telegram_logging_safety()
        assert logger.level == logging.WARNING
        logger.setLevel(later_level)
        restore_telegram_logging_safety()
        assert logger.level == later_level
        # State was cleared exactly once: a repeated restore cannot reinterpret a
        # later value as this component's installed transport level.
        restore_telegram_logging_safety()
        assert logger.level == later_level
    finally:
        restore_telegram_logging_safety()
        logger.setLevel(original_level)


def test_restore_is_idempotent_and_exact():
    previous_factory = logging.getLogRecordFactory()
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}
    try:
        # Exact normal restoration must include a prior WARNING level; ownership
        # cannot be inferred merely from the value changing during installation.
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        expected = {name: logging.getLogger(name).level for name in levels}
        install_telegram_logging_safety()
        assert logging.getLogRecordFactory() is not previous_factory
        restore_telegram_logging_safety()
        restore_telegram_logging_safety()
        assert logging.getLogRecordFactory() is previous_factory
        assert {name: logging.getLogger(name).level for name in levels} == expected
    finally:
        restore_telegram_logging_safety()
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


def test_serve_restores_process_boundary_when_server_construction_fails(monkeypatch, tmp_path):
    lifecycle: list[str] = []

    class FakeManager:
        def start(self):
            lifecycle.append("start")

        def stop(self):
            lifecycle.append("stop")

    fake_manager = FakeManager()
    monkeypatch.setattr(server, "build_manager", lambda: (fake_manager, tmp_path))

    def fail_build(_manager):
        raise RuntimeError("synthetic server construction failure")

    monkeypatch.setattr(server, "build_server", fail_build)
    previous_factory = logging.getLogRecordFactory()
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}

    with pytest.raises(RuntimeError, match="synthetic server construction failure"):
        anyio.run(server.serve)

    assert lifecycle == ["start", "stop"]
    assert logging.getLogRecordFactory() is previous_factory
    assert {name: logging.getLogger(name).level for name in levels} == levels


def test_serve_rolls_back_partial_manager_start_before_restoring_boundary(monkeypatch, tmp_path):
    lifecycle: list[str] = []
    previous_factory = logging.getLogRecordFactory()
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore")}

    class FakeManager:
        def start(self):
            lifecycle.append("start")
            assert logging.getLogRecordFactory() is not previous_factory
            raise RuntimeError("synthetic partial listener start failure")

        def stop(self):
            lifecycle.append("stop")
            assert logging.getLogRecordFactory() is not previous_factory

    monkeypatch.setattr(server, "build_manager", lambda: (FakeManager(), tmp_path))

    def fail_build(manager):
        assert manager is None
        raise RuntimeError("synthetic server construction after failed start")

    monkeypatch.setattr(server, "build_server", fail_build)
    with pytest.raises(RuntimeError, match="construction after failed start"):
        anyio.run(server.serve)

    assert lifecycle == ["start", "stop"]
    assert logging.getLogRecordFactory() is previous_factory
    assert {name: logging.getLogger(name).level for name in levels} == levels


def test_partial_start_uncertain_cleanup_is_retained_retried_before_redaction_restore(
    monkeypatch,
    tmp_path,
):
    lifecycle: list[str] = []
    previous_factory = logging.getLogRecordFactory()
    previous_logger_handle = logging.Logger.handle

    class FakeManager:
        def __init__(self):
            self.stop_calls = 0

        def start(self):
            lifecycle.append("start")
            assert logging.getLogRecordFactory() is not previous_factory
            assert logging.Logger.handle is not previous_logger_handle
            raise RuntimeError("synthetic partial start")

        def stop(self):
            self.stop_calls += 1
            lifecycle.append(f"stop-{self.stop_calls}")
            assert logging.getLogRecordFactory() is not previous_factory
            assert logging.Logger.handle is not previous_logger_handle
            if self.stop_calls == 1:
                raise RuntimeError("synthetic uncertain cleanup")

    manager = FakeManager()
    monkeypatch.setattr(server, "build_manager", lambda: (manager, tmp_path))

    def fail_build(candidate):
        assert candidate is None
        raise RuntimeError("synthetic terminal server construction")

    monkeypatch.setattr(server, "build_server", fail_build)
    with pytest.raises(RuntimeError, match="terminal server construction"):
        anyio.run(server.serve)

    assert lifecycle == ["start", "stop-1", "stop-2"]
    assert logging.getLogRecordFactory() is previous_factory
    assert logging.Logger.handle is previous_logger_handle


def test_uncertain_final_cleanup_keeps_process_redaction_installed(monkeypatch, tmp_path):
    previous_factory = logging.getLogRecordFactory()

    class FakeManager:
        def start(self):
            raise RuntimeError("synthetic partial start")

        def stop(self):
            raise RuntimeError("synthetic still-live worker")

    monkeypatch.setattr(server, "build_manager", lambda: (FakeManager(), tmp_path))
    monkeypatch.setattr(
        server,
        "build_server",
        lambda _manager: (_ for _ in ()).throw(RuntimeError("synthetic build failure")),
    )
    try:
        with pytest.raises(RuntimeError, match="synthetic build failure"):
            anyio.run(server.serve)
        # Booleans only: the process boundary remains because both cleanup attempts
        # failed to prove that a token-bearing worker stopped.
        assert logging.getLogRecordFactory() is not previous_factory
    finally:
        restore_telegram_logging_safety()
    assert logging.getLogRecordFactory() is previous_factory


def test_manager_caught_public_error_is_redacted(tmp_path):
    class _FailingService:
        def list_accounts(self):
            raise RuntimeError(f"provider rejected GET {_FAKE_URL}")

    manager = TelegramManager(
        _FailingService(),
        working_dir=tmp_path,
        on_inbound=lambda _message: None,
        notification_store=FakeNotificationStore(),
    )

    result = manager.handle({"action": "accounts"})
    assert _FAKE_TOKEN not in result["error"]
    assert _PLACEHOLDER in result["error"]
    assert "provider rejected" in result["error"]


def test_public_telegram_tool_error_is_redacted(monkeypatch):
    def _fail(_manager, _arguments):
        raise RuntimeError(f"provider rejected POST {_FAKE_URL}")

    monkeypatch.setattr(server, "handle_telegram", _fail)

    async def _run():
        async with Client(server.build_server(object())) as client:
            return await client.call_tool("telegram", {})

    payload = _payload(anyio.run(_run))
    assert payload["status"] == "error"
    assert payload["error_type"] == "RuntimeError"
    assert _FAKE_TOKEN not in payload["error"]
    assert _PLACEHOLDER in payload["error"]
    assert "provider rejected" in payload["error"]


def test_prior_handle_enrichment_is_clean_before_logger_filter_and_handler_once():
    original_handle = logging.Logger.handle
    logger = logging.getLogger("lingtai.telegram.prior-enrichment-filter")
    filter_observations: list[bool] = []
    handler_observations: list[bool] = []
    prior_calls = 0

    def prior(logger_self, record):
        nonlocal prior_calls
        prior_calls += 1
        record.prior_enrichment = _FAKE_URL
        return original_handle(logger_self, record)

    class ObserveFilter(logging.Filter):
        def filter(self, record):
            filter_observations.append(_FAKE_TOKEN in repr(record.__dict__))
            return True

    class ObserveHandler(logging.Handler):
        def emit(self, record):
            handler_observations.append(_FAKE_TOKEN in repr(record.__dict__))

    old_handlers = list(logger.handlers)
    old_filters = list(logger.filters)
    old_level = logger.level
    old_propagate = logger.propagate
    logging.Logger.handle = prior
    logger.handlers[:] = [ObserveHandler()]
    logger.filters[:] = [ObserveFilter()]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        install_telegram_logging_safety()
        logger.info("safe")
        assert prior_calls == 1
        assert filter_observations == [False]
        assert handler_observations == [False]
    finally:
        restore_telegram_logging_safety()
        logging.Logger.handle = original_handle
        logger.handlers[:] = old_handlers
        logger.filters[:] = old_filters
        logger.setLevel(old_level)
        logger.propagate = old_propagate
