"""Deterministic Codex/OpenCode-family CLI for detached resume acceptance."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path


def main() -> int:
    name = os.environ.get("FAKE_DAEMON_CLI", "codex")
    args = sys.argv[1:]
    resume = "resume" in args or "--session" in args
    completion_path = None
    run_id = None
    for index, arg in enumerate(args[:-1]):
        if arg != "-c":
            continue
        value = args[index + 1]
        for key in ("LINGTAI_DAEMON_COMPLETION_FILE", "LINGTAI_DAEMON_RUN_ID"):
            match = re.search(
                rf'{key}\s*=\s*("(?:\\.|[^"])*")',
                value,
            )
            if not match:
                continue
            parsed = json.loads(match.group(1))
            if key == "LINGTAI_DAEMON_COMPLETION_FILE":
                completion_path = parsed
            else:
                run_id = parsed
    completion_status = os.environ.get("FAKE_DAEMON_FINISH_STATUS")
    if resume and completion_path and completion_status:
        payload = {
            "schema": "lingtai.daemon_completion.v1",
            "status": completion_status,
            "run_id": run_id or os.environ.get("LINGTAI_DAEMON_RUN_ID"),
        }
        if completion_status != "done":
            payload["reason"] = "fake follow-up status"
        target = Path(completion_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")
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
