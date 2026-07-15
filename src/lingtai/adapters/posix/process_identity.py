"""POSIX process-incarnation identities used by daemon ownership checks.

PID values are reusable.  This module deliberately returns ``None`` when the
operating system cannot provide a bounded, stable observation; callers must
then refuse ownership-sensitive signalling.
"""
from __future__ import annotations

import os
import ctypes
import ctypes.util
import subprocess
import sys
from pathlib import Path


MAXCOMLEN = 16


def _linux_process_identity(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    boot_id_path: Path = Path("/proc/sys/kernel/random/boot_id"),
) -> str | None:
    """Return Linux boot-id plus ``/proc/<pid>/stat`` start ticks.

    The command name is enclosed in parentheses and may itself contain
    spaces, so parsing starts after the final closing parenthesis rather than
    using a plain whitespace split over the whole record.
    """
    try:
        stat = (proc_root / str(pid) / "stat").read_text(encoding="utf-8")
        close = stat.rfind(")")
        if close < 0:
            return None
        fields_after_comm = stat[close + 1 :].split()
        # Field 22 (starttime) is field 20 after the comm field has been
        # removed: state is index 0, starttime is index 19.
        start_ticks = fields_after_comm[19]
        boot_id = boot_id_path.read_text(encoding="utf-8").strip()
    except (OSError, IndexError, ValueError):
        return None
    if not boot_id or not start_ticks:
        return None
    return f"linux:{boot_id}:{start_ticks}"


def _ps_process_identity(pid: int) -> str | None:
    """Return bounded Darwin/BSD ``ps`` start time and parent PID."""
    try:
        result = subprocess.run(
            ["ps", "-o", "lstart=", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    return f"ps:{line}" if result.returncode == 0 and line else None


class _DarwinProcBsdInfo(ctypes.Structure):
    """The stable prefix and birth timeval of Darwin ``proc_bsdinfo``."""

    # Darwin's public proc_info ABI uses 4-byte alignment for this structure.
    _pack_ = 4
    # Python 3.14 requires an explicit layout when _pack_ is present.
    _layout_ = "ms"

    _fields_ = [
        ("pbi_flags", ctypes.c_uint32),
        ("pbi_status", ctypes.c_uint32),
        ("pbi_xstatus", ctypes.c_uint32),
        ("pbi_pid", ctypes.c_uint32),
        ("pbi_ppid", ctypes.c_uint32),
        ("pbi_uid", ctypes.c_uint32),
        ("pbi_gid", ctypes.c_uint32),
        ("pbi_ruid", ctypes.c_uint32),
        ("pbi_rgid", ctypes.c_uint32),
        ("pbi_svuid", ctypes.c_uint32),
        ("pbi_svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("pbi_comm", ctypes.c_char * MAXCOMLEN),
        ("pbi_name", ctypes.c_char * (2 * MAXCOMLEN)),
        ("pbi_nfiles", ctypes.c_uint32),
        ("pbi_pgid2", ctypes.c_uint32),
        ("pbi_pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("pbi_nice", ctypes.c_int32),
        ("pbi_start_tvsec", ctypes.c_uint64),
        ("pbi_start_tvusec", ctypes.c_uint64),
    ]


def _darwin_process_identity(pid: int) -> str | None:
    """Read Darwin's kernel birth timeval through bounded ``libproc``."""
    if sys.platform != "darwin":
        return None
    try:
        library_name = ctypes.util.find_library("proc") or "/usr/lib/libproc.dylib"
        libproc = ctypes.CDLL(library_name)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
        info = _DarwinProcBsdInfo()
        size = ctypes.sizeof(info)
        # PROC_PIDTBSDINFO is the documented BSD process-info flavor.
        copied = proc_pidinfo(pid, 3, 0, ctypes.byref(info), size)
        if copied < size or info.pbi_pid != pid:
            return None
        if info.pbi_start_tvsec <= 0 or info.pbi_start_tvusec >= 1_000_000:
            return None
        return f"darwin:{info.pbi_start_tvsec}:{info.pbi_start_tvusec}"
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def process_identity(pid: int) -> str | None:
    """Capture a stable process incarnation token, never PID alone."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    if sys.platform.startswith("linux"):
        return _linux_process_identity(pid)
    if sys.platform == "darwin":
        return _darwin_process_identity(pid)
    return None


def process_identity_matches(pid: int, saved_identity: object) -> bool:
    """Return true only for the same observed process incarnation."""
    return (
        isinstance(saved_identity, str)
        and bool(saved_identity)
        and process_identity(pid) == saved_identity
    )
