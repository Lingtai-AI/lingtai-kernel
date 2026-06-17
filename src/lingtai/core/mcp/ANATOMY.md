# core/mcp

MCP capability — per-agent **minimal control plane** for MCP (Model Context
Protocol) servers. The `mcp` tool has exactly three actions — `list` / `add` /
`remove` — that edit *desired state* through a validated API: registry records
(`mcp_registry.jsonl`) and init.json activation entries. `add` writes the
registry record **and** the init.json activation in one step; `remove` strips
both (plus any `addons:` entry) in one step. It still reads the registry from
disk and renders it as XML into the system prompt. **Runtime loading is
unchanged** — desired-state edits do not touch the live tool surface; they take
effect only when the agent refreshes via `system(action="refresh")` (which
writes the `.refresh` signal the kernel heartbeat consumes at a turn boundary,
running the existing unseal→rebuild→reseal cycle). **Refresh is owned by the
`system` tool, not `mcp`.** Anything beyond list/add/remove — fine-grained
config edits, troubleshooting, unusual registrations — is hand-edited via
`write`/`edit`/`bash` then `system(action="refresh")`.

Also includes the **LICC v1 (LingTai Inbox Callback Contract)** — a
filesystem-based inbox that lets out-of-process MCP servers push events into
the agent's inbox.

## Components

- `mcp/__init__.py` — MCP registry/activation control plane and tool surface. `get_description`/`get_schema`/`setup` at the bottom; `setup`'s `handle_mcp` routes the three actions to `_handle_list`/`_handle_add`/`_handle_remove`. Key functions: `validate_record`, `validate_registry_line`, `read_registry`, `_append_record`, `_remove_record` (rewrite JSONL minus one name, best-effort comment/blank preservation), `_substitute_placeholders` (`{python}`→sys.executable, shared with `decompress_addons`), `decompress_addons`, `_build_registry_xml`, `_reconcile` (renders the registry XML into the system prompt on setup — no return value). Control-plane helpers: `_redact_config` (env/headers/token-like fields → `"<redacted>"`), `_redact_problems` (strips raw lines from registry problems), `_read_init`/`_read_init_for_write`/`_write_init` (init.json round-trip preserving unrelated keys; `_read_init_for_write` raises on invalid init.json so mutations never overwrite it), `_derive_init_config` (registry record → init.json `mcp` entry), `_activation_summary` (registry vs init.json cross-reference, redacted), `_log_action` (audit via `agent._log("mcp_manager_action", ...)`, no secrets), and the three `_handle_*` action handlers. `add` and `remove` resolve/validate init.json **before** touching the registry so a corrupt init.json aborts cleanly with no partial write.
- `mcp/inbox.py` — LICC v1 filesystem inbox poller (the **consumer** half). `validate_event` validates required `from`/`subject`/`body` fields; `_format_notification_summary` is **deprecated** legacy helper (retained for backward compat); `_extract_preview_meta` pulls optional IM/chat scalars (`conversation_ref`, `message_ref`, `platform`) out of `event["metadata"]` when present as non-empty strings, each capped at `_PREVIEW_META_FIELD_CAP` (200 chars); `_consume_event` returns `(wake, preview)` where `preview = {"from": sender, "subject": subject, "preview": body[:_PREVIEW_FIELD_CAP], **extracted_meta}` — only the body snippet gets capped (sender/subject are bounded by upstream construction); `_dispatch_summary` publishes to `.notification/mcp.<mcp_name>.json` via `notifications.submit`, embedding full body snippets once in `data.previews` while keeping `instructions` to read/check guidance plus lightweight sender/subject/metadata routing context; `_scan_once` coalesces per MCP, threading the preview list through; `MCPInboxPoller` class drives the poll loop. Body snippet cap is `_PREVIEW_FIELD_CAP = 10000`. Defines the shared contract constants `LICC_VERSION` / `INBOX_DIRNAME` / `DEAD_DIRNAME` / `TMP_SUFFIX` / `EVENT_SUFFIX`.
- `mcp/licc.py` — LICC v1 client (the **producer** half). One public function, `push_inbox_event(sender, subject, body, *, metadata=None, wake=True, received_at=None, agent_dir=None, mcp_name=None, event_id=None) -> bool`, that an out-of-process MCP imports to drop one event into `<agent_dir>/.mcp_inbox/<mcp_name>/<event_id>.json`. Lightweight by design — importing it starts no threads and re-exports the contract constants (`LICC_VERSION`, `INBOX_DIRNAME`, `TMP_SUFFIX`, `EVENT_SUFFIX`) straight from `inbox.py` so producer and consumer never drift. `agent_dir`/`mcp_name` default to env vars `LINGTAI_AGENT_DIR`/`LINGTAI_MCP_NAME` (kernel-injected per MCP); explicit params override for tests/advanced callers. Writes atomically: serialize → `<event_id>.json.tmp` → `flush`+`os.fsync` → `os.replace` onto the final `.json` (the poller ignores `.tmp`, so half-writes are never observed). `event_id` defaults to a fresh `uuid4().hex` (guarantees per-call uniqueness); explicit `mcp_name`/`event_id` path components are validated before use. The payload is checked with `validate_event` before writing, so the canonical producer does not intentionally emit dead-letterable events. Best-effort/silent: missing/invalid target, unsafe path component, invalid payload, or filesystem/serialization error → `False` (never raises into the MCP), with a terse, content-free log that never echoes `body`/`subject`/`metadata`.
- `mcp/manual/` — skill documentation (`SKILL.md`) plus reference docs (`curated-addons.md`, `third-party-and-legacy.md`, `troubleshooting.md`) and scripts (`find_readme.py`).

