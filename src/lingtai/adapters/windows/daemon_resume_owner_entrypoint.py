"""Windows detached owner entrypoint for one post-terminal CLI resume generation.

Mirror of ``lingtai.adapters.posix.daemon_resume_owner_entrypoint``: only the
capsule acquisition differs (inherited pipe HANDLE converted to a CRT fd,
``strict=True`` like the POSIX ``int(raw)`` fail-loud path, then the POSIX
module's mechanism-free read loop). The resume-generation ownership logic in
``run_resume_owner`` is shared runtime code that selects the platform's
supervisor adapter internally.
"""
from __future__ import annotations

import os
import sys

from lingtai.adapters.windows.daemon_supervisor import adopt_capsule_handle_to_fd


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: daemon_resume_owner <manifest> <run_id> <generation>")
    if os.name != "nt":
        raise OSError("Windows daemon resume owner requires Windows")
    adopt_capsule_handle_to_fd(strict=True)
    from lingtai.adapters.posix.daemon_resume_owner_entrypoint import _read_capsule
    capsule = _read_capsule()
    from lingtai.tools.daemon.supervisor_runtime import run_resume_owner
    run_resume_owner(argv[0], argv[1], argv[2], capsule=capsule)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


__all__ = ["main"]
