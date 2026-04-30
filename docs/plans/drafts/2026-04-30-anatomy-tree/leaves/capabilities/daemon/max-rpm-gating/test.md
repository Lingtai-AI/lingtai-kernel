# test: max-rpm-gating

## Setup

- Agent with `capabilities=["daemon"]` configured with `max_emanations=2` (low limit for test).
- Clean working directory.

## Steps

### Test A: Within limit succeeds

1. `daemon(action="emanate", tasks=[{task: "Sleep 30 seconds then say 'task done'", tools: []}, {task: "Sleep 30 seconds then say 'task done'", tools: []}], timeout=60)`
   → expect: `{ "status": "dispatched", "count": 2, "ids": ["em-1", "em-2"] }`

2. `daemon(action="list")`
   → expect: `{ "emanations": [...2 items...], "running": 2, "max_emanations": 2 }`

### Test B: Over limit refuses

3. `daemon(action="emanate", tasks=[{task: "test", tools: []}], timeout=60)` — while both from step 1 are still running.
   → expect: `{ "status": "error", "message": "..." }` where message contains `running=2`, `requested=1`, `max=2` (exact i18n text may vary).

4. `bash(command="ls daemons/ | wc -l")`
   → expect: `2` (no new folder from the refused request).

### Test C: Reclaim frees capacity

5. `daemon(action="reclaim")`
   → expect: `{ "status": "reclaimed", "cancelled": 2 }` (cancelled ≥ 2).

6. `daemon(action="list")`
   → expect: `{ "emanations": [], "running": 0, "max_emanations": 2 }`

7. `daemon(action="emanate", tasks=[{task: "Say 'task done'", tools: []}], timeout=60)`
   → expect: `{ "status": "dispatched", "count": 1, "ids": ["em-1"] }` (ID resets to 1 after reclaim).

8. `bash(command="ls daemons/ | wc -l")`
   → expect: `3` (2 folders from Test A + 1 new from Test C).

## Pass criteria (filesystem-observable only)

- **Test A**: `daemons/` contains exactly 2 new folders. Both `daemon.json` files show `state: "running"`.
- **Test B**: No new folder created. `ls daemons/ | wc -l` still equals 2.
- **Test C**: After reclaim, `running == 0`. After re-emanate, a 3rd folder appears. Total folder count is 3.
- **`daemon(action="list")`** always reports `max_emanations: 2`.

## Output template

```
TEST A (within limit):
  dispatched: 2
  running: 2
  max_emanations: 2
  folders: [em-1-..., em-2-...]

TEST B (over limit):
  error: yes, message contains "running=2, requested=1, max=2"
  folder_count: 2

TEST C (reclaim + retry):
  reclaimed: 2
  running_after_reclaim: 0
  re_dispatched: 1
  folder_count: 3
  max_emanations: 2
```
