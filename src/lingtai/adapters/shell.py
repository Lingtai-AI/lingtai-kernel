"""Composition selector for the canonical ``shell`` capability.

The implementation package remains ``lingtai.tools.bash`` for PR1 durable and
packaging compatibility.  Platform identity belongs at this outer selector:
:func:`resolve_shell_kind` turns platform + discovered executables + config
override (``LINGTAI_SHELL`` or the ``shell_kind`` setup kwarg) into one
:class:`~lingtai.tools.bash._shell_dialect.ShellKind`, and
:func:`select_shell_dialect` builds the matching dialect.  Spawn argv itself
lives in the single ``ShellKind``-keyed authority in ``_shell_dialect.py`` so
the model-facing description and ``subprocess`` can never drift apart.
"""
from __future__ import annotations

import os
import platform
import shutil

from lingtai.tools.bash._shell_dialect import ShellDialect, ShellKind
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


def _discover_pwsh() -> str | None:
    """Probe the PowerShell 7 executable exactly like the adapter does.

    ``PowerShellDialect`` performs the same ``shutil.which`` probe when it is
    constructed; keeping this copy in the classifier means the kind choice and
    the dialect construction always agree (the batch-1 pwsh discovery PR
    deepens both at once).
    """
    return shutil.which("pwsh")


def resolve_shell_kind(
    *,
    os_name: str | None = None,
    env: dict[str, str] | None = None,
    shell_setting: object = None,
) -> ShellKind:
    """Resolve the active :class:`ShellKind` from platform, discovery, config.

    Precedence:
      1. explicit ``shell_setting`` (init.json ``manifest.capabilities.shell``
         ``shell_kind`` kwarg), then the ``LINGTAI_SHELL`` environment variable;
      2. platform default: POSIX on Unix; on Windows PowerShell when ``pwsh``
         is discoverable, then Git Bash, then cmd.exe as the last resort;
         WSL is opt-in only (never auto-selected).

    The kind stays ``POSIX`` on macOS -- the Darwin-specific work (resolving
    the user's login shell to ``/bin/zsh`` or ``/bin/bash`` and spawning
    ``[shell, "-lc", script]`` with the Homebrew PATH guarantee) lives in the
    POSIX dialect/``make_invocation_for_kind`` so the durable kind vocabulary
    never splits between classifier and dialect.

    Unknown override values fall back to the platform default instead of
    failing setup, so a stale config never disables the shell capability.
    """
    os_name = os.name if os_name is None else os_name
    env = os.environ if env is None else env
    for override in (shell_setting, env.get("LINGTAI_SHELL")):
        kind = ShellKind.coerce(override)
        if kind is not None:
            return kind
    if os_name == "posix":
        return ShellKind.POSIX
    if os_name == "nt":
        if _discover_pwsh():
            return ShellKind.POWERSHELL
        from .windows.gitbash import discover_git_bash
        if discover_git_bash():
            return ShellKind.GITBASH
        return ShellKind.CMD
    raise NotImplementedError(f"shell kind is unsupported on platform {os_name!r}")


def _dialect_for(kind: ShellKind) -> ShellDialect:
    """Map one ShellKind to its dialect implementation (lazy imports)."""
    if kind is ShellKind.POSIX:
        return PosixBashDialect()
    if kind is ShellKind.POWERSHELL:
        from .windows.powershell import PowerShellDialect
        return PowerShellDialect()
    if kind is ShellKind.CMD:
        from .windows.cmd import CmdDialect
        return CmdDialect()
    if kind is ShellKind.GITBASH:
        from .windows.gitbash import GitBashDialect
        return GitBashDialect()
    if kind is ShellKind.WSL:
        from .windows.wsl import WslDialect
        return WslDialect()
    raise NotImplementedError(f"no dialect for shell kind {kind!r}")


def select_shell_dialect(shell_kind: ShellKind | None = None) -> ShellDialect:
    """Select the dialect for ``shell_kind`` (or the resolved default)."""
    kind = shell_kind or resolve_shell_kind()
    return _dialect_for(kind)