## Public API

The `mcp` tool is a minimal control plane with exactly three actions — one
read-only inspector and two desired-state mutations. Refresh is **not** part of
`mcp`; it is owned by the `system` tool:

| Action | Kind | Description |
|--------|------|-------------|
| `list` | read | Registry summary + activation summary (init.json `mcp` enabled/gated, redacted). No manual body, no raw problem lines. |
| `add` | mutate | Register a new entry **and** write its init.json `mcp` activation in one step — `record` dict or `name` (catalog entry, with `{python}` substitution); optional `config` overrides the derived activation. Validates, rejects duplicates, rejects invalid init.json without overwriting. Returns `needs_refresh` + a `system(action="refresh")` reminder. |
| `remove` | mutate | Drop a registry entry by `name` (rewrite JSONL) **and** delete its init.json `mcp` activation **and** drop `name` from init.json `addons:` if present — in one step. Errors cleanly if neither registry nor init activation has the name; rejects invalid init.json without overwriting. Returns `needs_refresh` + a `system(action="refresh")` reminder. |

Mutating actions return `needs_refresh: true`, an explicit
`system(action="refresh")` reminder in `message`, and never alter the running
tool surface — only `system(action="refresh")` (via the kernel refresh path) does.
Deleted in the minimal manager: `show`, `diagnose`, `validate`, `enable`,
`disable`, and any in-tool refresh. Those workflows move to the MCP manual plus
`write`/`edit`/`bash`.

### LICC v1 Inbox Protocol

Two halves share one wire format:

- **Producer** (`licc.py`) — an out-of-process MCP calls `push_inbox_event(...)` to atomically drop an event into the inbox. This is the canonical client-side entry point; MCPs should import it rather than hand-rolling the atomic write.
- **Consumer** (`inbox.py`) — `MCPInboxPoller` sweeps the inbox at `POLL_INTERVAL`, validates each event with `validate_event`, coalesces a `.notification/mcp.<name>.json`, and deletes the file.

MCP servers push events via filesystem writes:
```
<agent_working_dir>/.mcp_inbox/<mcp_name>/<event_id>.json
```

