---
timeout: 180
---

# Test: Agent State Machine

## Setup

1. Locate an active agent's working directory (e.g. `~/.lingtai/<project>/<agent>/`).
2. Verify the agent is running: `cat <workdir>/.agent.heartbeat` should show a recent epoch timestamp.

## Steps

1. **Read `.agent.json`** — confirm it exists and contains `address`, `agent_name`, `started_at`.
   ```bash
   cat <workdir>/.agent.json | python3 -m json.tool
   ```

2. **Check heartbeat freshness** — the `.agent.heartbeat` file should contain a float epoch within the last 5 seconds.
   ```bash
   python3 -c "import time; hb=float(open('<workdir>/.agent.heartbeat').read().strip()); print(f'stale: {time.time()-hb:.1f}s')"
   ```

3. **Verify ACTIVE/IDLE via logs** — grep the event log for recent state transitions.
   ```bash
   tail -20 <workdir>/logs/events.jsonl | grep '"type":"agent_state"'
   ```

4. **Verify heartbeat writes signal file detection** — check that `.interrupt`, `.sleep`, `.suspend` are consumed (not left behind) when not present.
   ```bash
   ls -la <workdir>/.interrupt <workdir>/.sleep <workdir>/.suspend 2>&1 | grep -c "No such file"
   # Expected: 3 (all three absent = signals were consumed or never set)
   ```

5. **Verify `.status.json` reflects state** — if the working directory has a `.status.json`, read it.
   ```bash
   cat <workdir>/.status.json 2>/dev/null | python3 -m json.tool
   ```

6. **Verify SUSPENDED state leaves no heartbeat** — if a suspended agent exists, confirm `.agent.heartbeat` is absent.
   ```bash
   ls <suspended_workdir>/.agent.heartbeat 2>&1 | grep "No such file"
   ```

## Pass Criteria

- `.agent.json` exists and is valid JSON with `address` field.
- `.agent.heartbeat` contains a numeric epoch float within the last 30 seconds (for a running agent).
- `logs/events.jsonl` contains `"type":"agent_state"` entries with `old` and `new` fields matching the five state names (`active`, `idle`, `stuck`, `asleep`, `suspended`).
- Signal files (`.interrupt`, `.sleep`, `.suspend`) do not persist when not actively being set — they are consumed by the heartbeat loop.
- A SUSPENDED agent's working directory has no `.agent.heartbeat` file.

## Output Template

```
## Agent State Machine Test Results

| Check | Result | Evidence |
|-------|--------|----------|
| .agent.json valid | PASS/FAIL | <snippet> |
| Heartbeat freshness | PASS/FAIL | <age>s stale |
| State transitions in log | PASS/FAIL | <last transition> |
| Signal files consumed | PASS/FAIL | <count>/3 absent |
| SUSPENDED no heartbeat | PASS/SKIP/FAIL | <evidence> |
```
