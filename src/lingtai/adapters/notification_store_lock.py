"""Platform selection for the Core-owned notification mutation-lock Port."""

from __future__ import annotations

import os

from lingtai.kernel.notification_store._mutation_lock import (
    NotificationMutationLockPort,
)


def select_notification_store_lock() -> NotificationMutationLockPort:
    """Return the native cross-process lock for this platform."""
    if os.name == "posix":
        from .posix.notification_store_lock import PosixNotificationStoreLockAdapter

        return PosixNotificationStoreLockAdapter()
    if os.name == "nt":
        from .windows.notification_store_lock import WindowsNotificationStoreLockAdapter

        return WindowsNotificationStoreLockAdapter()
    raise NotImplementedError(
        f"notification Store mutation locking is unsupported on {os.name!r}"
    )
