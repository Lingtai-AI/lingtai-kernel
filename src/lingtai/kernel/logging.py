"""Package-internal logging for lingtai."""
from __future__ import annotations

import logging
import threading
from pathlib import Path

_logger: logging.Logger | None = None
_SETUP_LOCK = threading.RLock()
_HANDLER_OWNER_ATTR = "_lingtai_handler"
_CONSOLE_HANDLER = "console"
_FILE_HANDLER = "file"


def _owned_handlers(logger: logging.Logger, kind: str) -> list[logging.Handler]:
    return [
        handler
        for handler in logger.handlers
        if getattr(handler, _HANDLER_OWNER_ATTR, None) == kind
    ]


def _replace_owned_handler(
    logger: logging.Logger,
    handlers: list[logging.Handler],
    replacement: logging.Handler,
) -> logging.Handler:
    for handler in handlers:
        logger.removeHandler(handler)
        handler.close()
    logger.addHandler(replacement)
    return replacement


def setup_logging(
    verbose: bool = False,
    log_dir: Path | str | None = None,
    logger_name: str = "lingtai",
) -> logging.Logger:
    """Initialize the package logger.

    Args:
        verbose: If True, set console to DEBUG; otherwise INFO.
        log_dir: Directory for log files. None leaves file logging unchanged.
        logger_name: Logger name (default: "lingtai").
    """
    global _logger
    with _SETUP_LOCK:
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)

        console_handlers = _owned_handlers(logger, _CONSOLE_HANDLER)
        if console_handlers and isinstance(console_handlers[0], logging.StreamHandler):
            ch = console_handlers[0]
            for duplicate in console_handlers[1:]:
                logger.removeHandler(duplicate)
                duplicate.close()
        else:
            ch = logging.StreamHandler()
            setattr(ch, _HANDLER_OWNER_ATTR, _CONSOLE_HANDLER)
            ch = _replace_owned_handler(logger, console_handlers, ch)
        ch.setLevel(logging.DEBUG if verbose else logging.INFO)
        if ch.formatter is None:
            fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                    datefmt="%H:%M:%S")
            ch.setFormatter(fmt)

        if log_dir is not None:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            filename = (log_path / "agent.log").resolve()
            file_handlers = _owned_handlers(logger, _FILE_HANDLER)
            if file_handlers and isinstance(file_handlers[0], logging.FileHandler):
                fh = file_handlers[0]
                if Path(fh.baseFilename) != filename:
                    replacement = logging.FileHandler(filename)
                    setattr(replacement, _HANDLER_OWNER_ATTR, _FILE_HANDLER)
                    replacement.setLevel(fh.level)
                    replacement.setFormatter(fh.formatter)
                    fh = _replace_owned_handler(logger, file_handlers, replacement)
                else:
                    for duplicate in file_handlers[1:]:
                        logger.removeHandler(duplicate)
                        duplicate.close()
            else:
                fh = logging.FileHandler(filename)
                setattr(fh, _HANDLER_OWNER_ATTR, _FILE_HANDLER)
                fh = _replace_owned_handler(logger, file_handlers, fh)
            fh.setLevel(logging.DEBUG)
            if fh.formatter is None:
                fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
                fh.setFormatter(fmt)

        _logger = logger
        return logger


def get_logger() -> logging.Logger:
    """Get the package logger. Creates a default if setup_logging() was not called."""
    global _logger
    if _logger is None:
        _logger = setup_logging()
    return _logger
