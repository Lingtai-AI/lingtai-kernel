"""Composition selector for the canonical ``shell`` capability.

The implementation package remains ``lingtai.tools.bash`` for PR1 durable and
packaging compatibility.  Platform identity belongs at this outer selector.
"""
from __future__ import annotations

import os
import platform

from lingtai.tools.bash._shell_dialect import ShellDialect
from .posix.bash import PosixBashDialect


def _one_line(value: object) -> str:
    """Normalize host metadata for one-line agent-facing tool prose."""
    return " ".join(str(value or "").split())


def describe_host_os() -> str:
    """Return a truthful human-readable host OS name and version."""
    system = _one_line(platform.system())
    if system == "Darwin":
        version = _one_line(platform.mac_ver()[0])
        if version:
            return f"macOS {version}"
        kernel = _one_line(platform.release())
        return f"macOS (Darwin kernel {kernel})" if kernel else "macOS"

    if system == "Linux":
        try:
            os_release = platform.freedesktop_os_release()
        except OSError:
            os_release = {}
        pretty_name = _one_line(os_release.get("PRETTY_NAME"))
        if pretty_name:
            return pretty_name
        name = _one_line(os_release.get("NAME")) or "Linux"
        version = _one_line(os_release.get("VERSION_ID") or os_release.get("VERSION"))
        return f"{name} {version}".strip()

    if system == "Windows":
        release = _one_line(platform.release())
        version = _one_line(platform.version())
        label = f"Windows {release}".strip()
        return f"{label} ({version})" if version and version != release else label

    system = system or _one_line(os.name) or "unknown"
    release = _one_line(platform.release())
    return f"{system} {release}".strip()


def select_shell_dialect() -> ShellDialect:
    if os.name == "posix":
        return PosixBashDialect()
    if os.name == "nt":
        from .windows.powershell import PowerShellDialect
        return PowerShellDialect()
    raise NotImplementedError(f"shell dialect is unsupported on platform {os.name!r}")
