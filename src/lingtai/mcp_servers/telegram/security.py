"""Secret-safe logging and error rendering for the Telegram MCP process."""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Mapping
from typing import Any

from lingtai.kernel.trace_redaction import redact_text

_MAX_SAFE_ERROR_CHARS = 2_000
_TELEGRAM_BOT_URL_TOKEN_RE = re.compile(
    r"(?<=/bot)\d{6,12}:[A-Za-z0-9_-]{30,}"
)
_TELEGRAM_BOT_TOKEN_PLACEHOLDER = "<REDACTED:telegram_bot_token>"
_FACTORY_LOCK = threading.RLock()
_installed_factory = None
_previous_factory = None
_installed_logger_handle = None
_previous_logger_handle = None
_installed_logger_filter = None
_previous_logger_filter = None
_installed_logger_call_handlers = None
_previous_logger_call_handlers = None
_previous_transport_levels: dict[str, int] | None = None


def _redact_telegram_text(text: str) -> str:
    # The kernel's generic token pattern deliberately requires a leading word
    # boundary. Bot API URLs use `/bot<TOKEN>`, so the adjacent `bot` prefix
    # needs this Telegram-specific, high-confidence path redaction first.
    without_url_token = _TELEGRAM_BOT_URL_TOKEN_RE.sub(
        _TELEGRAM_BOT_TOKEN_PLACEHOLDER,
        text,
    )
    return redact_text(without_url_token)


def safe_telegram_error(exc: BaseException) -> str:
    """Return a bounded readable exception string with credential shapes removed."""
    rendered = _redact_telegram_text(str(exc)).strip()
    if not rendered:
        rendered = type(exc).__name__
    return rendered[:_MAX_SAFE_ERROR_CHARS]


def _sanitize_value(value: Any) -> Any:
    """Redact string leaves while retaining normal %-format argument structure."""
    if isinstance(value, str):
        return _redact_telegram_text(value)
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, Mapping):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def _sanitize_record(record: logging.LogRecord) -> logging.LogRecord:
    """Mutate one newly-created record before any logger/handler can observe it."""
    # Let malformed logging interpolation raise normally. Redaction must not turn
    # logging defects into silently swallowed records.
    original_rendered = record.getMessage()
    expected_rendered = _redact_telegram_text(original_rendered)

    record.msg = _sanitize_value(record.msg)
    record.args = _sanitize_value(record.args)
    structured_rendered = record.getMessage()
    if structured_rendered != expected_rendered:
        # Some object/string formatting can introduce a credential only after
        # interpolation. In that case safety wins over retaining structured args.
        record.msg = expected_rendered
        record.args = ()

    if record.exc_info:
        exception_text = logging.Formatter().formatException(record.exc_info)
        record.exc_info = None
        record.exc_text = _redact_telegram_text(exception_text)
    elif record.exc_text:
        record.exc_text = _redact_telegram_text(record.exc_text)

    # ``Logger.makeRecord`` copies standard ``extra={...}`` fields only after the
    # process LogRecord factory returns.  Sanitize those normal structured fields
    # at the post-makeRecord/pre-handler boundary without stringifying values or
    # disturbing the already-preserved message, args, and exception semantics.
    for key, value in tuple(record.__dict__.items()):
        if key not in {"msg", "args", "exc_info", "exc_text"}:
            record.__dict__[key] = _sanitize_value(value)
    return record


class TelegramSecretRedactingFilter(logging.Filter):
    """Compatibility defense-in-depth; the process factory is the boundary."""

    _lingtai_telegram_secret_filter = True

    def filter(self, record: logging.LogRecord) -> bool:
        _sanitize_record(record)
        return True


