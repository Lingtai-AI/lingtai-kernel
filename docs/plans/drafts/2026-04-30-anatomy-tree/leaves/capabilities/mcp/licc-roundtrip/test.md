---
timeout: 180
---

# Test: LICC Roundtrip (MCP → Agent Event Delivery)

## Setup

1. Identify the agent's working directory (`<workdir>`).
2. Ensure the agent is running and in IDLE or ASLEEP state.
3. Note the agent's address for mail delivery.
4. Ensure no other MCPs are actively writing to `.mcp_inbox/` during the test window.

## Steps

1. **Prepare the inbox directory.**
   ```bash
   mkdir -p <workdir>/.mcp_inbox/roundtrip-test/
   ```

2. **Write a valid LICC event atomically (simulate MCP).**
   ```python
   import json, os, time, uuid
   from pathlib import Path

   agent_dir = "<workdir>"
   mcp_name = "roundtrip-test"
   event_id = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
   event = {
       "licc_version": 1,
       "from": "roundtrip-test-mcp",
       "subject": "roundtrip verification",
       "body": "This event tests the full LICC roundtrip from MCP to agent inbox.",
       "metadata": {"test": True, "event_id": event_id},
       "wake": True,
   }

   target_dir = Path(agent_dir) / ".mcp_inbox" / mcp_name
   target_dir.mkdir(parents=True, exist_ok=True)
   tmp = target_dir / f"{event_id}.json.tmp"
   final = target_dir / f"{event_id}.json"

   with tmp.open("w", encoding="utf-8") as f:
       f.write(json.dumps(event, ensure_ascii=False))
       f.flush()
       os.fsync(f.fileno())
   tmp.rename(final)
   ```

3. **Record timestamp before write.**
   ```bash
   date -u +%Y-%m-%dT%H:%M:%S.%3NZ
   ```

4. **Wait for poll cycle (≤1 second).**
   ```bash
   sleep 1
   ```

5. **Verify event file consumed.**
   ```bash
   ls <workdir>/.mcp_inbox/roundtrip-test/*.json 2>/dev/null
   ```
   Should return no files (all consumed).

6. **Verify dispatch in events.jsonl.**
   ```bash
   grep "mcp_inbox_event" <workdir>/logs/events.jsonl | tail -1
   ```
   Should contain `mcp=roundtrip-test`, `sender=roundtrip-test-mcp`, `subject=roundtrip verification`.

7. **Verify notification landed in agent inbox.**
   Send a short mail to the agent asking "What was the last system notification you received?" The agent should reference the LICC event.

8. **Test wake behavior.**
   If the agent was ASLEEP, verify it became ACTIVE after the event (check `system(show)` or send a mail and observe response latency).

9. **Test `wake=false` roundtrip.**
   Write a second event with `"wake": false`:
   ```python
   event["wake"] = False
   event["subject"] = "no-wake test"
   # ... same atomic write procedure ...
   ```
   Wait 1 second. Verify the event was consumed and logged, but the agent was NOT woken if it was ASLEEP.

10. **Test dead-letter roundtrip.**
    Write an invalid event (missing `from`):
    ```bash
    echo '{"licc_version":1,"subject":"bad","body":"no sender"}' > <workdir>/.mcp_inbox/roundtrip-test/bad-rt.json
    ```
    Wait 1 second. Verify:
    ```bash
    ls <workdir>/.mcp_inbox/roundtrip-test/.dead/bad-rt.json
    ls <workdir>/.mcp_inbox/roundtrip-test/.dead/bad-rt.error.json
    cat <workdir>/.mcp_inbox/roundtrip-test/.dead/bad-rt.error.json
    ```

## Pass criteria (filesystem-observable ONLY)

- [ ] Valid event file is deleted from `.mcp_inbox/` within ≤1 second
- [ ] `events.jsonl` contains `mcp_inbox_event` entry with correct `mcp`, `sender`, `subject` fields
- [ ] Agent's inbox received a `[system]` notification matching the event (verifiable via agent response)
- [ ] `wake=true` event wakes an ASLEEP agent (verifiable via `system(show)` state change or response latency)
- [ ] `wake=false` event is delivered but does NOT wake the agent
- [ ] Invalid event is dead-lettered to `.dead/` with `.error.json` sibling containing the validation error
- [ ] No `.tmp` files left behind in the inbox directory after the test
- [ ] No crash or STUCK state in the agent after the roundtrip

## Output template

```
## LICC Roundtrip Test Results

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1. Inbox dir prepared | dir exists | [paste] | ✓/✗ |
| 2. Valid event written atomically | .json exists | [paste] | ✓/✗ |
| 3. Timestamp recorded | ISO string | [paste] | ✓/✗ |
| 4. Poll wait | ≤1s | [elapsed] | ✓/✗ |
| 5. Event consumed | no .json files left | [paste] | ✓/✗ |
| 6. events.jsonl entry | mcp_inbox_event present | [paste] | ✓/✗ |
| 7. Agent received notification | agent references event | [paste response] | ✓/✗ |
| 8. Wake behavior | agent ACTIVE after event | [paste] | ✓/✗ |
| 9. wake=false | event consumed, agent not woken | [paste] | ✓/✗ |
| 10. Dead-letter | .dead/ files present with error | [paste] | ✓/✗ |

**Verdict:** PASS / FAIL
**Notes:** [any observations, timing measurements, edge cases]
```
