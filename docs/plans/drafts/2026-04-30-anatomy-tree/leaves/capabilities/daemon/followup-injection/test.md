# test: followup-injection

## Setup

- Agent with `capabilities=["daemon"]` and a working LLM backend.
- Clean working directory.

## Steps

1. `daemon(action="emanate", tasks=[{task: "Count to 3, printing each number on its own line. Then say 'task done'.", tools: []}], timeout=120)`
   → expect: `{ "status": "dispatched", "count": 1, "ids": ["em-1"] }`
   Record `em_id = "em-1"` (or the actual id from `ids`).

2. Wait ~10 seconds for the emanation to produce intermediate output.

3. `email(action="check")`
   → expect: at least one message where `source` is `"daemon"` and the body contains `[daemon:em-1]`. Example preview:
   ```
   [daemon:em-1]

   1
   2
   3
   ```

4. `daemon(action="ask", id="em-1", message="Also count to 5 instead of 3.")`
   → expect: `{ "status": "sent", "id": "em-1" }`

5. Poll `daemon(action="list")` until the emanation shows `status: "done"`. Note the `run_id`.
   → expect: `{ "emanations": [{ "id": "em-1", "status": "done", "run_id": "em-1-YYYYMMDD-HHMMSS-hex6", ... }], "running": 0 }`

6. `email(action="check")`
   → expect: a second `[daemon:em-1]` message (the terminal notification), distinct from the one in step 3. The terminal message should contain the emanation's final text output.

7. `bash(command="python3 -c \"import json; [print(json.loads(l)['kind']) for l in open('daemons/<run_id>/history/chat_history.jsonl')]\"")`
   → expect: a list of kinds, e.g.:
   ```
   task
   tool_results
   followup
   tool_results
   ```
   At minimum: `task` (first), `followup` (the ask), and at least one assistant or `tool_results` entry.

## Pass criteria (filesystem-observable only)

- **Intermediate notification**: `email(action="check")` shows at least one message with source `"daemon"` containing `[daemon:em-1]` before step 5 completes.
- **Terminal notification**: after step 5, another `[daemon:em-1]` message appears in inbox.
- **Chat history** at `daemons/<run_id>/history/chat_history.jsonl` contains entries with `kind: "task"`, `kind: "followup"`, and at least one assistant or `kind: "tool_results"` entry.
- **Follow-up content** appears verbatim in the `"kind": "followup"` JSONL entry.
- **daemon.json** `state` is `"done"`.

## Output template

```
INTERMEDIATE NOTIFICATIONS: N messages matching [daemon:em-1]
TERMINAL NOTIFICATION: present (yes/no), text_preview: "..."
CHAT HISTORY kinds found: [task, followup, tool_results, ...]
DAEMON.JSON state: done/failed/...
```
