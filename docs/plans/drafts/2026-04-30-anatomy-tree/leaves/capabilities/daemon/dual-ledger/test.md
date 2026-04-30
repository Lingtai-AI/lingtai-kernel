# test: dual-ledger

## Setup

- Agent with `capabilities=["daemon"]` and a working LLM backend.
- Clean working directory — no pre-existing `daemons/` or `logs/token_ledger.jsonl`.

## Steps

1. `daemon(action="emanate", tasks=[{task: "Print the word 'hello' then say 'task done'", tools: ["read"]}], timeout=120)`
   → expect: `{ "status": "dispatched", "count": 1, "ids": ["em-1"] }`

2. Poll `daemon(action="list")` until the emanation shows `status: "done"` or `"failed"`.
   → expect: `{ "emanations": [{ "id": "em-1", "status": "done", "run_id": "em-1-YYYYMMDD-HHMMSS-hex6", ... }], "running": 0, "max_emanations": 4 }`
   Note the `run_id` value for subsequent steps.

3. `bash(command="cat daemons/<run_id>/logs/token_ledger.jsonl")`
   → expect: one or more JSON lines, e.g.:
   ```
   {"source":"daemon","em_id":"em-1","run_id":"em-1-...","input":850,"output":42,"thinking":0,"cached":0,"model":"...","ts":"..."}
   ```

4. `bash(command="grep '<run_id>' logs/token_ledger.jsonl")`
   → expect: one or more lines matching the `run_id` from step 2, with the same `source:"daemon"` tag.

5. `bash(command="python3 -c \"import json; d=json.load(open('daemons/<run_id>/daemon.json')); print(d['tokens'])\"")`
   → expect: `{'input': N, 'output': N, 'thinking': N, 'cached': N}` where N > 0 for at least `input`.

## Pass criteria (filesystem-observable only)

- **Daemon ledger exists** at `daemons/<run_id>/logs/token_ledger.jsonl` with ≥1 line.
- **Each daemon ledger entry** contains `"source": "daemon"`, `"em_id"`, `"run_id"`, `"input"`, `"output"`, `"thinking"`, `"cached"` fields.
- **Parent ledger** at `logs/token_ledger.jsonl` has ≥1 line containing `"source": "daemon"` and the same `run_id`.
- **daemon.json** `tokens` field shows non-zero cumulative counts matching the sum of daemon ledger entries.
- **No entry** has all four numeric fields equal to zero.

## Output template

```
DAEMON LEDGER: daemons/<run_id>/logs/token_ledger.jsonl
  lines: N
  sample_entry: { ... }

PARENT LEDGER: logs/token_ledger.jsonl
  daemon_tagged_lines: N
  sample_entry: { ... }

DAEMON.JSON tokens: { input: X, output: Y, thinking: Z, cached: W }
MATCH: yes/no (sum of daemon ledger entries == daemon.json tokens)
```
