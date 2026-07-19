"""Windows entrypoint mirror for the detached daemon supervisor.

``WindowsDaemonSupervisorAdapter.spawn_detached`` launches
``<python_executable> -m lingtai.adapters.windows.daemon_supervisor_entrypoint
<encoded-request>``. Only the capsule acquisition differs from the POSIX
entrypoint: the one-shot capsule arrives as an inherited pipe HANDLE number in
``LINGTAI_DAEMON_CAPSULE_HANDLE``, which is converted to a CRT fd via
``msvcrt.open_osfhandle`` and republished under the shared fd wire name. The
decode/read/dispatch logic is then delegated verbatim to the POSIX entrypoint
module, whose read loop is mechanism-free ``os.read`` code.

The handle conversion is lenient (``strict=False``) to mirror the POSIX
supervisor read path, where a malformed capsule descriptor degrades to "no
capsule supplied" rather than crashing the supervisor before it can commit a
truthful terminal state.
"""
from __future__ import annotations

import os
import sys

from lingtai.adapters.windows.daemon_supervisor import adopt_capsule_handle_to_fd


def main(argv: list[str]) -> int:
    """Adopt the capsule handle, then delegate to the shared POSIX entrypoint."""
    if os.name != "nt":
        raise OSError("Windows daemon supervisor entrypoint requires Windows")
    adopt_capsule_handle_to_fd(strict=False)
    from lingtai.adapters.posix.daemon_supervisor_entrypoint import main as _shared_main

    return _shared_main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["main"]
