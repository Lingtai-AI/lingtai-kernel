"""Contained parent interpreter used by detached-supervisor acceptance tests."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    # Keep this process focused on the real Agent/DaemonManager launch boundary;
    # its mock service is never used for execution because ownership transfers
    # to the detached supervisor before a session is created.
    parent = Path(sys.argv[1]).resolve()
    parent.mkdir(parents=True, exist_ok=True)
    from _daemon_helpers import make_daemon_agent
    agent = make_daemon_agent(parent, ["daemon"], working_dir_name="")
    agent.service.provider = "lingtai-supervisor-test-fake"
    agent.service.model = "fake-model"
    agent.service.api_key = "PARENT_EXIT_INLINE_SENTINEL"
    agent.service._base_url = None
    agent.service._provider_defaults = {}
    result = agent.get_capability("daemon").handle({
        "action": "emanate",
        "tasks": [{"task": "parent interpreter exits", "tools": []}],
        "timeout": 30,
    })
    if result.get("status") != "dispatched":
        raise SystemExit(f"manager did not dispatch: {result!r}")
    # The supervisor owns the run. Do not wait, reclaim, or pass a future back
    # to the parent; returning from this interpreter is the acceptance boundary.
    print(result["ids"][0], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
