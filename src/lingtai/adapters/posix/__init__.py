"""Narrow POSIX filesystem adapters."""

from .event_journal import PosixJsonlEventJournalAdapter

__all__ = ["PosixJsonlEventJournalAdapter"]
