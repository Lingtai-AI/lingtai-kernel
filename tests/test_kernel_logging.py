"""Regression tests for package-internal logging setup."""

from __future__ import annotations

import logging

from lingtai.kernel.logging import setup_logging


def _close_handlers(logger: logging.Logger) -> None:
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def test_setup_logging_preserves_application_handlers_and_is_idempotent(tmp_path):
    logger = logging.getLogger("test.lingtai.logging.idempotent")
    _close_handlers(logger)
    application_handler = logging.StreamHandler()
    logger.addHandler(application_handler)

    try:
        first = setup_logging(log_dir=tmp_path, logger_name=logger.name)
        owned = [
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None)
        ]

        second = setup_logging(log_dir=tmp_path, logger_name=logger.name)

        assert first is second is logger
        assert application_handler in logger.handlers
        assert len(logger.handlers) == 3
        assert [
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None)
        ] == owned
    finally:
        _close_handlers(logger)


def test_setup_logging_retargets_owned_file_handler(tmp_path):
    logger = logging.getLogger("test.lingtai.logging.retarget")
    _close_handlers(logger)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    try:
        setup_logging(log_dir=first_dir, logger_name=logger.name)
        file_handler = next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "file"
        )

        setup_logging(log_dir=second_dir, logger_name=logger.name)
        logger.info("new target")
        file_handler.flush()

        assert next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "file"
        ) is file_handler
        assert not (first_dir / "agent.log").read_text()
        assert "new target" in (second_dir / "agent.log").read_text()
    finally:
        _close_handlers(logger)


def test_setup_logging_updates_console_level_and_none_keeps_file_handler(tmp_path):
    logger = logging.getLogger("test.lingtai.logging.reconfigure")
    _close_handlers(logger)

    try:
        setup_logging(verbose=False, log_dir=tmp_path, logger_name=logger.name)
        console_handler = next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "console"
        )
        file_handler = next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "file"
        )

        setup_logging(verbose=True, log_dir=None, logger_name=logger.name)

        assert console_handler.level == logging.DEBUG
        assert file_handler in logger.handlers
    finally:
        _close_handlers(logger)


def test_setup_logging_closes_duplicate_owned_file_handler(tmp_path):
    logger = logging.getLogger("test.lingtai.logging.duplicate")
    _close_handlers(logger)

    try:
        setup_logging(log_dir=tmp_path, logger_name=logger.name)
        duplicate = logging.FileHandler(tmp_path / "duplicate.log")
        setattr(duplicate, "_lingtai_handler", "file")
        logger.addHandler(duplicate)

        setup_logging(log_dir=tmp_path, logger_name=logger.name)

        assert duplicate not in logger.handlers
        assert duplicate._closed
    finally:
        _close_handlers(logger)
