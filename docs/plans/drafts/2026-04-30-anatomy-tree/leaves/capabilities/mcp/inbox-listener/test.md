---
timeout: 180
---

# Test: MCP Inbox Listener (LICC)

## Setup

1. Identify the agent's working directory (`<workdir>`).
2. Ensure the agent is running (ACTIVE or IDLE state).
3. Have the agent's address ready for mail-based wake detection.

## Steps

1. **Verify poller is running.**
   ```bash
   ls <workdir>/.mcp_inbox/
   ```
   The directory should exist (created by `MCPInboxPoller.start()`).

2. **Write a valid LICC event atomically.**
   ```bash
   cat > <workdir>/.mcp_inbox/test-mcp/evt-001.json.tmp << 'EOF'
   {"licc_version":1,"from":"test-sender","subject":"licc test","body":"hello from test","metadata":{"key":"val"},"wake":true}
   EOF
   fsync (or use python to write + fsync + rename)
   mv <workdir>/.mcp_inbox/test-mcp/evt-001.json.tmp <workdir>/.mcp_inbox/test-mcp/evt-001.json
   ```

3. **Wait for poll cycle (≤1 second).**
   ```bash
   sleep 1
   ```

4. **Verify event was consumed.**
   ```bash
   ls <workdir>/.mcp_inbox/test-mcp/evt-001.json
   ```
   File should NOT exist (deleted after dispatch).

5. **Verify dispatch logged.**
   ```bash
   grep "mcp_inbox_event" <workdir>/logs/events.jsonl | tail -1
   ```

6. **Write a malformed event (missing `from`).**
   ```bash
   cat > <workdir>/.mcp_inbox/test-mcp/bad-001.json << 'EOF'
   {"licc_version":1,"subject":"bad","body":"no sender"}
   EOF
   ```

7. **Wait for poll cycle (≤1 second).**
   ```bash
   sleep 1
   ```

8. **Verify dead-letter.**
   ```bash
   ls <workdir>/.mcp_inbox/test-mcp/.dead/bad-001.json
   ls <workdir>/.mcp_inbox/test-mcp/.dead/bad-001.error.json
   cat <workdir>/.mcp_inbox/test-mcp/.dead/bad-001.error.json
   ```
   Both files should exist. The `.error.json` should contain `"missing or empty field: from"`.

9. **Write a half-written event (leave `.tmp` extension).**
   ```bash
   cat > <workdir>/.mcp_inbox/test-mcp/pending-001.json.tmp << 'EOF'
   {"licc_version":1,"from":"test","subject":"pending","body":"incomplete"}
   EOF
   ```

10. **Wait for poll cycle, verify `.tmp` is ignored.**
    ```bash
    sleep 1
    ls <workdir>/.mcp_inbox/test-mcp/pending-001.json.tmp
    ```
    File should still exist — poller skips `.tmp` files.

## Pass criteria (filesystem-observable ONLY)

- [ ] `<workdir>/.mcp_inbox/` directory exists after agent start
- [ ] Valid `.json` event file is deleted within ≤1 second of write
- [ ] `events.jsonl` contains a `mcp_inbox_event` entry matching the test event
- [ ] Invalid event is moved to `.dead/` subdirectory (original deleted from parent)
- [ ] `.dead/<id>.error.json` exists and contains the validation error string
- [ ] `.tmp` files are not consumed (remain in place)

## Output template

```
## MCP Inbox Listener Test Results

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1. .mcp_inbox/ exists | dir exists | [paste] | ✓/✗ |
| 2. Valid event written | file created | [paste] | ✓/✗ |
| 3. Poll wait | ≤1s | [elapsed] | ✓/✗ |
| 4. Event consumed | file gone | [paste] | ✓/✗ |
| 5. Dispatch logged | mcp_inbox_event in events.jsonl | [paste] | ✓/✗ |
| 6. Bad event written | file created | [paste] | ✓/✗ |
| 7. Poll wait | ≤1s | [elapsed] | ✓/✗ |
| 8. Dead-lettered | .dead/bad-001.json + .error.json | [paste] | ✓/✗ |
| 9. .tmp event written | file created | [paste] | ✓/✗ |
| 10. .tmp ignored | file remains | [paste] | ✓/✗ |

**Verdict:** PASS / FAIL
**Notes:** [any observations]
```
