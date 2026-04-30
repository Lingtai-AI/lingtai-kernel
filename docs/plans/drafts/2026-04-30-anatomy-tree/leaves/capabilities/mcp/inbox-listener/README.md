# MCP Inbox Listener — LICC Filesystem Protocol

## What

The inbox listener is the kernel-side poller that watches `.mcp_inbox/` for events pushed by out-of-process MCP servers. It implements the LingTai Inbox Callback Contract (LICC) v1 — a filesystem-based protocol that lets MCP subprocesses deliver events into the host agent's inbox without any IPC or network handshake. The MCP subprocess writes a JSON event file; the poller reads, validates, dispatches to the agent's inbox, and deletes the file.

## Contract

**Path convention.** MCPs write events to:
```
<agent_workdir>/.mcp_inbox/<mcp_name>/<event_id>.json
```
- `<agent_workdir>` comes from `LINGTAI_AGENT_DIR` env var (injected by the kernel at spawn time).
- `<mcp_name>` comes from `LINGTAI_MCP_NAME` env var (injected per-spawn).
- `<event_id>` is any unique string the MCP picks (reference client uses `<millis>-<uuid_hex8>`).

**Atomic write.** MCPs MUST write atomically: write to `<event_id>.json.tmp`, fsync, then `os.rename()` to `.json`. The poller ignores `.tmp` files.

**Event schema (v1).**
| Field | Type | Required | Notes |
|---|---|---|---|
| `licc_version` | int | optional (default 1) | Hard-rejected if not 1 |
| `from` | str | yes | Non-empty |
| `subject` | str | yes | Non-empty, max 200 chars |
| `body` | str | yes | May be empty string |
| `metadata` | dict | optional | Arbitrary, MCP-defined |
| `wake` | bool | optional (default true) | When false, delivers to inbox but does NOT wake a sleeping agent |
| `received_at` | ISO-8601 str | optional | Set by MCP client (reference `licc.py`); not validated or filled by kernel |

**Poll cadence.** 0.5 seconds — same as the `FilesystemMailService` poller. Max 100 events per cycle per MCP (`MAX_EVENTS_PER_CYCLE`).

**Validation and dead-letter.** Invalid events (parse error, missing required fields, unknown version) are moved to:
```
.mcp_inbox/<mcp_name>/.dead/<event_id>.json
.mcp_inbox/<mcp_name>/.dead/<event_id>.error.json
```
Dead-letters are never auto-deleted. The `.dead/` subdirectory is skipped on subsequent polls.

**Dispatch.** A valid event becomes a `[system]` notification in the agent's inbox with sender, subject, and a 200-char body preview. If `wake=true`, `agent._wake_nap("mcp_event")` is called. The event is logged to `events.jsonl` under `mcp_inbox_event`. After successful dispatch, the event file is deleted.

**Lifecycle.**
- **Start:** `MCPInboxPoller.start()` is called from `Agent.start()` — it pre-creates the `.mcp_inbox/` directory and spawns a daemon polling thread.
- **Stop:** `MCPInboxPoller.stop()` is called from `Agent.stop()` *before* closing MCP clients, so in-flight events finish dispatching before subprocess teardown.
- **Crash isolation:** An MCP crashing does not crash the host agent. The poller only reads the filesystem.

## Source (real file:line)

| Component | File | Lines |
|---|---|---|
| Constants (`INBOX_DIRNAME`, `POLL_INTERVAL`, `MAX_EVENTS_PER_CYCLE`) | `core/mcp/inbox.py` | 50-58 |
| `validate_event()` | `core/mcp/inbox.py` | 65-96 |
| `_format_notification()` | `core/mcp/inbox.py` | 103-116 |
| `_dispatch_event()` | `core/mcp/inbox.py` | 119-136 |
| `_dead_letter()` | `core/mcp/inbox.py` | 143-159 |
| `_scan_once()` — main poll loop | `core/mcp/inbox.py` | 170-234 |
| `MCPInboxPoller` class | `core/mcp/inbox.py` | 241-284 |
| Poller started in `Agent.start()` | `agent.py` | 440-444 |
| Poller stopped before MCP clients in `Agent.stop()` | `agent.py` | 545-552 |
| LICC env injection (`LINGTAI_AGENT_DIR`) | `agent.py` | 308-310 |
| `LINGTAI_MCP_NAME` per-spawn injection | `agent.py` | 328-330 |

## Related

- **capability-discovery/** — how MCP servers are registered and activated (the subprocess that writes to `.mcp_inbox/` must be registered first)
- **licc-roundtrip/** — end-to-end test of writing an event and verifying the agent receives it
- Anatomy reference: `intrinsic_skills/lingtai-kernel-anatomy/reference/mcp-protocol.md` §4 (LICC v1 spec) and §5 (reference client)
