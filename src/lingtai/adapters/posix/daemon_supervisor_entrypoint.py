"""Ordinary importable/executable entrypoint for the detached daemon supervisor.

``PosixDaemonSupervisorAdapter.spawn_detached`` launches
``<python_executable> -m lingtai.adapters.posix.daemon_supervisor_entrypoint
<encoded-request>``, where ``<encoded-request>`` is the compact deterministic
JSON payload produced by
``lingtai.kernel.daemon_supervisor.encode_request``. This module is the only
thing on argv; it decodes the request and hands off to the Core-owned
``run_supervisor`` for the actual run-manifest read, emanation execution,
terminal-state commit, and notification publish.

This module is process/transport mechanism, not technology-neutral Core
logic, so it lives beside ``PosixDaemonSupervisorAdapter`` under
``adapters/posix`` rather than in the kernel — it is only ever invoked as a
subprocess entrypoint via ``python -m``. It performs no policy of its own.
"""
from __future__ import annotations

import json
import os
import sys

from lingtai.kernel.daemon_supervisor import decode_request
from lingtai.tools.daemon.supervisor_runtime import run_supervisor


_MAX_CAPSULE_BYTES = 4 * 1024 * 1024


def _read_capsule() -> dict | None:
    """Consume the bounded inherited one-shot capsule and close its descriptor."""
    raw_fd = os.environ.pop("LINGTAI_DAEMON_CAPSULE_FD", None)
    if raw_fd is None:
        return None
    try:
        fd = int(raw_fd)
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_CAPSULE_BYTES:
                raise ValueError("daemon runtime capsule exceeds size limit")
            chunks.append(chunk)
        os.close(fd)
        value = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("daemon capsule must be an object")
        return value
    except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        try:
            os.close(fd)  # type: ignore[possibly-undefined]
        except (OSError, UnboundLocalError):
            pass
        return None


def main(argv: list[str]) -> int:
    """Decode the single encoded-request argument and run the supervisor.

    Expects exactly one argument: the ``encode_request`` payload. Fails
    loudly (propagates ``decode_request``'s ``ValueError``) on a malformed
    payload rather than silently doing nothing, so a transport defect is
    immediately visible instead of a supervisor process that spawned but
    never actually supervised anything.
    """
    if len(argv) != 1:
        raise SystemExit(
            "usage: python -m lingtai.adapters.posix.daemon_supervisor_entrypoint "
            "<encoded-request>"
        )
    request = decode_request(argv[0])
    run_supervisor(request, capsule=_read_capsule())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


__all__ = ["main"]
