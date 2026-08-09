"""Shared stdio entrypoint for curated MCP servers.

Every bundled MCP server's ``__main__.py`` ran the same three steps: configure
INFO logging to **stderr** (so logs never corrupt the JSON-RPC stdout channel),
``asyncio.run(serve())``, and swallow ``KeyboardInterrupt`` on Ctrl-C. This is
the single copy.

Since #812, the stderr logging setup also applies defense in depth against
credential leakage:

1. ``httpx``/``httpcore`` are pinned to WARNING so the INFO ``HTTP Request:``
   lines (whose URL embeds provider credentials such as the Telegram bot token
   in the request path) never reach stderr.
2. A redaction filter is attached to the root logger so any record that still
   contains a Telegram bot token or a ``api.telegram.org/bot<TOKEN>/...`` URL
   is rewritten to a fixed placeholder before any handler renders it.

Additional per-logger filters (``_protect_http_loggers``, #1298) attach the
same Telegram credential redaction directly to the HTTP client loggers for
belt-and-braces coverage when those loggers are reconfigured independently of
the root.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from collections.abc import Awaitable, Callable

# HTTP clients log one INFO ``HTTP Request: <METHOD> <url> ...`` line per
# request. Providers such as Telegram embed the live credential in the URL
# path (``https://api.telegram.org/bot<TOKEN>/<method>``), so at INFO level
# that line would copy the token to stderr — and from there into
# refresh-watcher diagnostics. Keep these clients quiet by default for every
# curated MCP server.
_QUIET_CLIENT_LOGGERS = ("httpx", "httpcore")

# Telegram bot tokens look like ``<bot_id>:<secret>`` with a 6-12 digit bot
# id and a long base64url-ish secret, e.g. ``123456789:AAH...``. The Bot API
# embeds the token in the request path.
_TELEGRAM_BOT_URL_RE = re.compile(
    r"(https://api\.telegram\.org/(?:file/)?bot)\d{6,12}:[A-Za-z0-9_-]{30,}"
)
_TELEGRAM_TOKEN_RE = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")

_REDACTED = "[REDACTED]"


class _RedactTelegramCredentials(logging.Filter):
    """Replace Telegram bot credentials before any handler renders them."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        redacted = _TELEGRAM_BOT_URL_RE.sub(r"\1" + _REDACTED, rendered)
        redacted = _TELEGRAM_TOKEN_RE.sub(_REDACTED, redacted)
        if redacted != rendered:
            record.msg = redacted
            record.args = ()
        return True


_telegram_credential_filter = _RedactTelegramCredentials()


# Matches the Telegram Bot API URL path segment that embeds the bot token
# (https://api.telegram.org/bot<id>:<secret>/<method> and the
# /file/bot<id>:<secret>/... download form). Keeps the "/bot" prefix visible so
# the redacted record remains recognizable. Mirrors the trajectory redactor in
# ``lingtai.kernel.trace_redaction``.
_TELEGRAM_BOT_URL_RE_SEGMENT = re.compile(
    r"(/bot)\d{6,12}:[A-Za-z0-9_-]{30,}(?![A-Za-z0-9_-])"
)
_TELEGRAM_BOT_URL_REDACTED = r"\1<REDACTED:telegram_bot_token>"


class _HttpUrlCredentialFilter(logging.Filter):
    """Redact credential-bearing URL path segments from HTTP client logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True  # malformed record: leave it to the normal formatter
        redacted = _TELEGRAM_BOT_URL_RE_SEGMENT.sub(_TELEGRAM_BOT_URL_REDACTED, message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _protect_http_loggers() -> None:
    """Attach the credential-redacting filter to HTTP client loggers."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).addFilter(_HttpUrlCredentialFilter())


def configure_stdio_logging() -> None:
    """Configure INFO stderr logging for a curated MCP stdio server.

    Logs go to stderr so they don't pollute the MCP stdio channel. HTTP client
    request logging is suppressed and Telegram-shaped credentials are redacted
    before the stderr handler renders them (defense in depth, issue #812).
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    for name in _QUIET_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    root_logger = logging.getLogger()
    if _telegram_credential_filter not in root_logger.filters:
        root_logger.addFilter(_telegram_credential_filter)
    # Filters on ancestor loggers are not consulted for records emitted by
    # descendant loggers (only the emitting logger's and each handler's filters
    # run), so the redaction filter must also sit on every root handler — the
    # layer every propagated record passes through before it reaches stderr.
    # Same pattern as the feishu addon's credential filter.
    for handler in root_logger.handlers:
        if _telegram_credential_filter not in handler.filters:
            handler.addFilter(_telegram_credential_filter)
    _protect_http_loggers()


def run_stdio_server_main(serve: Callable[[], Awaitable[None]]) -> None:
    """Configure stderr logging and run ``serve()`` until interrupted."""
    configure_stdio_logging()
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
