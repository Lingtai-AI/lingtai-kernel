# MCP Capability — Anatomy Leaves

Drafted 2026-04-30 by leaves-mcp.

## Completed

| Leaf | Scope | README | test.md |
|------|-------|--------|---------|
| `inbox-listener/` | LICC filesystem poller — `.mcp_inbox/` polling, event schema, validation, dead-letter, dispatch | ✓ | ✓ |
| `capability-discovery/` | Three-layer model — catalog → registry → activation, decompression, health check | ✓ | ✓ |
| `licc-roundtrip/` | End-to-end MCP → agent event delivery — atomic write, poll, dispatch, wake | ✓ | ✓ |

## Missing — needs a leaf

**`tool-call-bridge/`** — Outbound MCP tool calls: how the agent calls tools on a connected MCP server.

The three completed leaves cover the *inbound* side (MCP → agent) and the *registry/discovery* side. The *outbound* side — how `agent.connect_mcp()` spawns a `MCPClient`, calls `tools/list`, registers each tool into the agent's tool surface, and how `call_tool()` bridges sync→async to invoke MCP tools — is not yet covered.

Key source references:
- `services/mcp.py` — `MCPClient` (stdio, L21–254), `HTTPMCPClient` (HTTP/SSE, L257–431)
- `agent.py` — `connect_mcp()` (L446–496), `connect_mcp_http()` (L498–542), `_mcp_clients` lifecycle (L555–559, L740–745)
- Anatomy reference: `mcp-protocol.md` §3 (Subprocess Loader), §7 (Reference Implementations)

Should follow the same leaf contract: README.md (What / Contract / Source / Related, ≤100 lines) + test.md (Setup / Steps ≤10 / Pass criteria / Output template).
