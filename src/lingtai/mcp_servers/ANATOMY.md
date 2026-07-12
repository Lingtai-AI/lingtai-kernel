---
related_files:
  - src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md
  - pyproject.toml
  - src/lingtai/ANATOMY.md
  - src/lingtai/tools/mcp/ANATOMY.md
  - src/lingtai/mcp_catalog.json
  - src/lingtai/mcp_servers/__init__.py
  - src/lingtai/mcp_servers/_identity.py
  - src/lingtai/mcp_servers/_skill.py
  - src/lingtai/mcp_servers/daemon_common/server.py
  - src/lingtai/mcp_servers/cloud_mail/manager.py
  - src/lingtai/mcp_servers/feishu/manager.py
  - src/lingtai/mcp_servers/imap/manager.py
  - src/lingtai/mcp_servers/telegram/manager.py
  - src/lingtai/mcp_servers/wechat/manager.py
  - src/lingtai/mcp_servers/whatsapp/manager.py
  - tests/test_cloud_mail_addon.py
  - tests/test_mcp_skill_manuals.py
  - tests/test_telegram_rich_formatting.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# lingtai.mcp_servers

Curated and built-in MCP server package implementations shipped inside the `lingtai` Python distribution. They are launched by catalog/script entry points such as `python -m lingtai.mcp_servers.<name>` and expose real addon tools (IMAP, Telegram, Feishu, WeChat, WhatsApp, Cloud Mail), daemon lifecycle tools, plus bundled progressive-disclosure manuals.

## Components

| File / folder | Role |
|---|---|
| `_skill.py` | Shared bundled-skill helper: re-exports the kernel-owned `split_frontmatter` from `lingtai.kernel._frontmatter` (one impl shared with the prompt-section catalog; kernel never imports the wrapper), `load_skill()` loads package `SKILL.md`, `manual_action_description()` injects frontmatter into the schema, and `manual_payload()` returns the manual body + absolute path without sidecar lists (`_skill.py:36-79`). |
| `_identity.py` | Shared public-identity envelope/path/write helper for curated messaging MCPs: builds the `lingtai.mcp.identity.v1` document, computes `system/mcp_identities/<name>.json`, and performs the newline-terminated atomic JSON write. Provider-specific account fields and redaction stay in each provider. |
| `daemon_common/` | Built-in daemon lifecycle MCP. `daemon_common/server.py:1-151` exposes `finish(status, summary?, reason?, artifacts?)`, validates the call, and atomically writes the internal per-run `daemon_completion.json` file named by `LINGTAI_DAEMON_COMPLETION_FILE`; daemon runners validate that file before allowing success. |
| `telegram/`, `imap/`, `feishu/`, `wechat/`, `whatsapp/`, `cloud_mail/` | Curated MCPs using `_skill.py` for their `action="manual"` payloads (`telegram/manager.py`, `imap/manager.py`, `feishu/manager.py`, `wechat/manager.py`, `whatsapp/manager.py`, `cloud_mail/manager.py`). Messaging MCPs with runtime account identity also delegate their identity envelope/path/write policy to `_identity.py`; curated IM notifications package bounded structured `recent_messages` / `latest_incoming` metadata (Telegram also includes `referenced_messages` when the current message replies to one outside the last-20 window) plus generic `platform` / `conversation_ref` / `message_ref` routing keys for the kernel `_meta.notification_persistent.mcp.<channel>` lanes. Telegram is a last-20 delta lane; WeChat and Feishu are last-10 delta lanes; WhatsApp is a last-10 snapshot lane with no `previous_block`. Durable message text lives in `_meta.notification_persistent`, not in the ephemeral notification hook — Jason #6148. |
| Per-package `SKILL.md` | The human/agent-facing bundled manual. If a manual has sidecars, the sidecar inventory and relative paths live in this markdown, not in the tool payload. |
| `pyproject.toml` package-data entries | Ships every curated MCP `SKILL.md`; `reference/**/*` and `assets/**/*` are also packaged for future sidecar files (`pyproject.toml:81-86`). |

## Connections

- Catalog/script launchers (`pyproject.toml:43-49`) start these servers as subprocess MCPs; agents activate them through the generic MCP capability (`src/lingtai/tools/mcp/ANATOMY.md`).
- Manager schemas include `manual` in each action enum and use `_skill.manual_action_description()` to advertise the bundled skill without loading the full body into the resident schema.
- Tests pin the manual contract, package-data sidecar support, and Telegram parity in `tests/test_mcp_skill_manuals.py` and `tests/test_telegram_rich_formatting.py`.

## Composition

Parent: `src/lingtai/` wrapper package (`src/lingtai/ANATOMY.md`). Sibling wrapper areas include `agent.py`, `core/`, `services/`, and `intrinsic_skills/`. Curated MCPs are independent subprocess packages, not intrinsic capabilities.

## State

The package itself is mostly code + packaged manuals. Runtime state is per-agent and server-specific: e.g. message caches, contacts, inbox replay guards, credential-derived identities, or daemon completion sentinels live under the agent workdir/run dir or `.secrets/`, not in `src/lingtai/mcp_servers/`. The shared manual and identity helpers have no persistent state of their own. Telegram's per-account non-secret `state.json` (under `<workdir>/telegram/<alias>/`) additionally persists the resident Task Card id per chat under a `task_cards` map (`{chat_id: compound_id}`), loaded backward-compatibly and normalized against malformed values by `TelegramAccount._normalize_task_cards`, so the one-card-per-account+chat singleton survives a refresh and the next card can still delete the prior one.

