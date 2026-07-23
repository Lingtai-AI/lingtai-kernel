"""Regression tests for package-internal logging setup."""

from __future__ import annotations

import logging
import threading

import lingtai.kernel.logging as kernel_logging
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
        replacement = next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "file"
        )
        replacement.flush()

        assert replacement is not file_handler
        assert file_handler._closed
        assert not (first_dir / "agent.log").read_text()
        assert "new target" in (second_dir / "agent.log").read_text()
    finally:
        _close_handlers(logger)


def test_setup_logging_failed_retarget_preserves_handler_and_retry_recovers(tmp_path):
    logger = logging.getLogger("test.lingtai.logging.retarget_failure")
    _close_handlers(logger)
    first_dir = tmp_path / "first"
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    obstacle = blocked_dir / "agent.log"
    obstacle.mkdir()

    try:
        setup_logging(log_dir=first_dir, logger_name=logger.name)
        file_handler = next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "file"
        )
        original_filename = file_handler.baseFilename
        original_stream = file_handler.stream
        original_formatter = file_handler.formatter
        original_level = file_handler.level

        try:
            setup_logging(log_dir=blocked_dir, logger_name=logger.name)
        except IsADirectoryError:
            pass
        else:
            raise AssertionError("retargeting to a directory should fail")

        assert file_handler in logger.handlers
        assert file_handler.baseFilename == original_filename
        assert file_handler.stream is original_stream
        assert file_handler.formatter is original_formatter
        assert file_handler.level == original_level
        logger.info("old target remains usable")
        file_handler.flush()
        assert "old target remains usable" in (first_dir / "agent.log").read_text()

        obstacle.rmdir()
        setup_logging(log_dir=blocked_dir, logger_name=logger.name)
        replacement = next(
            handler
            for handler in logger.handlers
            if getattr(handler, "_lingtai_handler", None) == "file"
        )
        logger.info("retry reached new target")
        replacement.flush()

        assert replacement is not file_handler
        assert file_handler._closed
        assert "retry reached new target" in obstacle.read_text()
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


def test_setup_logging_serializes_concurrent_handler_discovery(monkeypatch):
    logger = logging.getLogger("test.lingtai.logging.concurrent")
    _close_handlers(logger)
    discovery_barrier = threading.Barrier(2)
    original_owned_handlers = kernel_logging._owned_handlers

    def synchronized_discovery(target, kind):
        handlers = original_owned_handlers(target, kind)
        if kind == "console" and not handlers:
            try:
                discovery_barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
        return handlers

    monkeypatch.setattr(kernel_logging, "_owned_handlers", synchronized_discovery)
    errors = []

    def run_setup():
        try:
            setup_logging(logger_name=logger.name)
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=run_setup)
        for _ in range(2)
    ]

    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        assert all(not thread.is_alive() for thread in threads)
        assert not errors
        assert len(original_owned_handlers(logger, "console")) == 1
    finally:
        _close_handlers(logger)
