"""Deterministic fake `codex exec --json`-shaped CLI for detached-supervisor tests.

Emits the same JSONL event shapes `_run_codex_emanation` (supervisor.py)
parses: an `item.completed` agent_message followed by a terminal
`turn.completed`. `--sleep N` makes the process live long enough for a test
to observe it running (and, if reclaimed, to observe it actually die) before
it would otherwise exit on its own.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sleep", type=float, default=0.0)
    args, _ = parser.parse_known_args()

    report = os.environ.get("LINGTAI_FAKE_CLI_REPORT")
    if report:
        with open(report, "w", encoding="utf-8") as stream:
            json.dump({
                "argv": sys.argv[1:],
                "openai_api_key": os.environ.get("OPENAI_API_KEY"),
            }, stream)

    if args.sleep:
        time.sleep(args.sleep)

    print(json.dumps({"type": "thread.started", "thread_id": "fake-thread-1"}), flush=True)
    print(json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": "fake-codex-output"},
    }), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
