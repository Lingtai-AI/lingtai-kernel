---
related_files:
- src/lingtai/intrinsic_skills/lingtai-kernel-anatomy/SKILL.md
- src/lingtai/services/mcp.py
- src/lingtai/agent.py
- src/lingtai/tools/daemon/__init__.py
- src/lingtai/services/ANATOMY.md
- src/lingtai/mcp_servers/ANATOMY.md
- src/lingtai/tools/mcp/manual/SKILL.md
- pyproject.toml
- tests/test_mcp_sdk_v2_contract.py
- tests/test_mcp_capability.py
- tests/test_mcp_v2_adapter_metadata.py
maintenance: |
  Tracks the officially supported MCP SDK range, the negotiated protocol
  version, and the SDK-versus-LingTai ownership split. Update it whenever the
  `mcp` dependency range changes, the client/server handler shape changes, or
  the tool-metadata sidecar contract changes.
---

# MCP protocol compatibility

What LingTai assumes about the Model Context Protocol, and where those
assumptions live in the kernel. This is **not** a copy of the standard — the
protocol is specified externally and the wire is owned by the official SDK. It
records only the compatibility surface LingTai itself owns, so a reader can check
the code against a fixed anchor instead of guessing. It is the maintained target
for the `reference/mcp-protocol.md` route used by the MCP manual and
`src/lingtai/init.jsonc`, and ships inside the wheel.

## Supported SDK and protocol

| Item | Value | Where |
|---|---|---|
| SDK dependency | `mcp>=2,<3` | `pyproject.toml` |
| HTTP client | `httpx2>=2.5.0`, declared directly because the kernel imports it | `pyproject.toml` |
| Protocol version | `2026-07-28`, with automatic fallback to the pre-2026 handshake | negotiated by the SDK, never pinned in LingTai code |
| Types package | `mcp-types`, exact-pinned by the `mcp` wheel | do not pin separately |

LingTai declares **no** protocol-version literal. The SDK `Client` decides the
version at connect, and the result is read back off the connected client rather
than assumed. Do not add a version constant; add a capability check if behavior
must differ.

## Ownership boundary

Owned by the official SDK:

* JSON-RPC framing, request IDs, notifications, cancellation;
* the `initialize` handshake, `server/discover`, and version negotiation;
* transport encoding for stdio and Streamable HTTP;
* typed protocol model validation.

Owned by LingTai:

* process/session lifecycle and the stale-resource replay policy
  (`src/lingtai/services/mcp.py`);
* the kernel-facing tool record, the metadata sidecar, and the legacy result
  projection (same file, plus the adapters below);
* schema-aware argument preparation at Agent stdio/HTTP and task-daemon MCP
  provider boundaries (`src/lingtai/services/mcp.py`, `src/lingtai/agent.py`,
  `src/lingtai/tools/daemon/__init__.py`);
* configuration, registry gating, redaction, and task-scoped registration
  (`src/lingtai/services/mcp_registry.py`, `src/lingtai/tools/daemon/`);
* the curated server implementations under `src/lingtai/mcp_servers/`.

## Client behavior

* **Negotiation.** Both clients construct `mcp.Client` in its default
  `mode="auto"`. The negotiated `protocol_version`, `server_info`,
  `server_capabilities`, and `instructions` are exposed read-only on each
  client.
* **Attribute spelling.** SDK v2 model attributes are snake_case
  (`input_schema`, `output_schema`, `is_error`, `structured_content`,
  `next_cursor`, `meta`). The wire names are unchanged; they reappear only when
  serializing with `by_alias=True`.
* **Timeouts** are float seconds, not `timedelta`.
* **Pagination.** Every `list_*` result carries `next_cursor`; the tool catalog
  is paged to exhaustion before it is returned.
* **Results.** `preserve_tool_result` keeps the complete typed result. The
  single legacy value handed to kernel tool handlers is an explicit
  compatibility projection, documented as such at the call site.
* **HTTP client ownership.** `HTTPMCPClient` constructs the
  `httpx2.AsyncClient` the v2 transport requires, enters it before the `Client`
  and exits it after — the SDK factory does not manage a supplied client's
  lifecycle.
* **Retry.** stdio supports an opt-in `retry_policy="safe"` single replay. HTTP
  deliberately supports none; see `src/lingtai/services/ANATOMY.md`.
* **Host-private arguments.** Provider-bound arguments are copied before
  adaptation. The kernel-owned `_reasoning` field is removed unless the
  advertised server schema explicitly declares it; the exact closed LTP-v2
  family schema instead restores its required public `reasoning` field.
  Ordinary unknown business fields pass through unchanged so server-side
  closed-schema validation still detects caller mistakes and contract drift.

