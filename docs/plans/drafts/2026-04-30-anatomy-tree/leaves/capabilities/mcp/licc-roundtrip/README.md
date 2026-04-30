# LICC Roundtrip — End-to-End MCP → Agent Event Delivery

## What

This leaf documents the complete roundtrip: an out-of-process MCP server writes a LICC event to the filesystem, the kernel's `MCPInboxPoller` picks it up, validates it, dispatches it as a `[system]` notification into the agent's inbox, wakes the agent if sleeping, logs it to `events.jsonl`, and deletes the event file. This is the inbound counterpart of MCP tool calls — MCP-initiated rather than agent-initiated.

## Contract

**Full roundtrip sequence.**

1. **MCP subprocess writes event.** Using the `push_inbox_event()` helper (or any language implementing the contract):
   - Read `LINGTAI_AGENT_DIR` and `LINGTAI_MCP_NAME` from environment.
   - Construct target dir: `<LINGTAI_AGENT_DIR>/.mcp_inbox/<LINGTAI_MCP_NAME>/`.
   - Create JSON event matching LICC v1 schema.
   - Write to `<event_id>.json.tmp`, fsync, `os.rename()` to `.json`.

2. **Kernel poller detects file.** `_scan_once()` iterates `.mcp_inbox/*/*.json` (skipping `.dead/` and `.tmp`), reads and parses the JSON.

3. **Validation.** `validate_event()` checks: is dict, `licc_version == 1`, non-empty `from`, non-empty `subject` ≤ 200 chars, string `body`, dict `metadata` when present, boolean `wake` when present.

4. **Dispatch.** `_dispatch_event()`:
   - Formats notification: `[system] New event from MCP '<mcp_name>'.\n  From: <from>\n  Subject: <subject>\n  <body[:200]>...`
   - Creates a `MSG_REQUEST` message via `_make_message()`.
   - Puts it into `agent.inbox`.
   - If `wake=true`, calls `agent._wake_nap("mcp_event")`.
   - Logs `mcp_inbox_event` to `events.jsonl`.

5. **Cleanup.** On successful dispatch, deletes the event file. On failure, dead-letters it.

**Wake behavior.** When `wake=true` (the default), the agent is woken from ASLEEP state. When `wake=false`, the event is delivered to the inbox but the agent continues sleeping — useful for high-volume, non-urgent events.

**Backpressure.** Max 100 events per cycle per MCP. Events beyond that queue on disk until the next 0.5s tick.

**Dead-letter on failure.** If validation fails or dispatch throws, the event moves to `.dead/` with a sibling `.error.json` describing the failure. Both files persist until manually cleaned.

## Source (real file:line)

| Component | File | Lines |
|---|---|---|
| Reference client (`push_inbox_event()`) | Anatomy ref §5 | (vendored into each MCP repo, e.g. `lingtai-imap/src/lingtai_imap/licc.py`) |
| Atomic write protocol | `core/mcp/inbox.py` | 1-33 (docstring) |
| `_scan_once()` — poller sweep | `core/mcp/inbox.py` | 170-234 |
| `validate_event()` | `core/mcp/inbox.py` | 65-96 |
| `_dispatch_event()` — inbox put + wake | `core/mcp/inbox.py` | 119-136 |
| `_format_notification()` | `core/mcp/inbox.py` | 103-116 |
| `_dead_letter()` | `core/mcp/inbox.py` | 143-159 |
| `MCPInboxPoller` — daemon thread | `core/mcp/inbox.py` | 241-284 |
| LICC env vars injected at spawn | `agent.py` | 308-310, 328-330 |
| Poller started in `Agent.start()` | `agent.py` | 440-444 |
| Poller stopped before MCP clients | `agent.py` | 545-552 |

## Related

- **inbox-listener/** — the poller in isolation (schema, dead-letter, poll cadence)
- **capability-discovery/** — how the MCP subprocess gets registered and spawned (prerequisite for writing events)
- Anatomy reference: `intrinsic_skills/lingtai-kernel-anatomy/reference/mcp-protocol.md` §4-§5 (LICC spec + reference client)