def install_telegram_logging_safety() -> None:
    """Install chained process-wide record-creation and pre-handler redaction."""
    global _installed_factory, _previous_factory
    global _installed_logger_handle, _previous_logger_handle
    global _installed_logger_filter, _previous_logger_filter
    global _installed_logger_call_handlers, _previous_logger_call_handlers
    global _previous_transport_levels
    with _FACTORY_LOCK:
        if _installed_factory is not None:
            return
        previous = logging.getLogRecordFactory()
        previous_logger_handle = logging.Logger.handle
        previous_logger_filter = logging.Logger.filter
        previous_logger_call_handlers = logging.Logger.callHandlers

        def telegram_safe_factory(*args, **kwargs):
            return _sanitize_record(previous(*args, **kwargs))

        def telegram_safe_logger_handle(logger, record):
            # `extra` is copied by Logger.makeRecord after the factory returns.
            # Preserve any pre-existing handle customization and sanitize before it
            # observes the record. A second boundary below catches enrichment that
            # customization adds before ordinary handler dispatch.
            return previous_logger_handle(logger, _sanitize_record(record))

        def telegram_safe_logger_filter(logger, record):
            # Standard or captured-prior ``handle`` reaches this virtual boundary
            # after its enrichment and immediately before ordinary logger filters.
            # Chain the captured filter method exactly once with a clean record.
            return previous_logger_filter(logger, _sanitize_record(record))

        def telegram_safe_logger_call_handlers(logger, record):
            # Standard Logger.handle (including a captured prior override which
            # chains it) calls this virtual boundary only after logger filtering and
            # prior enrichment. Sanitize one final time, then dispatch exactly once.
            return previous_logger_call_handlers(logger, _sanitize_record(record))

        telegram_safe_factory._lingtai_telegram_secret_factory = True  # type: ignore[attr-defined]
        telegram_safe_logger_handle._lingtai_telegram_secret_logger_handle = True  # type: ignore[attr-defined]
        telegram_safe_logger_filter._lingtai_telegram_secret_logger_filter = True  # type: ignore[attr-defined]
        telegram_safe_logger_call_handlers._lingtai_telegram_secret_logger_call_handlers = True  # type: ignore[attr-defined]
        _previous_factory = previous
        _installed_factory = telegram_safe_factory
        _previous_logger_handle = previous_logger_handle
        _installed_logger_handle = telegram_safe_logger_handle
        _previous_logger_filter = previous_logger_filter
        _installed_logger_filter = telegram_safe_logger_filter
        _previous_logger_call_handlers = previous_logger_call_handlers
        _installed_logger_call_handlers = telegram_safe_logger_call_handlers
        _previous_transport_levels = {
            name: logging.getLogger(name).level for name in ("httpx", "httpcore")
        }
        # httpx/httpcore INFO records include the complete credential-bearing URL.
        for logger_name in ("httpx", "httpcore"):
            logging.getLogger(logger_name).setLevel(logging.WARNING)
        logging.setLogRecordFactory(telegram_safe_factory)
        logging.Logger.callHandlers = telegram_safe_logger_call_handlers
        logging.Logger.filter = telegram_safe_logger_filter
        logging.Logger.handle = telegram_safe_logger_handle


def restore_telegram_logging_safety() -> None:
    """Exactly restore captured boundaries and transport levels when still current."""
    global _installed_factory, _previous_factory
    global _installed_logger_handle, _previous_logger_handle
    global _installed_logger_filter, _previous_logger_filter
    global _installed_logger_call_handlers, _previous_logger_call_handlers
    global _previous_transport_levels
    with _FACTORY_LOCK:
        if _installed_factory is None:
            return
        installed = _installed_factory
        previous = _previous_factory
        installed_logger_handle = _installed_logger_handle
        previous_logger_handle = _previous_logger_handle
        installed_logger_filter = _installed_logger_filter
        previous_logger_filter = _previous_logger_filter
        installed_logger_call_handlers = _installed_logger_call_handlers
        previous_logger_call_handlers = _previous_logger_call_handlers
        levels = _previous_transport_levels or {}
        # Do not overwrite a boundary or transport level another component
        # deliberately installed after ours. Exact restoration applies only to
        # each process surface where our own wrapper/value is still current.
        if logging.getLogRecordFactory() is installed and previous is not None:
            logging.setLogRecordFactory(previous)
        if (
            installed_logger_handle is not None
            and logging.Logger.handle is installed_logger_handle
            and previous_logger_handle is not None
        ):
            logging.Logger.handle = previous_logger_handle
        if (
            installed_logger_filter is not None
            and logging.Logger.filter is installed_logger_filter
            and previous_logger_filter is not None
        ):
            logging.Logger.filter = previous_logger_filter
        if (
            installed_logger_call_handlers is not None
            and logging.Logger.callHandlers is installed_logger_call_handlers
            and previous_logger_call_handlers is not None
        ):
            logging.Logger.callHandlers = previous_logger_call_handlers
        for logger_name, level in levels.items():
            transport_logger = logging.getLogger(logger_name)
            if transport_logger.level == logging.WARNING:
                transport_logger.setLevel(level)
        _installed_factory = None
        _previous_factory = None
        _installed_logger_handle = None
        _previous_logger_handle = None
        _installed_logger_filter = None
        _previous_logger_filter = None
        _installed_logger_call_handlers = None
        _previous_logger_call_handlers = None
        _previous_transport_levels = None


__all__ = [
    "TelegramSecretRedactingFilter",
    "install_telegram_logging_safety",
    "restore_telegram_logging_safety",
    "safe_telegram_error",
]
