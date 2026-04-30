# test: pre-send-health

## Setup

- Agent with `capabilities=["daemon"]` and a working LLM backend.
- Clean working directory — no pre-existing `daemons/`.

## Steps

### Test A: Valid batch succeeds

1. `daemon(action="emanate", tasks=[{task: "Say 'hello' then say 'task done'", tools: ["read"]}], timeout=60)`
   → expect: `{ "status": "dispatched", "count": 1, "ids": ["em-1"] }`

2. `daemon(action="list")` — note the `run_id` for step 3-5.
   → expect: `{ "emanations": [{ "id": "em-1", "status": "running", "run_id": "em-1-YYYYMMDD-HHMMSS-hex6", ... }], "running": 1 }`

3. `bash(command="python3 -c \"import json; d=json.load(open('daemons/<run_id>/daemon.json')); print(d['state'], d['task'][:40])\"")`
   → expect: `running Say 'hello' then say 'task done'`

4. `bash(command="head -c 100 daemons/<run_id>/.prompt")`
   → expect: text starting with `You are a daemon emanation (分神)` followed by the task text.

5. `bash(command="stat -f '%Sm' daemons/<run_id>/.heartbeat")` (macOS) or `bash(command="stat -c '%y' daemons/<run_id>/.heartbeat")` (Linux)
   → expect: a timestamp close to the current time (within the last few seconds).

### Test B: Invalid tool refuses batch

6. `daemon(action="emanate", tasks=[{task: "test", tools: ["nonexistent_tool_xyz"]}], timeout=60)`
   → expect: `{ "status": "error", "message": "..." }` where message contains `nonexistent_tool_xyz` or `Unknown tools`.

7. `bash(command="ls daemons/ | wc -l")`
   → expect: same count as after Test A (no new folder created).

### Test C: Blacklisted tool refuses batch

8. `daemon(action="emanate", tasks=[{task: "test", tools: ["daemon"]}], timeout=60)`
   → expect: `{ "status": "error", "message": "..." }` — daemon cannot be nested.

9. `bash(command="ls daemons/ | wc -l")`
   → expect: same count as Test B (no new folder created).

### Test D: Folder uniqueness

10. After Test A's emanation completes, run a second emanation and compare folders.
    `daemon(action="emanate", tasks=[{task: "Say 'done'", tools: []}], timeout=60)`
    → expect: `{ "status": "dispatched", ... }`
    `bash(command="ls daemons/")`
    → expect: two folders with different `run_id` suffixes (different timestamp and/or hex hash).

## Pass criteria (filesystem-observable only)

- **Test A**: `daemons/<run_id>/` contains `daemon.json` (valid JSON, `state: "running"`), `.heartbeat` (exists), `.prompt` (non-empty), `history/`, `logs/`.
- **Test B**: No new folder created after the failed request. `ls daemons/ | wc -l` unchanged.
- **Test C**: Same as Test B — no new folder, error response.
- **Test D**: Each successful emanation produces a unique folder. Folder names match `em-N-<YYYYMMDD-HHMMSS>-<hex6>`.

## Output template

```
TEST A (valid batch):
  run_id: em-1-YYYYMMDD-HHMMSS-hex6
  daemon.json state: running
  .prompt preview: "You are a daemon emanation..."
  .heartbeat mtime: <recent timestamp>
  folder contents: daemon.json, .prompt, .heartbeat, history/, logs/

TEST B (invalid tool):
  error: yes, message contains "nonexistent_tool_xyz"
  daemon_folder_count_after: N (same as before)

TEST C (blacklisted tool):
  error: yes
  daemon_folder_count_after: N (same as Test B)

TEST D (uniqueness):
  folder_1: em-1-YYYYMMDD-HHMMSS-hex1
  folder_2: em-2-YYYYMMDD-HHMMSS-hex2
  unique: yes (different timestamps/hashes)
```
