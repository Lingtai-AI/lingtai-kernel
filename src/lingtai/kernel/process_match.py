"""Process-command matcher for LingTai agent runs."""
from __future__ import annotations

import ntpath
import os
import posixpath


def _is_absolute_anywhere(path: str) -> bool:
    """True if ``path`` is absolute under POSIX or Windows path syntax.

    ``ps command=`` text can carry either OS's path shape regardless of the
    OS this matcher happens to run on (e.g. Windows-shaped test fixtures
    exercised on POSIX CI), so relative-vs-absolute must be judged
    syntactically rather than via ``os.path.isabs``, which only knows the
    host OS's own convention.
    """
    return posixpath.isabs(path) or ntpath.isabs(path)


def match_agent_run(cmdline: str, working_dir: str) -> str | None:
    """Return the launch form if ``cmdline`` is an agent run for ``working_dir``.

    The matcher is intentionally conservative for the console-script and legacy
    forms: ``lingtai-agent`` / ``lingtai`` must be the command itself or the
    basename of a path. The module form is separate because real launches look
    like ``<python> -m lingtai run <dir>``.

    Residual limitation: ``ps command=`` is a flat string, not the original argv
    vector. A non-LingTai process can still match if its argument text is shaped
    exactly like an absolute LingTai program path followed by ``run <dir>``.

    Program anchoring accepts both path separators: Windows process tables
    report ``C:\\...\\Scripts\\lingtai-agent.exe run <dir>`` with backslashes,
    and a backslash immediately before the program name is as much a path
    anchor there as ``/`` is on POSIX. ``os.path.normpath`` on each platform
    normalizes the trailing directory the same way for the equality check.

    Relative ``<dir>`` arguments are intentionally unsupported. Symlink
    aliases are resolved with ``realpath`` only when ``working_dir`` is
    absolute on the host OS; a foreign-OS-shaped path (e.g. a Windows
    ``C:\\...`` path observed while running on POSIX) falls back to plain
    ``normpath`` equality, since ``realpath`` would otherwise resolve it
    against the wrong filesystem convention.
    """
    host_absolute = os.path.isabs(working_dir)
    target = (
        os.path.realpath(os.path.normpath(working_dir))
        if host_absolute
        else os.path.normpath(working_dir)
    )
    for token, label, program_anchored in (
        (" -m lingtai run ", "module", False),
        ("lingtai-agent run ", "console", True),
        ("lingtai run ", "legacy", True),
    ):
        idx = cmdline.find(token)
        while idx != -1:
            if (not program_anchored) or idx == 0 or cmdline[idx - 1] in ("/", "\\"):
                tail = cmdline[idx + len(token):].strip()
                if tail and _is_absolute_anywhere(tail):
                    resolved = (
                        os.path.realpath(os.path.normpath(tail))
                        if host_absolute
                        else os.path.normpath(tail)
                    )
                    if resolved == target:
                        return label
            idx = cmdline.find(token, idx + 1)
    return None
