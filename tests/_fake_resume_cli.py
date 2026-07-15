"""Deterministic Codex/OpenCode-family CLI for detached resume acceptance."""
from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    name = os.environ.get("FAKE_DAEMON_CLI", "codex")
    args = sys.argv[1:]
    resume = "resume" in args or "--session" in args
    calls_path = os.environ.get("FAKE_DAEMON_RESUME_CALLS")
    if resume and calls_path:
        with open(calls_path, "a", encoding="utf-8") as calls:
            calls.write(json.dumps({"name": name, "pid": os.getpid()}) + "\n")
    if os.environ.get("FAKE_DAEMON_CLI_SLEEP"):
        time.sleep(float(os.environ["FAKE_DAEMON_CLI_SLEEP"]))
    if name == "codex":
        if not resume:
            print(json.dumps({"type": "thread.started", "thread_id": "fake-codex-session"}), flush=True)
        text = "fake-codex-followup" if resume else "fake-codex-primary"
        print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": text}}), flush=True)
        print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
    else:
        text = "fake-opencode-followup" if resume else "fake-opencode-primary"
        if not resume:
            print(json.dumps({"type": "session", "id": "fake-opencode-session"}), flush=True)
        print(json.dumps({"type": "text", "text": text, "part": {"text": text}}), flush=True)
        print(json.dumps({"type": "step_finish"}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
