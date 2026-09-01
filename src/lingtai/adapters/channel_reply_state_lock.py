"""Platform selector for channel_reply state mutation serialization."""

from __future__ import annotations

import os
import sys

from lingtai.kernel.channel_reply._mutation_lock import ChannelReplyMutationLockPort


class UnsupportedChannelReplyPlatform(NotImplementedError):
    """The deliberate fail-closed result for a platform outside V1 scope."""


def _platform_identity() -> tuple[str, str]:
    """Return the runtime identity through a narrow deterministic test seam."""
    return os.name, sys.platform


def select_channel_reply_state_lock() -> ChannelReplyMutationLockPort:
    """Return the native lock on supported V1 platforms; fail closed otherwise."""
    os_name, sys_platform = _platform_identity()
    if (os_name, sys_platform) == ("posix", "darwin"):
        from .posix.channel_reply_state_lock import PosixChannelReplyStateLockAdapter

        return PosixChannelReplyStateLockAdapter()
    raise UnsupportedChannelReplyPlatform(
        "channel_reply is closed: file-backed submission is supported on macOS "
        f"only (unsupported platform os.name={os_name!r}, "
        f"sys.platform={sys_platform!r})"
    )
