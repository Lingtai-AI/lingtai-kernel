"""Optional libc hint to return freed-but-retained heap pages to the OS.

On macOS, libmalloc keeps the address space of a freed magazine region mapped
and dirty, showing up in ``vmmap -summary`` as ``MALLOC_MEDIUM (empty)``. A
long-lived daemon execution child that has processed a few large tool outputs
can carry hundreds of MB of such pages: the memory is free to the allocator but
still counted against the process RSS.

``malloc_zone_pressure_relief(zone, goal)`` is Apple's documented request to
release that space back. This module exposes it as an opt-in hook.

**Measured result: it did not work.** On macOS 14 (Darwin 23.4, arm64,
CPython 3.13) a synthetic 250MB transient produced 244.7MB of
``MALLOC_MEDIUM (empty)``, and ``malloc_zone_pressure_relief(NULL, 0)``
returned 0 bytes released and moved RSS by ~1MB. A realistic
tool-output-shaped workload moved empty-region dirty pages 30.9MB -> 22.3MB
but RSS only 57MB -> 56MB. So this is **disabled by default** and gated behind
``LINGTAI_DAEMON_MEMORY_RELIEF=1`` purely so the hypothesis can be re-measured
on a live daemon (different workload, macOS version, or allocation mix) without
shipping a per-tool-batch cost to everyone.

Do not enable it in production without measuring that specific daemon first.
"""
from __future__ import annotations

import os

_UNRESOLVED = object()
_relief = _UNRESOLVED


def enabled() -> bool:
    """True when the operator opted in via ``LINGTAI_DAEMON_MEMORY_RELIEF=1``."""
    return os.environ.get("LINGTAI_DAEMON_MEMORY_RELIEF", "").strip() == "1"


def _resolve():
    """Bind ``malloc_zone_pressure_relief`` once; None where unavailable."""
    global _relief
    if _relief is not _UNRESOLVED:
        return _relief
    _relief = None
    try:
        import ctypes
        import ctypes.util
        path = ctypes.util.find_library("c")
        if path:
            libc = ctypes.CDLL(path)
            fn = getattr(libc, "malloc_zone_pressure_relief", None)
            if fn is not None:
                fn.restype = ctypes.c_size_t
                fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
                _relief = fn
    except Exception:
        # Non-Darwin, no libc handle, or a hardened runtime that refuses the
        # dlopen — the hint is strictly best-effort, so stay silent.
        _relief = None
    return _relief


def relieve() -> int:
    """Ask libc to return empty heap regions. Returns bytes released (0 if noop).

    Never raises: this is a hint, and a failure to give it is not an error.
    """
    if not enabled():
        return 0
    fn = _resolve()
    if fn is None:
        return 0
    try:
        # NULL zone == every zone; goal 0 == release as much as possible.
        return int(fn(None, 0))
    except Exception:
        return 0
