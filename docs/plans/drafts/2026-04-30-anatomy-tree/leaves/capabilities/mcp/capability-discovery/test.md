---
timeout: 180
---

# Test: MCP Capability Discovery

## Setup

1. Identify the agent's working directory (`<workdir>`).
2. Ensure the agent is running (ACTIVE or IDLE state).
3. Have access to `bash` tool for filesystem inspection.

## Steps

1. **Verify registry file exists.**
   ```bash
   ls -la <workdir>/mcp_registry.jsonl
   ```

2. **Inspect registry contents.**
   ```bash
   cat <workdir>/mcp_registry.jsonl
   ```
   Each line should be valid JSON with required fields (`name`, `summary`, `transport`, `source`).

3. **Call `mcp(action="show")` via agent tool.**
   Invoke the `mcp` tool with `action="show"` and capture the returned JSON.

4. **Verify registry rendered in system prompt.**
   Check that the agent's system prompt contains a `<registered_mcp>` XML block with entries matching the registry file.

5. **Verify `problems` list.**
   From the `mcp(action="show")` response, check the `problems` array. It should be empty for a healthy registry.

6. **Append a third-party record manually.**
   ```bash
   echo '{"name":"test-mcp","summary":"Test MCP server.","transport":"stdio","command":"echo","args":["hello"],"source":"user"}' >> <workdir>/mcp_registry.jsonl
   ```

7. **Run `system(action="refresh")`** to reload the capability.

8. **Verify new record appears in `mcp(action="show")` response.**
   The `registered` array should now include `{"name":"test-mcp","summary":"Test MCP server."}`.

9. **Add activation entry and verify gating.**
   Add `test-mcp` to `init.json.mcp`, then refresh. Verify the loader logs show the MCP was spawned (or attempted). Remove the entry afterward.

10. **Test decompression from addons.**
    If `init.json` has `addons: ["imap"]`, verify that `mcp_registry.jsonl` contains an `imap` entry (auto-decompressed from the kernel catalog). If not, add `"imap"` to `addons` and refresh, then check the registry.

## Pass criteria (filesystem-observable ONLY)

- [ ] `<workdir>/mcp_registry.jsonl` exists and contains valid JSONL (each line parses, has required fields)
- [ ] `mcp(action="show")` returns `status: "ok"` with `registered_count >= 0`
- [ ] `registered` array entries match the JSONL file contents (same names)
- [ ] `problems` array is empty for a clean registry
- [ ] Appending a valid record + refresh makes it appear in `registered`
- [ ] `<registered_mcp>` XML block exists in the rendered system prompt after reconciliation
- [ ] Unregistered MCP name in `init.json.mcp` produces a warning log (not a crash)

## Output template

```
## MCP Capability Discovery Test Results

| Step | Expected | Actual | Pass |
|------|----------|--------|------|
| 1. Registry file exists | file present | [paste] | ✓/✗ |
| 2. Registry contents valid | valid JSONL | [paste lines] | ✓/✗ |
| 3. mcp(show) returns ok | status=ok | [paste] | ✓/✗ |
| 4. System prompt has <registered_mcp> | XML block present | [paste] | ✓/✗ |
| 5. Problems empty | [] | [paste] | ✓/✗ |
| 6. Third-party record appended | line added | [paste] | ✓/✗ |
| 7. Refresh completes | no error | [paste] | ✓/✗ |
| 8. New record in registered | test-mcp appears | [paste] | ✓/✗ |
| 9. Activation gating | MCP spawned or warning logged | [paste] | ✓/✗ |
| 10. Addons decompression | imap in registry | [paste] | ✓/✗ |

**Verdict:** PASS / FAIL
**Notes:** [any observations]
```
