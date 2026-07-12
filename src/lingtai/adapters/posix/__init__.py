"""Narrow POSIX filesystem adapters."""

from .event_journal import PosixJsonlEventJournalAdapter
from .mail import PosixFilesystemMailAdapter

__all__ = ["PosixJsonlEventJournalAdapter", "PosixFilesystemMailAdapter"]
