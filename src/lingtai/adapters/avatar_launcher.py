"""Platform selector for the avatar-local launcher Port."""
from __future__ import annotations

import os
import sys

from lingtai.tools.avatar._launcher import AvatarLauncherPort


def select_avatar_launcher() -> AvatarLauncherPort:
    if os.name == "posix":
        from lingtai.adapters.posix.avatar_launcher import PosixAvatarLauncherAdapter
        return PosixAvatarLauncherAdapter()
    if sys.platform == "win32":
        from .windows.avatar_launcher import WindowsAvatarLauncherAdapter
        return WindowsAvatarLauncherAdapter()
    raise NotImplementedError(
        f"No production avatar launcher for platform {sys.platform!r} (os.name={os.name!r})"
    )


__all__ = ["select_avatar_launcher"]
