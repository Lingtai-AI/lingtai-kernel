---
related_files:
  - src/lingtai/cli.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/kernel/event_journal/ANATOMY.md
  - src/lingtai/kernel/mail_transport/ANATOMY.md
  - src/lingtai/services/ANATOMY.md
  - src/lingtai/kernel/ANATOMY.md
  - src/lingtai/kernel/services/__init__.py
  - src/lingtai/kernel/services/logging.py
  - src/lingtai/kernel/services/mail.py
  - tests/test_services_logging.py
  - tests/test_services_mail.py
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# services

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues/mail/PR proposals; do not silently fix.

Kernel-side service helpers and implementations. Services back cross-cutting kernel concerns without making intrinsics depend directly on one transport: the mailbox id generator for peer messaging, structured event logging, and token-ledger indexing with JSONL files as sources of truth plus a rebuildable SQLite query sidecar. Mail transport itself is now a Ports & Adapters boundary: the Port lives in `../mail_transport/ANATOMY.md` and the concrete filesystem mechanism lives outside Core in `../../adapters/posix/ANATOMY.md`.

## Components

- `services/mail.py` — the mailbox id generator only.
  - `_new_mailbox_id()` builds the sortable `<YYYYMMDDTHHMMSS>-<4 hex>` mailbox id (`services/mail.py:29-44`). It is Core-neutral (no filesystem access, no adapter import) so the email tool (`lingtai/tools/email/primitives.py`) and the POSIX mail adapter both import it from here.
  - The former `MailService` ABC and `FilesystemMailService` class were removed from this module: the ABC is superseded by the Core-owned `MailTransportPort` (`../mail_transport/__init__.py`) and the concrete class is re-homed as `PosixFilesystemMailAdapter` (`../../adapters/posix/mail.py`). See `../mail_transport/CONTRACT.md`.
- `services/logging.py` — structured event log, token-ledger mirror, and additive SQLite trace query index. Agent event rows are stamped upstream with compact kernel runtime identity fields (`kernel_version`, `kernel_runtime_stamp`, `kernel_runtime`) before durable JSONL/SQLite persistence.
  - `LoggingService` is the ABC for `log(event)` and `close()`; `log()` may return optional storage metadata such as JSONL offsets (`services/logging.py:77`, `services/logging.py:84-90`).
  - `JSONLLoggingService` appends UTF-8 JSON lines with a lock and flush per write, returning `(source_file, source_offset)` metadata (`services/logging.py:96`, `services/logging.py:105-112`, `services/logging.py:120-131`).
  - `SQLiteEventIndex` owns the derived `logs/log.sqlite` schema, fail-open runtime event/token writes, v1→v3 migrations, and read-only inspection opens (`services/logging.py:175`, `services/logging.py:334-412`, `services/logging.py:552-625`).
  - `CompositeLoggingService` redacts the event via `trace_redaction.redact_for_trajectory()` before any durable write, writes the top-level `logs/events.jsonl` primary first, then best-effort inserts that same redacted event into SQLite with the JSONL source offset (`services/logging.py:679`, `services/logging.py:686-696`).
  - `rebuild_sqlite_event_index()`, `doctor_sqlite_event_index()`, and `query_sqlite_event_index()` back the CLI rebuild/doctor/query commands; rebuild scans agent events, token ledger, chat history/archive, and daemon event/chat/token-ledger JSONL sources into one sidecar (`services/logging.py:915`, `services/logging.py:1044`, `services/logging.py:1056`). Rebuild receives a **required** Core-owned `WorkdirLeasePort` (composed and injected by the CLI), acquires it non-blocking (`acquire(0)`) so it fails immediately when the agent is live, and covers directory creation, temporary rebuild state, replacement, cleanup, and lease release with the outer post-acquire `try/finally` (`services/logging.py:938-1041`). It never constructs a lock or imports an adapter.
- `services/__init__.py` is an empty package marker; callers import concrete modules directly.

## Connections

- `PosixJsonlEventJournalAdapter` composes `JSONLLoggingService`, `SQLiteEventIndex`, and `CompositeLoggingService` for the Core-owned journal Port (`src/lingtai/adapters/posix/event_journal.py:15-48`). `BaseAgent` no longer imports or constructs these concrete services.
- `BaseAgent` receives a `MailTransportPort | None` (`mail_service`) constructor argument; a missing transport disables the email intrinsic. It calls only Port methods — `listen` at start and `stop` at teardown (`base_agent/lifecycle.py:225`, `base_agent/lifecycle.py:277`). The Port and its promises live in `../mail_transport/ANATOMY.md` and `../mail_transport/CONTRACT.md`; the concrete `PosixFilesystemMailAdapter` lives in `../../adapters/posix/ANATOMY.md`.
- `services/mail.py` no longer imports `handshake` or defines any transport; it owns only `_new_mailbox_id` (`services/mail.py:29-44`). The POSIX mail adapter imports `_new_mailbox_id` from here and imports `handshake.{is_agent,is_alive,resolve_address}` itself; `lingtai/tools/email/primitives.py` also imports `_new_mailbox_id` from here.

## Composition

