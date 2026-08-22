"""Static declaration data owned by the System official tool plugin.

The declared-host kernel owns registration and the reserved ``manual`` slot;
this package owns its public identity and operational action order.  It does
not discover, register, or mount anything.
"""
from __future__ import annotations

SYSTEM_DECLARED_ACTIONS: tuple[str, ...] = (
    "refresh",
    "target_refresh",
    "sleep",
    "lull",
    "interrupt",
    "suspend",
    "cpr",
    "clear",
    "nirvana",
    "presets",
    "name_set",
    "name_nickname",
)
