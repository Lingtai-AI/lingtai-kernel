# src/lingtai/services/

Root services package — pluggable backends for intrinsic tools and MCP clients.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 1 | Docstring-only package marker |
| `file_io.py` | 134 | `FileIOService` (ABC) + `LocalFileIOService` — backs read/edit/write/glob/grep |
| `mail.py` | 4 | Re-exports `MailService`, `FilesystemMailService` from `lingtai_kernel.services.mail` |
| `mcp.py` | 431 | `MCPClient` (stdio) + `HTTPMCPClient` (streamable HTTP) — async-to-sync MCP bridges |

**Sub-packages (not covered here):** `vision/` (7 provider files), `websearch/` (6 provider files).

## Connections

- **→ `lingtai_kernel.logging.get_logger`** (mcp.py:16) — structured logging.
- **→ `lingtai_kernel.services.mail`** (mail.py:2) — pure re-export of kernel mail types.
- **→ `mcp.client.stdio`**, **`mcp.client.streamable_http`**, **`mcp.client.session`** (mcp.py:224, 406-407) — third-party MCP SDK. Imported lazily inside async connect methods.
- **← `lingtai.capabilities.vision`** — uses `services.vision.VisionService`.
- **← `lingtai.capabilities.web_search`** — uses `services.websearch.SearchService`.
- **← `lingtai.core.*`** — read/write/edit/glob/grep use `FileIOService`.

## Composition

`file_io.py` is a pure abstraction layer (no external deps beyond stdlib). `mail.py` is a passthrough re-export. `mcp.py` is the heavy module — two parallel client classes sharing the same pattern.

## State

- **`MCPClient` / `HTTPMCPClient`**: each instance manages a background daemon thread (L68, 292), an asyncio event loop (`_loop`), a `ClientSession` (`_session`), and a 50-entry activity log (`_activity_log`, L54, 286). Thread-safe via `threading.Lock` and `threading.Event`.
- **`LocalFileIOService`**: stateless beyond optional `_root` path (L64).
- **`FileIOService` ABC**: pure interface, no state.

## Notes

- `MCPClient` uses `stdio_client` transport (subprocess); `HTTPMCPClient` uses `streamablehttp_client` (remote HTTP/SSE). Both expose identical `call_tool()` / `list_tools()` / `close()` API.
- Lazy start: both clients auto-connect on first `call_tool()` (L119, 321).
- `mcp.py` has significant code duplication between the two classes — same `call_tool()`, `list_tools()`, `_run_loop()`, `_async_cleanup()` pattern.
- `mail.py` is a thin shim — the real implementation lives in `lingtai_kernel.services.mail`.
