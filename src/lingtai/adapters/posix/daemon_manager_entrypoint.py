"""Resident POSIX daemon manager entrypoint for high-concurrency runs."""
from __future__ import annotations

import sys
from pathlib import Path

from lingtai.adapters.posix.daemon_manager import run_manager


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit(
            "usage: python -m lingtai.adapters.posix.daemon_manager_entrypoint "
            "<agent-working-dir> <pool-size>"
        )
    run_manager(Path(argv[0]), pool_size=int(argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