```python
from lingtai.core.mcp.licc import push_inbox_event
push_inbox_event("alice", "new DM", "hey, are you around?")  # agent_dir/mcp_name from env
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

Atomic write: write to `<event_id>.json.tmp`, fsync, then rename. `push_inbox_event` does exactly this (`os.replace`) so the poller never observes a half-written file.

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
  │   ├── _append_record()          — appends a validated record as a JSONL line
  │   ├── _remove_record()          — rewrites JSONL minus one name (keeps comments/blanks)
  │   └── _substitute_placeholders()— {python}→sys.executable (shared with decompress + add)
  │
  ├── Control plane (desired-state edits + inspection)
  │   ├── _redact_config()              — env/headers/token-like fields → "<redacted>"
  │   ├── _redact_problems()            — strips raw lines from registry problems
  │   ├── _read_init / _read_init_for_write / _write_init
  │   │                                 — init.json round-trip; _for_write raises on invalid JSON
  │   ├── _derive_init_config()         — registry record → init.json mcp entry
  │   ├── _activation_summary()         — registry vs init.json mcp (enabled/gated, redacted)
  │   ├── _log_action()                 — audit via agent._log("mcp_manager_action", ...)
  │   └── _handle_{list,add,remove}()   — the three actions; add/remove edit registry + init together
  │
  ├── XML builder
  │   ├── _escape_xml()             — XML entity escaping
  │   └── _build_registry_xml()     — renders registry records as <registered_mcp> XML
  │
  ├── Reconciliation
  │   └── _reconcile()              — reads registry, renders XML into prompt (no return value)
  │
  └── Tool surface
      ├── get_description/schema()  — module-level (3-action enum: list/add/remove)
      └── setup()                   — runs initial _reconcile, registers mcp tool,
                                      routes list/add/remove to _handle_*

mcp/inbox.py
  ├── Validation
  │   └── validate_event()              — validates a parsed LICC event
  │
  ├── Dispatch (signal-only since issue #37, .notification/ since this fix)
  │   ├── _format_notification_summary()— DEPRECATED; retained for backward compat
  │   ├── _consume_event()              — per-event log + wake intent collector
  │   └── _dispatch_summary()           — publishes to .notification/mcp.<name>.json
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

mcp/licc.py  (client-side producer; mirrors inbox.py's consumer)
  ├── Re-exports                        — LICC_VERSION / INBOX_DIRNAME / TMP_SUFFIX / EVENT_SUFFIX (from inbox.py)
  └── push_inbox_event()                — resolve agent_dir/mcp_name (args or env) → build v1 payload
                                          → atomic .tmp + fsync + os.replace → True / False (no-op or OSError)
```

## Key Invariants

