"""Detached owner entrypoint for one post-terminal CLI resume generation."""
from __future__ import annotations

import json
import os
import sys

_MAX_CAPSULE_BYTES = 4 * 1024 * 1024


def _read_capsule() -> dict:
    raw = os.environ.pop("LINGTAI_DAEMON_CAPSULE_FD", None)
    if raw is None:
        return {}
    fd = int(raw)
    chunks = []
    total = 0
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_CAPSULE_BYTES:
                raise ValueError("daemon runtime capsule exceeds size limit")
            chunks.append(chunk)
    finally:
        os.close(fd)
    value = json.loads(b"".join(chunks).decode("utf-8"))
    return value if isinstance(value, dict) else {}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: daemon_resume_owner <manifest> <run_id> <generation>")
    capsule = _read_capsule()
    from lingtai.tools.daemon.supervisor_runtime import run_resume_owner
    run_resume_owner(argv[0], argv[1], argv[2], capsule=capsule)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
