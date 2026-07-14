---
related_files:
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - src/lingtai/ANATOMY.md
  - src/lingtai/tools/mcp/__init__.py
  - src/lingtai/services/mcp_inbox.py
  - src/lingtai/services/mcp_licc.py
  - src/lingtai/tools/mcp/manual/SKILL.md
  - src/lingtai/mcp_servers/ANATOMY.md
  - tests/test_mcp_capability.py
  - tests/test_mcp_inbox.py
  - src/lingtai/tools/mcp/glossary-en.md
  - src/lingtai/tools/mcp/glossary-zh.md
  - src/lingtai/tools/mcp/glossary-wen.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# lingtai/tools/mcp + lingtai/services/mcp_* (split)

MCP capability — per-agent registry of MCP (Model Context Protocol) servers.
Pure presentation: reads the registry from disk, validates records, and renders
it as XML into the system prompt. No tool writes; all registry mutations happen
via file operations from the agent (`write`, `edit`).

Also includes the **LICC v1 (LingTai Inbox Callback Contract)** — a
filesystem-based inbox that lets out-of-process MCP servers push events into
the agent's inbox.

The model-visible notification projection for LICC events is governed by `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md`; touching `inbox.py`, `licc.py`, or curated human-message producer metadata must re-check that contract.

## Components