## Notes

- **Notification contract:** curated messaging MCPs that change structured notification metadata (`recent_messages`, `latest_incoming`, `referenced_messages`, stable IDs, routing hooks, or preview/body placement) must check `src/lingtai/services/LICC_NOTIFICATION_CONTRACT.md` in the same change.
- **Manual sidecar minimal contract:** `action="manual"` returns the main `SKILL.md` body, parsed metadata, and the main `SKILL.md` absolute `path` only. Concrete `assets/` and `reference/` lists MUST NOT be returned as structured tool fields; `SKILL.md` is the single source of truth for what sidecars exist and how to follow their relative paths.
- **Packaging discipline:** when adding manual sidecars, put their relative paths in `SKILL.md` and keep the package-data globs for `reference/**/*` / `assets/**/*` so wheels contain them (`pyproject.toml:81-86`).
- **Telegram private reverse-channel tool:** `telegram/server.py` `list_tools` advertises only the public `telegram` tool (validated against `SCHEMA` by the mcp library's default `validate_input=True`). Its `build_server._call_tool` also accepts one **unlisted** private tool name, `_PRIVATE_TASK_CARD_TOOL = "_lingtai_telegram_task_card"` (`telegram/server.py:64`), used solely by the kernel to project the live Task Card; being unlisted, it skips public-schema validation yet still reaches the handler, which forces `action="_task_card_update"` before `manager.handle` so the hidden route cannot invoke any public action. The mechanism, kernel caller (`_TASK_CARD_TOOL`), and regression tests are described in `src/lingtai/tools/mcp/ANATOMY.md`.
- **Resident Task Card singleton, one per account+chat; create is update-first (Jason #6665/#6667, #6894/#6899).** `_handle_task_card_update` (`telegram/manager.py:1374`) dispatches create/update/finalize. Because the kernel's automatic task-card context is turn/request-local, every new BaseAgent tool batch/turn re-issues `create`; `_task_card_create` (`telegram/manager.py:1402`) is therefore **update-first** so the singleton card is edited in place and never flickers: it reads the persisted resident id (`_get_resident_task_card`, `telegram/manager.py:1470`) and, when one exists, edits that resident through Telegram (`update_progress_message`) and returns the **same** compound id — sending nothing new and deleting nothing. A replacement send/delete happens only as fail-open recovery: with no resident it sends and persists the first card (`_set_resident_task_card`, `telegram/manager.py:1478`); if the persisted message genuinely cannot be edited it calls the shared `_recover_task_card_by_replacement` (`telegram/manager.py:1446`), which sends the replacement first, then persists the new id and best-effort deletes the exact stale `account:chat:message` (`_delete_task_card_message`, `telegram/manager.py:1489`). A failed replacement send preserves the old card and its id and deletes nothing; a delete failure is fail-open and never rolls back the new id. `_task_card_update` (`telegram/manager.py:1505`) recovers a deleted active card through the same `_recover_task_card_by_replacement` helper. `_task_card_finalize` (`telegram/manager.py:1520`) freezes the card on its concrete last batch (rows + `✓` markers + final elapsed) with no generic overall `DONE` subject; the legacy scalar form keeps `✅ TASK CARD · DONE`.
- **Task Card render: rows, heartbeat elapsed, fixed footer, one card-level time line.** `_format_task_card_text` (`telegram/manager.py:1546`) renders the batched multi-row form via `_format_rows_task_card_text` (`telegram/manager.py:1589`): one line per tool call (`tool.action`, redacted reasoning, own whole-second elapsed via `_format_elapsed` at `telegram/manager.py:1721`, `✓` when done) with **no** per-row inline timestamp. The card carries a single card-level time line instead (Jason #6894/#6899): `时间 HH:MM:SS UTC±HH` (`_TASK_CARD_TIME_PREFIX` at `telegram/manager.py:76`) rendered as the card's **final standalone line after the footer**, sourced from the first non-empty `started_at` in original row order and omitted entirely when no row carries a usable stamp; its exact text is counted in the reasoning-excerpt budget. Redaction runs on each row before any excerpt/trim, and every row always stays represented (rows are never dropped; only per-row reasoning excerpts shrink). The `_TASK_CARD_TEXT_LIMIT` (3500) budget bounds that reasoning-excerpt shrinkage only, **not** the whole render: fixed per-row scaffolding is unbounded in row count, so a very large operator-set `LINGTAI_TASK_CARD_MAX_TOOL_ROWS` can push the render above the budget (and above Telegram's transport limit) — by design the code neither drops requested rows nor truncates the final string. Both the running and frozen renders carry the fixed `_TASK_CARD_FOOTER` (`telegram/manager.py:66`, "⚠️ Progress only — don't reply to this Task Card."). The kernel owns the batch/timer: BaseAgent's pre-dispatch hook builds one row per call and starts a 0.5s monotonic heartbeat (elapsed floored to whole seconds, so half-second frames read 0s, 0s, 1s, 1s, 2s), captures each tool's local start instant **once** into an immutable `started_at` string (`_capture_task_card_started_at`/`_format_task_card_timestamp` at `base_agent/__init__.py:2432,2526`, separate from the monotonic elapsed clock, so heartbeats never change it and parallel rows keep their own), the result hook freezes the completed row, and the payload the reverse channel carries is the `rows` list (see `base_agent/__init__.py` and `src/lingtai/tools/mcp/ANATOMY.md`).
