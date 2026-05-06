# core/mcp

MCP capability — per-agent registry of MCP (Model Context Protocol) servers.
Pure presentation: reads the registry from disk, validates records, and renders
it as XML into the system prompt. No tool writes; all registry mutations happen
via file operations from the agent (`write`, `edit`).

Also includes the **LICC v1 (LingTai Inbox Callback Contract)** — a
filesystem-based inbox that lets out-of-process MCP servers push events into
the agent's inbox.

## Components

- `mcp/__init__.py` — MCP registry management and tool surface. `get_description` (`mcp/__init__.py:374-375`), `get_schema` (`mcp/__init__.py:378-379`), `setup` (`mcp/__init__.py:382-406`). Key functions: `validate_record` (`mcp/__init__.py:82-125`), `validate_registry_line` (`mcp/__init__.py:128-138`), `read_registry` (`mcp/__init__.py:149-186`), `decompress_addons` (`mcp/__init__.py:201-257`), `_build_registry_xml` (`mcp/__init__.py:274-299`), `_reconcile` (`mcp/__init__.py:306-342`).
- `mcp/inbox.py` — LICC v1 filesystem inbox poller. `validate_event` (`mcp/inbox.py:65-96`); `_format_notification_summary` (`mcp/inbox.py:103-123`) — **signal-only** notification body, no sender/subject/body inlined (issue #37 — content arrives via the explicit `<mcp>(action="read")` tool call, never twice); `_consume_event` (`mcp/inbox.py:126-142`) — per-event log + wake intent; `_dispatch_summary` (`mcp/inbox.py:145-154`) — one coalesced [system] notification per MCP per sweep; `_scan_once` (`mcp/inbox.py:188-269`) coalesces per MCP; `MCPInboxPoller` class (`mcp/inbox.py:278-321`).
- `mcp/manual/` — skill documentation (`SKILL.md`) plus reference docs (`curated-addons.md`, `third-party-and-legacy.md`, `troubleshooting.md`) and scripts (`find_readme.py`).

## Public API

The `mcp` tool exposes one action:

| Action | Description |
|--------|-------------|
| `show` | Return the mcp-manual skill body plus a runtime health snapshot (registry contents, problems, registry path) |

### LICC v1 Inbox Protocol

MCP servers push events via filesystem writes:
```
<agent_working_dir>/.mcp_inbox/<mcp_name>/<event_id>.json
```

Schema (v1):
```json
{
  "licc_version": 1,
  "from": "human-readable sender (required)",
  "subject": "one-line summary (required, max 200 chars)",
  "body": "full message body (required)",
  "metadata": {},
  "wake": true,
  "received_at": "ISO 8601"
}
```

Atomic write: write to `<event_id>.json.tmp`, fsync, then rename.

## Internal Module Layout

```
mcp/__init__.py
  ├── Catalog
  │   ├── _load_catalog()           — reads kernel-shipped mcp_catalog.json, cached
  │   └── decompress_addons()       — boot-time: append catalog entries for addons not in registry
  │
  ├── Validation
  │   ├── validate_record()         — validates a single MCP registry record
  │   └── validate_registry_line()  — validates a single JSONL line
  │
  ├── Registry I/O
  │   ├── read_registry()           — reads mcp_registry.jsonl, returns (valid, problems)
  │   └── _append_record()          — appends a validated record as a JSONL line
  │
  ├── XML builder
  │   ├── _escape_xml()             — XML entity escaping
  │   └── _build_registry_xml()     — renders registry records as <registered_mcp> XML
  │
  ├── Reconciliation
  │   └── _reconcile()              — reads registry, renders into prompt, returns health snapshot
  │
  └── Tool surface
      ├── get_description/schema()  — module-level
      └── setup()                   — registers mcp tool, runs initial _reconcile

mcp/inbox.py
  ├── Validation
  │   └── validate_event()              — validates a parsed LICC event
  │
  ├── Dispatch (signal-only since issue #37)
  │   ├── _format_notification_summary()— count-only [system] body; no sender/subject/body
  │   ├── _consume_event()              — per-event log + wake intent collector
  │   └── _dispatch_summary()           — one coalesced inbox post per MCP per sweep
  │
  ├── Dead-letter
  │   └── _dead_letter()                — moves invalid file to .dead/ with .error.json sidecar
  │
  ├── Scanner
  │   └── _scan_once()                  — sweep .mcp_inbox/<mcp_name>/*.json,
  │                                       consume each event, post one summary per MCP
  │
  └── Poller
      └── MCPInboxPoller                — daemon thread that polls at POLL_INTERVAL (0.5s)
          ├── start()                   — creates root dir, starts poll thread
          └── stop()                    — signals stop, joins thread
```

## Key Invariants

- **Registry is append-only JSONL:** One record per line. Duplicates by name are flagged as problems during read. Mutations (register, deregister, update) happen via agent-side file operations.
- **Name convention:** Lowercase, dash-separated, bounded length (`^[a-z][a-z0-9_-]{0,30}$`).
- **Transport validation:** `stdio` requires `command` + `args`; `http` requires `url`.
- **Addons decompression is idempotent:** Running `decompress_addons()` multiple times produces the same registry. Existing records are never modified.
- **`{python}` substitution:** Catalog entries support `{python}` placeholder in command args, resolved to `sys.executable` at decompression time.
- **LICC atomicity:** MCP servers must write `.json.tmp` then rename to `.json`. Half-written `.tmp` files are ignored by the scanner.
- **LICC dead-letter:** Invalid events (parse errors, missing fields, unknown version, dispatch failures) are moved to `.dead/` with a `.error.json` sidecar. Dead-letters are never auto-deleted.
- **LICC bounded work:** `MAX_EVENTS_PER_CYCLE = 100` per MCP per sweep prevents pathological backlog from blocking the poller.
- **LICC signal-only notification (issue #37):** The kernel-synthesized `[system]` notification carries only the MCP name and event count — never the event's `from`, `subject`, or `body`. Messaging MCPs (telegram, feishu, wechat, imap, …) already deliver payload via the explicit `<mcp>(action="check"/"read")` tool result; inlining the body here caused the agent to process every message twice. The notification is a wake-up signal that says "N new events from MCP X — call its read action to fetch." Multiple events from the same MCP in one sweep are coalesced into a single summary; `wake` is the OR of all per-event `wake` flags.
- **Pure presentation:** The capability never writes to the registry file. It only reads and renders.

## Dependencies

- `yaml` (PyYAML) — used by the library capability's frontmatter parser (imported transitively; not directly used here)
- `lingtai.i18n` — `t()` for localized strings (imported but the description is hardcoded English)
- `lingtai_kernel.message` — `_make_message`, `MSG_REQUEST` for inbox dispatch (in `inbox.py`)
- `lingtai_kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)
- `lingtai.mcp_catalog.json` — kernel-shipped MCP catalog file (read at runtime)

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `daemon/`, `avatar/`, `library/`, `codex/`, `bash/`.
- **Manual:** `mcp/manual/SKILL.md` — registration contract and usage guide.
- **Kernel hooks:** `setup()` is called during capability initialization; `decompress_addons()` is called by the Agent initializer before `setup`. `MCPInboxPoller.start()/stop()` are called by the agent lifecycle.