- **Registry is line-oriented JSONL:** One record per line. `add` appends; `remove` rewrites the file minus the matching name (preserving other lines, comments, and blanks best-effort). Duplicates by name are flagged as problems during read and rejected by `add`. Hand-editing via `write`/`edit` remains valid.
- **Control plane edits desired state only:** `add`/`remove` write `mcp_registry.jsonl` **and** `init.json` together and return `needs_refresh: true` with an explicit `system(action="refresh")` reminder. They never touch the live tool surface or the seal. Refresh is owned by the `system` tool — `mcp` has no refresh action; `system(action="refresh")` routes through the existing `.refresh` signal-file path (`base_agent/lifecycle.py` heartbeat → `_perform_refresh` at a turn boundary).
- **add/remove are all-or-nothing across registry + init:** Both resolve and validate `init.json` (via `_read_init_for_write`, which raises on invalid JSON) **before** mutating the registry. An invalid `init.json` aborts with an error and **no** file is overwritten — registry and init never drift out of sync. `add` derives the init activation from the record (or an explicit `config`) and rejects duplicates; `remove` errors cleanly when neither the registry nor the init activation holds the name.
- **Secrets never leave the agent:** Any returned config (`list` activation entries, `add` config echo) and audit log passes through `_redact_config` — values under `env`/`headers` and token/password/secret/key/authorization-like field names become `"<redacted>"`. Registry problems pass through `_redact_problems` so raw invalid lines (which may carry env/token fragments) are never echoed. The registry XML rendered into the prompt holds no secrets.
- **init.json round-trip preserves unrelated keys:** `add`/`remove` read the full manifest, mutate only `mcp[name]` (and, on remove, the `addons:` list), and rewrite — provider, model, and other keys are untouched. The registry gate (a live init entry must be in `mcp_registry.jsonl`) is preserved: `add` writes the registry record alongside the activation; `remove` strips both so it leaves nothing gated.
- **Name convention:** Lowercase, dash-separated, bounded length (`^[a-z][a-z0-9_-]{0,30}$`).
- **Transport validation:** `stdio` requires `command` + `args`; `http` requires `url`.
- **Addons decompression is idempotent:** Running `decompress_addons()` multiple times produces the same registry. Existing records are never modified.
- **`{python}` substitution:** Catalog entries support `{python}` placeholder in command args, resolved to `sys.executable` at decompression time.
- **LICC atomicity:** Events must be written to `.json.tmp` then renamed to `.json`. Half-written `.tmp` files are ignored by the scanner. `licc.push_inbox_event` is the canonical producer that performs this (`flush` + `os.fsync` + `os.replace`); MCPs should call it rather than re-implement the dance.
- **LICC client is best-effort, path-safe, and receiver-validating:** `push_inbox_event` never raises into the calling MCP. Missing agent dir / mcp name (neither arg nor env var set), invalid MCP names, unsafe explicit event IDs, or payload fields rejected by `validate_event` → `False` no-op; filesystem/serialization errors → `False`. Failure logs are terse and never echo `body`/`subject`/`metadata` (which may carry user content or secrets). Producer and consumer share the contract constants and validation because `licc.py` imports them from `inbox.py` — they cannot drift.
- **LICC dead-letter:** Invalid events (parse errors, missing fields, unknown version, dispatch failures) are moved to `.dead/` with a `.error.json` sidecar. Dead-letters are never auto-deleted.
- **LICC bounded work:** `MAX_EVENTS_PER_CYCLE = 100` per MCP per sweep prevents pathological backlog from blocking the poller.
- **LICC notification shape (post-#37 + previews):** The coalesced notification carries the MCP name, event count, plus a `previews` list — one entry per consumed event with `{"from": <sender>, "subject": <subject>, "preview": <body[:_PREVIEW_FIELD_CAP]>}` and, **when the event opts in via `metadata`**, optional IM/chat scalars `conversation_ref`, `message_ref`, `platform` (each capped at `_PREVIEW_META_FIELD_CAP = 200` chars). Only well-formed non-empty string metadata values are copied; non-string/empty/unknown keys are silently ignored, so legacy events without metadata produce the identical preview shape as before. The body snippet is hard-truncated at `_PREVIEW_FIELD_CAP` (10000 chars); `from` and `subject` pass through uncapped (sender bounded by upstream construction; subject already validated `<= 200` chars by `validate_event`). Full message **bodies** are still NOT inlined — those stay behind the `<mcp>(action="check"/"read")` tool result. The original issue #37 invariant (no body duplication → no agent re-processing loop) is preserved; previews exist purely to let the agent triage which MCPs/messages deserve a read call. Multiple events from the same MCP in one sweep are coalesced into a single summary; `wake` is the OR of all per-event `wake` flags. Preview list length is naturally bounded by `MAX_EVENTS_PER_CYCLE` (100).
- **LICC uses `.notification/` filesystem-as-protocol:** `_dispatch_summary` publishes via `notifications.submit` to `.notification/mcp.<mcp_name>.json` instead of posting to the legacy inbox queue. This unifies MCP events with all other notification producers (email, soul, system events) in the kernel's `_sync_notifications` wire injection path.
- **Audit:** Every mutating action (`add`/`remove`) calls `_log_action` → `agent._log("mcp_manager_action", action, name, status)`. The audit carries names and outcomes only — never config or secrets.

## Dependencies

- `yaml` (PyYAML) — used by the skills capability's frontmatter parser (imported transitively; not directly used here)
- `lingtai.i18n` — `t()` for localized strings (imported but the description is hardcoded English)
- `lingtai_kernel.notifications` — `submit` (as `publish_notification`) for `.notification/` dispatch (in `inbox.py`)
- `lingtai_kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)
- `lingtai.mcp_catalog.json` — kernel-shipped MCP catalog file (read at runtime)
- `lingtai.core.mcp.inbox` — `licc.py` imports the contract constants (`LICC_VERSION`, `INBOX_DIRNAME`, `TMP_SUFFIX`, `EVENT_SUFFIX`) from it; stdlib only otherwise (`json`, `os`, `uuid`, `datetime`, `pathlib`, `logging`)
- env: `LINGTAI_AGENT_DIR` / `LINGTAI_MCP_NAME` — kernel-injected per spawned MCP (see `lingtai.agent`); the default source for `push_inbox_event`'s target

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `daemon/`, `avatar/`, `knowledge/` (private durable memory), `skills/` (skill catalog), `bash/`.
- **Manual:** `mcp/manual/SKILL.md` — registration contract and usage guide.
- **Kernel hooks:** `setup()` is called during capability initialization; `decompress_addons()` is called by the Agent initializer before `setup`. `MCPInboxPoller.start()/stop()` are called by the agent lifecycle.
