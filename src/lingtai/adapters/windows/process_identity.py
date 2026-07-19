"""Windows process-incarnation identities used by daemon ownership checks.

PID values are reusable. Like the POSIX sibling this module returns ``None``
whenever the operating system cannot provide a bounded, stable observation;
callers must then refuse ownership-sensitive signalling (fail-closed).

The identity format is ``windows:<creation_filetime>`` — the shared creation-
time token owned by :mod:`lingtai.adapters.windows._win32`, matching the shell
adapter's process references. Liveness/creation observation goes through
``OpenProcess``; it never signals, because ``os.kill`` on Windows *terminates*
for every non-console-control signal (including the POSIX-style ``kill(pid,
0)`` liveness idiom).

Consumers do not import this module directly: they keep importing
``lingtai.adapters.posix.process_identity``, whose ``process_identity``
delegates here on ``win32`` (see the naming note there).
"""
from __future__ import annotations

import os


def process_identity(pid: int) -> str | None:
    """Capture a stable Windows process incarnation token, never PID alone."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    if os.name != "nt":
        raise OSError("Windows process identity requires Windows")
    from lingtai.adapters.windows import _win32

    return _win32.process_creation_identity(pid)


__all__ = ["process_identity"]