- **Parent:** `src/lingtai/kernel/` (see `ANATOMY.md`).
- **Subfolders:** none.
- **Sibling consumers:** the `email` tool (`lingtai/tools/email/`) owns mailbox tool behavior; the outside POSIX adapter owns event-journal storage lifecycle and is injected into `base_agent/` through the Core Port; `src/lingtai/cli.py` exposes `lingtai-agent log {rebuild,doctor,query}` (`../lingtai/cli.py:294-305`).

## State

- **Persistent mail:** owned by the POSIX mail adapter, not this package. `<workdir>/mailbox/{inbox,outbox,sent}/<uuid>/message.json` plus optional `attachments/` are written atomically by `PosixFilesystemMailAdapter.send()` (`../../adapters/posix/mail.py:84-162`); this module contributes only the id (`_new_mailbox_id`).
- **Persistent log source-of-truth:** `<workdir>/logs/events.jsonl`; one JSON object per line, appended by `JSONLLoggingService.log()` after the composite service has redacted high-confidence secrets (`services/logging.py:131-150`, `services/logging.py:686-696`). Agent-originated rows carry `kernel_version`, `kernel_runtime_stamp`, and `kernel_runtime` so the latest event identifies the running kernel/runtime identity. Chat-history, token-ledger, and daemon traces remain authoritative in their own JSONL files (`history/chat_history*.jsonl`, `logs/token_ledger.jsonl`, `daemons/*/{logs/events.jsonl,logs/token_ledger.jsonl,history/chat_history.jsonl}`); live chat history is redacted by `BaseAgent._save_chat_history()` before `history/chat_history.jsonl` is written.
- **Persistent log sidecar:** `<workdir>/logs/log.sqlite`; rebuildable/deletable SQLite trace index with `schema_migrations`, `import_cursors`, `events`, `chat_entries`, and `token_entries` tables. `events`, `chat_entries`, and `token_entries` keep `source_file/source_offset/source_line` provenance so JSONL replays are idempotent and traceable; `token_entries` additionally records token counters, model/endpoint, source/em/run/api ids, and `source_kind`/`scope` to avoid parent/daemon double-counting ambiguity (`services/logging.py:334-412`, `services/logging.py:519-548`).
- **Ephemeral mail:** owned by the POSIX mail adapter. `_seen` (in-memory delivered-UUID set) and the daemon `_poll_thread` live in `PosixFilesystemMailAdapter` (`../../adapters/posix/mail.py:67-69`); this module owns no mail runtime state.
- **Ephemeral log:** when composed by the outside POSIX journal adapter, `JSONLLoggingService` holds an open file handle and a thread lock; `SQLiteEventIndex` holds an optional sqlite connection and disables itself after sqlite errors so agent turns fail open (`services/logging.py:111-125`, `services/logging.py:175-212`).

## Notes

- Pseudo-agent outbox claiming (optimistic concurrency, claim/rollback) now lives in the POSIX mail adapter and is characterized there; see `../../adapters/posix/ANATOMY.md` and `../mail_transport/CONTRACT.md`.
- After the mail Ports & Adapters split, this package no longer owns a mail transport ABC: mail is a Core Port (`../mail_transport/`) with an outside POSIX adapter, while logging still composes the JSONL primary with optional derived indexes behind the event-journal Port.
- `get_events()` favors simplicity over hot-path performance: it re-opens and parses the whole JSONL file each call (`services/logging.py:154-169`).
- SQLite is intentionally additive: JSONL remains the durable source of truth; rebuild requires the agent working-directory lease (offline/stopped agent) through the injected `WorkdirLeasePort`, uses a temporary database and atomic replace, checkpoints WAL before replacing, and releases the lease in the outer post-acquire `finally` (`services/logging.py:915-1041`).
- Runtime writes index the top-level `logs/events.jsonl` stream and standard `logs/token_ledger.jsonl` appends; token-ledger SQLite mirroring happens after the JSONL append and fails open (`token_ledger.py:50`, `token_ledger.py:105`). Chat history, archive, and daemon JSONL sources are indexed during explicit rebuild, avoiding extra work on every chat-history rewrite and giving daemon-local token ledgers provenance rows only when requested (`services/logging.py:784-907`).
- Query helpers accept read-only `SELECT`/CTE/`EXPLAIN` statements and open with SQLite `mode=ro` plus `PRAGMA query_only=ON`, so CLI inspection never writes database content and a long-lived reader still sees later writer commits; on a live store SQLite may materialize persistent `-wal`/`-shm` read sidecars as a side effect, which a read-only connection cannot remove on close. A schema probe runs before the reader is accepted. `immutable=1` is an escape hatch for quiescent offline storage only — archived logs, a read-only mount — because it skips the WAL, so a stale read is indistinguishable from a successful one. It is reached only when SQLite refused for the read-only-sidecar reason and the *resolved* database has no `-wal` and sits in storage that currently denies ordinary writers, re-checked after the retry opens; anything else keeps the original failure visible. That narrows but does not eliminate the window, and it is not a guarantee against a concurrent writer: root, ACLs, or a remount can still change storage that looks read-only (`services/logging.py:209-312`, `services/logging.py:663-677`).