- `mcp/__init__.py` — MCP tool surface (the tool slice, ~173 lines). `get_description` (`src/lingtai/tools/mcp/__init__.py:137`), `get_schema` (`src/lingtai/tools/mcp/__init__.py:141`), `setup` (`src/lingtai/tools/mcp/__init__.py:145`), `_reconcile` (`src/lingtai/tools/mcp/__init__.py:53`), `_manual` (`src/lingtai/tools/mcp/__init__.py:85`), `handle_mcp` (`src/lingtai/tools/mcp/__init__.py:155`). The registry infra now lives in `src/lingtai/services/mcp_registry.py`: `validate_record` (`src/lingtai/services/mcp_registry.py:72`), `validate_registry_line` (`src/lingtai/services/mcp_registry.py:118`), `read_registry` (`src/lingtai/services/mcp_registry.py:139`), `read_identities` (`src/lingtai/services/mcp_registry.py:253`), `decompress_addons` (`src/lingtai/services/mcp_registry.py:316`), `_load_catalog` (`src/lingtai/services/mcp_registry.py:43`), `_build_registry_xml` (`src/lingtai/services/mcp_registry.py:419`). **Account-identity discovery** (`src/lingtai/services/mcp_registry.py:253`): `read_identities` reads each addon's non-secret identity document at `system/mcp_identities/<name>.json` (schema `lingtai.mcp.identity.v1`) and projects every account through the secret-safe allowlist `IDENTITY_SAFE_ACCOUNT_KEYS` (`src/lingtai/services/mcp_registry.py:211`) via `_project_account` (`src/lingtai/services/mcp_registry.py:242`). The tool-slice `_reconcile` attaches the projected identity to each `registered` entry (`_registered_entry`, `src/lingtai/tools/mcp/__init__.py:45`) and the registry XML builder renders it into the prompt XML (`_build_identity_xml`, `src/lingtai/services/mcp_registry.py:399`). The projection is an allowlist, not a passthrough — tokens/passwords/secrets/headers and any unrecognized field are dropped even if an on-disk identity file contains them, so the generic surface can never leak what a producer bug might persist. The prompt render further narrows to `_PROMPT_ACCOUNT_KEYS` (`src/lingtai/services/mcp_registry.py:394`), which excludes `last_verified_at`: that field, and the identity-level `<last_verified_at>` that used to precede the account list, are volatile re-verification timestamps that change on plain refresh and would otherwise break prompt-cache stability. `read_identities` and the `mcp(action="info")` diagnostic payload still surface `last_verified_at` — only the model-facing prompt XML omits it.
- `mcp/inbox.py` — LICC v1 filesystem inbox poller (the **consumer** half). `validate_event` validates required `from`/`subject`/`body` fields; `_format_notification_summary` is **deprecated** legacy helper (retained for backward compat); `_extract_preview_meta` pulls optional IM/chat scalars (`conversation_ref`, `message_ref`, `platform`) out of `event["metadata"]` when present as non-empty strings, each capped at `_PREVIEW_META_FIELD_CAP` (200 chars), and curated IM structured fields (`latest_incoming`, `recent_messages`, `referenced_messages` — the full reply target when it falls outside the last-20 window) after a bounded JSON-safe copy — these feed the kernel builder for `_meta.notification_persistent.mcp.<channel>` (currently Telegram, WeChat, Feishu, WhatsApp) and are then stripped from the model-visible ephemeral lane by the per-channel `meta_block.sanitize_*_notification_after_persistent` wrappers (move, not duplicate — Jason #6148); `_consume_event` returns `(wake, preview)` where `preview = {"from": sender, "subject": subject, "preview": body[:_PREVIEW_FIELD_CAP], "preview_truncated": bool, **extracted_meta}` — only the body snippet gets capped (sender/subject are bounded by upstream construction); `_dispatch_summary` publishes to `.notification/mcp.<mcp_name>.json` via `notifications.submit`, embedding full body snippets once in `data.previews` while keeping `instructions` to read/check guidance plus lightweight sender/subject/metadata routing context; `_scan_once` coalesces per MCP, threading the preview list through; `MCPInboxPoller` class drives the poll loop. Body snippet cap is `_PREVIEW_FIELD_CAP = 10000`. Defines the shared contract constants `LICC_VERSION` / `INBOX_DIRNAME` / `DEAD_DIRNAME` / `TMP_SUFFIX` / `EVENT_SUFFIX`.
- `mcp/licc.py` — LICC v1 client (the **producer** half). One public function, `push_inbox_event(sender, subject, body, *, metadata=None, wake=True, received_at=None, agent_dir=None, mcp_name=None, event_id=None) -> bool`, that an out-of-process MCP imports to drop one event into `<agent_dir>/.mcp_inbox/<mcp_name>/<event_id>.json`. Lightweight by design — importing it starts no threads and re-exports the contract constants (`LICC_VERSION`, `INBOX_DIRNAME`, `TMP_SUFFIX`, `EVENT_SUFFIX`) straight from `inbox.py` so producer and consumer never drift. `agent_dir`/`mcp_name` default to env vars `LINGTAI_AGENT_DIR`/`LINGTAI_MCP_NAME` (kernel-injected per MCP); explicit params override for tests/advanced callers. Writes atomically: serialize → `<event_id>.json.tmp` → `flush`+`os.fsync` → `os.replace` onto the final `.json` (the poller ignores `.tmp`, so half-writes are never observed). `event_id` defaults to a fresh `uuid4().hex` (guarantees per-call uniqueness); explicit `mcp_name`/`event_id` path components are validated before use. The payload is checked with `validate_event` before writing, so the canonical producer does not intentionally emit dead-letterable events. Best-effort/silent: missing/invalid target, unsafe path component, invalid payload, or filesystem/serialization error → `False` (never raises into the MCP), with a terse, content-free log that never echoes `body`/`subject`/`metadata`.
- `mcp/manual/` — skill documentation (`SKILL.md`) plus reference docs (`curated-addons.md`, `third-party-and-legacy.md`, `troubleshooting.md`) and scripts (`find_readme.py`).

## Public API

The `mcp` tool exposes two signpost actions:

| Action | Description |
|--------|-------------|
| `info` | Re-read the MCP registry and return runtime health (registry contents, problems, registry path) without the manual body. Each `registered` entry may carry a non-secret `identity` block (account alias, provider username/id/display name, non-secret routing counts) read from `system/mcp_identities/<name>.json` when the addon has published one. |
| `manual` | Return the mcp-manual skill body on demand. |

### LICC v1 Inbox Protocol

Two halves share one wire format:

- **Producer** (`licc.py`) — an out-of-process MCP calls `push_inbox_event(...)` to atomically drop an event into the inbox. This is the canonical client-side entry point; MCPs should import it rather than hand-rolling the atomic write.
- **Consumer** (`inbox.py`) — `MCPInboxPoller` sweeps the inbox at `POLL_INTERVAL`, validates each event with `validate_event`, coalesces a `.notification/mcp.<name>.json`, and deletes the file.

MCP servers push events via filesystem writes:
```
<agent_working_dir>/.mcp_inbox/<mcp_name>/<event_id>.json
```

```python
from lingtai.services.mcp_licc import push_inbox_event
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
  │   └── _append_record()          — appends a validated record as a JSONL line
  │
  ├── Account-identity discovery (read-only, secret-safe)
  │   ├── IDENTITY_SAFE_ACCOUNT_KEYS — allowlist of non-secret per-account keys
  │   ├── _project_account()        — projects one account dict to the allowlist
  │   └── read_identities()         — reads system/mcp_identities/*.json (lingtai.mcp.identity.v1)
  │
  ├── XML builder
  │   ├── _escape_xml()             — XML entity escaping
  │   ├── _build_identity_xml()     — renders a non-secret <identity> block per MCP
  │   └── _build_registry_xml()     — renders registry records as <registered_mcp> XML
  │
  ├── Reconciliation
  │   ├── _registered_entry()       — builds one registered entry, attaching identity when present
  │   └── _reconcile()              — reads registry + identities, renders into prompt, returns snapshot
  │
  └── Tool surface
      ├── get_description/schema()  — module-level
      └── setup()                   — registers mcp tool, runs initial _reconcile

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

- **Registry is append-only JSONL:** One record per line. Duplicates by name are flagged as problems during read. Mutations (register, deregister, update) happen via agent-side file operations.
- **Name convention:** Lowercase, dash-separated, bounded length (`^[a-z][a-z0-9_-]{0,30}$`).
- **Transport validation:** `stdio` requires `command` + `args`; `http` requires `url`.
- **Addons decompression is idempotent:** Running `decompress_addons()` multiple times produces the same registry. Existing records are never modified.
- **`{python}` substitution:** Catalog entries support `{python}` placeholder in command args, resolved to `sys.executable` at decompression time.
- **LICC atomicity:** Events must be written to `.json.tmp` then renamed to `.json`. Half-written `.tmp` files are ignored by the scanner. `licc.push_inbox_event` is the canonical producer that performs this (`flush` + `os.fsync` + `os.replace`); MCPs should call it rather than re-implement the dance.
- **LICC client is best-effort, path-safe, and receiver-validating:** `push_inbox_event` never raises into the calling MCP. Missing agent dir / mcp name (neither arg nor env var set), invalid MCP names, unsafe explicit event IDs, or payload fields rejected by `validate_event` → `False` no-op; filesystem/serialization errors → `False`. Failure logs are terse and never echo `body`/`subject`/`metadata` (which may carry user content or secrets). Producer and consumer share the contract constants and validation because `licc.py` imports them from `inbox.py` — they cannot drift.
- **LICC dead-letter:** Invalid events (parse errors, missing fields, unknown version, dispatch failures) are moved to `.dead/` with a `.error.json` sidecar. Dead-letters are never auto-deleted.
- **LICC bounded work:** `MAX_EVENTS_PER_CYCLE = 100` per MCP per sweep prevents pathological backlog from blocking the poller.
- **LICC notification projection contract:** raw `.notification/mcp.<name>.json` previews are only the producer mirror; once a producer has a persistent context lane, model-visible `_meta.notifications` must be reduced to a minimal identity hook and content must move to `_meta.notification_persistent` (see `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md`).
- **LICC notification shape (post-#37 + previews):** The coalesced notification carries the MCP name, event count, plus a `previews` list — one entry per consumed event with `{"from": <sender>, "subject": <subject>, "preview": <body[:_PREVIEW_FIELD_CAP]>}` and, **when the event opts in via `metadata`**, optional IM/chat scalars `conversation_ref`, `message_ref`, `platform` (each capped at `_PREVIEW_META_FIELD_CAP = 200` chars) and bounded JSON-safe structured fields `recent_messages` / `latest_incoming` / `referenced_messages` (each capped at `_PREVIEW_STRUCTURED_META_JSON_CAP = 20000` JSON chars) that curated IM producers (Telegram, WeChat, Feishu, WhatsApp) attach to feed the kernel `_meta.notification_persistent.mcp.<name>` lane. Only well-formed non-empty string metadata values are copied; non-string/empty/unknown keys are silently ignored, so legacy events without metadata produce the identical preview shape as before. The body snippet is hard-truncated at `_PREVIEW_FIELD_CAP` (10000 chars); `from` and `subject` pass through uncapped (sender bounded by upstream construction; subject already validated `<= 200` chars by `validate_event`). Full message **bodies** are still NOT inlined — those stay behind the `<mcp>(action="check"/"read")` tool result. The original issue #37 invariant (no body duplication → no agent re-processing loop) is preserved; previews exist purely to let the agent triage which MCPs/messages deserve a read call. Multiple events from the same MCP in one sweep are coalesced into a single summary; `wake` is the OR of all per-event `wake` flags. Preview list length is naturally bounded by `MAX_EVENTS_PER_CYCLE` (100).
- **LICC uses `.notification/` filesystem-as-protocol:** `_dispatch_summary` publishes via `notifications.submit` to `.notification/mcp.<mcp_name>.json` instead of posting to the legacy inbox queue. This unifies MCP events with all other notification producers (email, soul, system events) in the kernel's `_sync_notifications` wire injection path.
- **Pure presentation:** The capability never writes to the registry file. It only reads and renders.
- **Private reverse channel via an unlisted MCP tool name (kernel-driven):** A server may expose a private capability through a *tool name that ``list_tools`` never returns* — e.g. Telegram's ``_lingtai_telegram_task_card``. Because the name is unlisted, ``mcp.server.lowlevel.Server.call_tool`` (default ``validate_input=True``) finds no cached tool definition, **skips** input validation, and still invokes the registered handler; the public ``telegram`` name keeps its default schema validation unchanged. The kernel invokes the private name directly through ``MCPClient.call_tool(tool_name, args)`` on a client reference obtained from ``agent._mcp_clients_by_tool`` (a stable ``tool_name → client`` mapping built at MCP registration time in ``agent.connect_mcp`` / ``agent.connect_mcp_http`` at ``src/lingtai/agent.py:933-938,990-995``), which bypasses ``ToolExecutor`` entirely (no recursion through the tool dispatch pipeline). The kernel sends **no** public ``action``; when the private name arrives, the server copies the arguments and **forces** ``action="_task_card_update"`` before ``manager.handle`` (``src/lingtai/mcp_servers/telegram/server.py``, ``build_server._call_tool`` branch on ``_PRIVATE_TASK_CARD_TOOL``), so the hidden route can only ever project the card — it cannot be repurposed for any public Telegram action. The public ``telegram`` name with a guessed ``action="_task_card_update"`` is still rejected by the unchanged public ``SCHEMA`` (the action is absent from its enum), and any other unlisted name is rejected by the handler. This is strictly smaller than validating manually: no ``validate_input=False`` and no re-implemented jsonschema check. The kernel-side name is mirrored as ``_TASK_CARD_TOOL`` in ``lingtai.kernel.base_agent`` (a literal, since the kernel must not import ``mcp_servers``); the two must stay in sync. Regression coverage that drives the real ``Server.request_handlers[CallToolRequest]`` boundary lives in ``tests/test_telegram_task_card_transport.py``.
- **Task Card batch/heartbeat hooks (``_on_tool_pre_dispatch_hook`` + ``_on_tool_result_hook``):** ``BaseAgent`` exposes two optional lifecycle hooks that ``ToolExecutor`` calls around each tool. The pre-dispatch hook (``src/lingtai/kernel/base_agent/__init__.py:2384``) receives ``(tool_name, tool_args, tool_call_id)`` and builds **one card row per tool call** appended in actual pre-dispatch order: because parallel pre-dispatch hooks are serialized before any future starts (``_execute_parallel`` Phase 2 at ``src/lingtai/kernel/tool_executor.py:1424-1432``), that order is deterministic. The card keeps a **rolling window of the newest N ordinary tool rows** where N is ``_task_card_max_tool_rows()`` (valid persisted ``<workdir>/telegram/taskcard.json`` ``normal_rows`` 1-10 first; legacy positive ``LINGTAI_TASK_CARD_MAX_TOOL_ROWS`` / default 1 otherwise) — a tool arriving with no active tool predecessor opens a new epoch (bumping a generation counter and starting the heartbeat), but completed tool rows are **not** cleared down to only the current tool; instead ``_cap_task_card_tool_rows`` (``src/lingtai/kernel/base_agent/__init__.py:2453``) evicts the oldest surplus tool rows in place while retaining the API-error row. The post-dispatch result hook (``_on_tool_result_hook`` at ``src/lingtai/kernel/base_agent/__init__.py:2321``) is the completion signal: ``ToolExecutor`` fires it on the orchestrating thread in input order in the sequential, parallel, tool-error, intercept, and raised-dispatch-exception paths, and ``_freeze_task_card_row`` (``src/lingtai/kernel/base_agent/__init__.py:2351``) freezes the matching row (final whole-second elapsed + ``done`` marker) while other rows keep ticking; a row already evicted from the window has no matching ``call_id``, so its late result is a no-op and cannot mutate the window. Rendering (create lazily, else edit the same card) is ``_render_task_card``; create/update/finalize carry a numeric-only canonical session metadata snapshot for the adapter's bounded footer block; the 0.5s monotonic heartbeat is ``_task_card_heartbeat_tick`` (``src/lingtai/kernel/base_agent/__init__.py:2846``) driven by ``_start_task_card_heartbeat`` (``src/lingtai/kernel/base_agent/__init__.py:2881``), with elapsed floored to whole seconds by ``_task_card_elapsed`` (``src/lingtai/kernel/base_agent/__init__.py:2583``) so the display shows integer seconds (half-second frames read ``0s, 0s, 1s, 1s, 2s``) with no decimal point. The single reused heartbeat thread is kept safe against stale writes by reading ``ctx["rows"]``/``ctx["generation"]`` freshly under the lock each tick and by the all-rows-done early return (which never overwrites the frozen last-behavior state); the tick's explicit ``generation`` parameter is retained for the stale-timer/test defense that simulates an old epoch's tick. Both hooks are best-effort and never block or mutate a tool result: the reverse-call MCP client reports tool-level failures as an error *dict* (and can also raise), so the hooks inspect the payload via ``_task_card_result_error`` / ``_task_card_result_message_id`` (no fake success), treat Telegram's explicit ``{status: ok, suppressed: true, taskcard: false}`` as deliberate non-error presentation suppression without inventing a card id or stopping rows/heartbeats, stay fail-open, and are made observable through the content-free ``_log_task_card_reverse_failure`` / ``_log_task_card_reverse_exception`` warnings (only ``phase``/``tool`` + result ``status`` or exception *class*, never reasoning, chat id, account, card id, or provider text). ``ToolExecutor._invoke_result_hook`` (``src/lingtai/kernel/tool_executor.py:1043``) isolates the result hook so a raising hook can never be caught by the dispatch error handler and turned into a fake error, and ``_invoke_result_hook_observe`` (``src/lingtai/kernel/tool_executor.py:1071``) runs it on the intercept path without letting it override the tool's intercept. Turn lifecycle cleanup lives in ``_handle_request`` / ``_handle_tc_wake`` (``src/lingtai/kernel/base_agent/turn.py:1123,1332``) via ``try/finally`` blocks that call ``_teardown_telegram_task_card``, which stops the heartbeat and freezes the resident card on its concrete last batch (no generic overall ``DONE``). The per-account+chat resident-card singleton and its ``state.json`` persistence live in the Telegram adapter (see ``src/lingtai/mcp_servers/ANATOMY.md``).
- **LLM/provider API errors surfaced to the automatic Task Card (observe-only):** provider API failures in a Telegram-originated turn (e.g. a ``429 usage_limit_reached`` before any tool, or an error after tool results) are surfaced to the same automatic card even though no tool call produced them. The AED loop in ``_run_loop`` catches provider errors on the **orchestrating** thread (``turn.py`` ``except Exception`` block) and reports through the fail-open module wrappers ``_report_api_error_to_task_card`` (``src/lingtai/kernel/base_agent/turn.py:130``) and ``_recover_api_error_on_task_card`` (``src/lingtai/kernel/base_agent/turn.py:149``): the transient-retry path (``src/lingtai/kernel/base_agent/turn.py:772``), the deterministic-AED path (``src/lingtai/kernel/base_agent/turn.py:830``), and the success path (``src/lingtai/kernel/base_agent/turn.py:676``, marks ``recovered``). Terminal truth is not merely ``aed_attempts >= max`` — the deterministic path reports the row ``terminal`` only when no next recovery remains (i.e. no viable, not-yet-attempted preset fallback), so a successful preset auto-fallback never renders a false ``failed`` before ``_perform_refresh``; if preset activation itself raises, the same row is then frozen ``error`` before ASLEEP. Because ``_handle_request`` tears down the card context in a ``finally`` before the outer AED catch runs, an **after-tool continuation** provider error is additionally reported from the continuation-send ``except`` while the context is still live (``src/lingtai/kernel/base_agent/turn.py:1902``, observe-only/fail-open, re-raised unchanged); the stable single-row upsert keeps that safe against a later AED report. Both wrappers ``getattr`` the optional BaseAgent hook and swallow any failure, so reporting can never change the retry/fallback decision, the eventual success/failure, or token accounting. ``BaseAgent._report_task_card_api_error`` (``src/lingtai/kernel/base_agent/__init__.py:2763``) upserts **one stable API-error row per turn/retry sequence** (sentinel ``_TASK_CARD_API_ROW_ID`` at ``src/lingtai/kernel/base_agent/__init__.py:2671``) into the current batch and renders through the same ``_render_task_card`` create-or-edit path — repeated failures update the row, never a card per error — and ``_recover_task_card_api_error`` (``src/lingtai/kernel/base_agent/__init__.py:2817``) freezes it ``recovered`` while preserving that an error happened. The surfaced summary contains only bounded machine fields: sanitized exception class, public provider/model, a structured HTTP status in 100-599, a machine code strictly allow-listed against ``_TASK_CARD_SAFE_API_CODES``, and retry/recovered/failed lifecycle; opaque external identifiers such as request-id attributes/headers are intentionally omitted. It never calls ``str(exc)`` or renders message/body/free-form headers, so arbitrary URLs, tokens, prompts, tracebacks, or paths cannot reach the card. The Telegram adapter renders the API-error row with ``TelegramManager._format_api_error_line`` (``src/lingtai/mcp_servers/telegram/manager.py:2225``); the row carries the fixed no-reply footer and obeys the same resident-card transport discipline: edit the persisted id in place, treat Telegram's identical-content response as success, fail loud without replacement on unknown/transient edit errors, and replace only after a provider-confirmed edit-impossible response (see ``src/lingtai/mcp_servers/ANATOMY.md``). A non-Telegram/no-route turn (no ``_telegram_task_card_context``) is a no-op.

## Dependencies

- `yaml` (PyYAML) — used by the skills capability's frontmatter parser (imported transitively; not directly used here)
- `lingtai.i18n` — `t()` for localized strings (imported but the description is hardcoded English)
- `lingtai.kernel.notifications` — `submit` (as `publish_notification`) for `.notification/` dispatch (in `inbox.py`)
- `lingtai.kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)
- `lingtai.mcp_catalog.json` — kernel-shipped MCP catalog file (read at runtime)
- `lingtai.services.mcp_inbox` — `licc.py` imports the contract constants (`LICC_VERSION`, `INBOX_DIRNAME`, `TMP_SUFFIX`, `EVENT_SUFFIX`) from it; stdlib only otherwise (`json`, `os`, `uuid`, `datetime`, `pathlib`, `logging`)
- env: `LINGTAI_AGENT_DIR` / `LINGTAI_MCP_NAME` — kernel-injected per spawned MCP (see `lingtai.agent`); the default source for `push_inbox_event`'s target

## Composition

- **Parent:** `src/lingtai/tools/` (tool slice); infra siblings live in `src/lingtai/services/`.
- **Siblings:** `daemon/`, `avatar/`, `knowledge/` (private durable memory), `skills/` (skill catalog), `bash/`.
- **Manual:** `mcp/manual/SKILL.md` — registration contract and usage guide.
- **Kernel hooks:** `setup()` is called during capability initialization; `decompress_addons()` is called by the Agent initializer before `setup`. `MCPInboxPoller.start()/stop()` are called by the agent lifecycle.