## Tool metadata that `FunctionSchema` cannot carry

`_tool_record()` returns the complete v2 record. `FunctionSchema` has fields
only for name, description, and parameters, so the advertised `title`,
`output_schema`, `annotations`, `icons`, `execution`, and `meta` are retained in
a **metadata sidecar** rather than being forced into the provider wire schema:

* Agent (stdio and HTTP): `Agent.mcp_tool_metadata(name)`;
* task daemon: `task_mcp_tool_metadata(name)` on the owner that connected the
  registrations.

Both read seams return a copy, so a caller cannot mutate stored metadata
through the returned mapping. Both sidecars are cleared with the MCP clients
they describe, so a torn-down or refreshed surface never exposes stale
metadata. Task-daemon's top-level `additionalProperties` removal remains an
existing `FunctionSchema` normalization and applies only to its schema copy;
Agent mounts retain the advertised root unchanged.

## Server behavior

All eight bundled servers are low-level `Server` instances over stdio:

`cloud_mail`, `imap`, `telegram`, `feishu`, `wechat`, `whatsapp`,
`daemon_common`, `daemon_email`.

* Handlers are **constructor parameters** (`on_list_tools`, `on_call_tool`,
  `on_list_resources`, `on_read_resource`), not decorators, and each is
  `async (ctx, params) -> <full typed result>`.
* Advertised capabilities follow the registered handlers. A server with no
  resource handler does not claim `resources`, and none of them advertises
  prompts, completion, subscriptions, or multi-round-trip. That subset is the
  product surface, deliberately, not an unfinished migration.
* **The SDK validates the typed MCP request envelope; it does not validate the
  advertised per-tool JSON Schema.** The v2 runner surface-checks the method and
  Pydantic-validates the registered request params before your handler runs, so
  `params.name` is a `str` and `params.arguments` is a `dict | None`. It never
  applies the `input_schema`/`output_schema` a tool advertises. Handlers that
  need per-tool argument validation do it themselves. Telegram has no hidden
  `task_card` route; the intrinsic capability owns that tool-specific contract.
  Telegram's public handler still validates its `chat_id` shape in the handler
  and manager rather than relying on the published schema.
* A provider/domain failure stays a model-readable
  `CallToolResult(is_error=True)`. A **lookup** failure is different: the
  2026-07-28 schema classifies an unknown tool name or unknown resource URI as
  `InvalidParamsError`, so every curated handler raises an official `MCPError`
  with `code=INVALID_PARAMS` (`-32602`), a `Unknown tool: ...` /
  `Unknown resource: ...` message, and `data={"requested": name}` or
  `data={"uri": uri}`. That is a caller-fixable parameter error, not a server
  fault — only an explicit `MCPError` keeps its code through the v2 dispatcher,
  since a generic exception would be flattened to `-32603`. The two raisers
  live in `mcp_servers/_results.py` so the bundled lookup branches cannot drift.

## Config and env-injection boundary (stdio only)

Launch configuration is LingTai's, not the protocol's. Two environment
variables are merged into the child environment when the kernel **spawns** a
stdio MCP server:

| Variable | Meaning |
|---|---|
| `LINGTAI_AGENT_DIR` | The owning agent's working directory, so the server can locate the LICC inbox. |
| `LINGTAI_MCP_NAME` | The server's own registry name, added per spawn so a server can identify itself. |

Injection sites: `src/lingtai/agent.py` (initial load and the failed-retry
respawn) and `src/lingtai/tools/daemon/__init__.py` for task-scoped
registrations. Caller-supplied `env` wins over both.

The HTTP path passes headers, not environment, so an HTTP-configured server
receives neither variable and cannot participate in LICC on this route — a stated
boundary, not an oversight.

Registry validation is owned by `src/lingtai/services/mcp_registry.py`
(`validate_record`): launch-configuration schema, not MCP wire schema, so a
record can be registry-valid and still fail at connect.

## Outside this contract

The LICC filesystem notification channel
(`src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md` — event envelope, two-lane
projection, `LICC_VERSION = 1`), the MCP registry and identity documents, the
`mcp` presentation tool, and the private Telegram Task Card route are all
LingTai-specific. They carry their own versioned contracts and have no MCP wire
semantics; read them for those questions, and do not file them as protocol
defects.

## Related

* `src/lingtai/services/ANATOMY.md` — client internals and state.
* `src/lingtai/mcp_servers/ANATOMY.md` — curated server layout.
* `tests/test_mcp_sdk_v2_contract.py` — executable form of this reference.
* `tests/test_mcp_capability.py` / `tests/test_mcp_v2_adapter_metadata.py` —
  Agent and task-daemon argument-boundary regressions.
