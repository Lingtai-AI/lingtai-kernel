---
name: sqlite-log-query
description: >
  Nested system-manual reference for the additive SQLite/`log.sqlite` sidecar
  over LingTai's JSONL runtime traces. Read it when you need
  `lingtai-agent log doctor|query|rebuild`, read-only SQL safety and WAL/rebuild
  caveats, the events/chat_entries/token_entries schema, quick-start snippets,
  SQL recipes (`tool_call_id` lifecycle, tool result stats and percentiles,
  spilled/large tool results), gotchas, or the redaction rules. Runtime trace
  forensics start here; trajectory mining is the sibling `trajectory-mining`.
version: 1.4.0
tags: [lingtai, system-manual, sqlite, log.sqlite, runtime-logs, trace, jsonl, daemon, event-log, pitfalls, observability]
last_changed_at: "2026-08-09T00:00:00Z"
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/trajectory-mining/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/sqlite-log-query/scripts/event_summary.py
maintenance: |
  Tracks the sqlite-log-query topic it documents; update when that integration changes.
---

# SQLite Log Query

LingTai keeps durable runtime traces and token ledgers in JSONL files. The SQLite file at
`logs/log.sqlite` is an **additive, rebuildable query index** over those JSONL
sources of truth. Use it to answer questions that are painful with `grep`: which
event types are hottest, what happened inside daemon runs, what chat-history
turn surrounded a failure, whether notification/daemon/context events are
storming, or how token usage is distributed across main/daemon sources.

## Start here for log.sqlite (quick start)

First-use, copy-pasteable snippets. `log.sqlite` lives at `logs/log.sqlite`
under the agent directory (`AGENT_DIR=/path/to/project/.lingtai/agent-name`).
Open it read-only and run the three first checks:

```bash
sqlite3 -readonly logs/log.sqlite '.tables'
sqlite3 -readonly logs/log.sqlite 'pragma table_info(events);'
sqlite3 -readonly logs/log.sqlite \
  "select type, count(*) as n from events group by type order by n desc limit 20;"
```

Plain `sqlite3 logs/log.sqlite` opens the file read-write; for inspection
prefer `-readonly` (or the Python URI form below) so you never accidentally
write the sidecar. Equivalent read-only Python:

```python
import sqlite3

db_path = "/path/to/.lingtai/<agent>/logs/log.sqlite"
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
for row in conn.execute(
    "select type, count(*) from events group by type order by count(*) desc limit 20;"
):
    print(row)
```

The five recipes in **Query recipes** below (event type counts, per-tool result
length, percentiles, `tool_call_id` lifecycle, large/spilled results) are
directly copy-pasteable. The **Safety contract** and **Gotchas** sections apply
to every query.

## Safety contract

- **JSONL is authoritative.** `logs/log.sqlite` is derived; deleting it should not
  delete facts.
- **Prefer the CLI.** Use `lingtai-agent log ...` instead of opening the DB for
  writes yourself.
- **Queries are read-only with respect to SQLite database contents.** `log query` accepts read-only
  `SELECT`, CTE (`WITH ... SELECT`), and `EXPLAIN` statements and opens the sidecar through the
  kernel read-only inspection path: they never write the main database file (opened `mode=ro` with
  `PRAGMA query_only=ON`), though SQLite read-support `-wal`/`-shm` sidecar files may still be
  created or updated.
- **Rebuild is offline.** `log rebuild` requires the agent working-directory lock;
  if the agent is running, stop/sleep/lull/suspend it first as appropriate.
- **Runtime SQLite is best effort.** New top-level `logs/events.jsonl` and
  standard `logs/token_ledger.jsonl` rows are indexed live after the JSONL write
  succeeds. Chat history, archive, and daemon JSONL sources are indexed into a
  target agent sidecar by explicit offline rebuild so normal turns and daemon
  runs do not pay recursive scan or live-rewrite costs.
- **Live queries are snapshots.** Runtime writes use SQLite WAL mode. For a complete historical
  snapshot, stop the agent and run `log rebuild` before querying.
- **Never paste secrets.** Logs and chat history can contain URLs, tokens,
  prompts, and user data — including raw `fields_json`/`entry_json`. Apply the
  redaction rules below before sharing anything.

## Gotchas

- The event-kind column is **`type`**, not `event_type`.
- The structured event payload lives in **`fields_json`** (chat rows in
  `entry_json`); reach into it with `json_extract(fields_json, '$.key')`.
- `log.sqlite` is **derived and rebuildable**; JSONL remains authoritative.
  A missing sidecar is a rebuildable index gap, never lost facts.
- Open the sidecar **read-only** for inspection: `sqlite3 -readonly`, the
  `file:...?mode=ro` URI, or `lingtai-agent log query`. Never write to it
  directly.
- For exact forensic payloads, follow `source_file` / `source_offset` back to
  the JSONL source.
- `fields_json`/`entry_json` can carry URLs, tokens, prompts, and user data —
  redact before sharing anything.

## Commands

Set a variable for the target agent directory:

```bash
AGENT_DIR=/path/to/project/.lingtai/agent-name
```

Check whether the sidecar exists and is readable:

```bash
lingtai-agent log doctor "$AGENT_DIR"
```

If `doctor` reports `{"status":"missing"...}` or a failing `integrity_check`,
rebuild **only while the target agent is stopped/offline**. `doctor` does not
detect staleness — a stale but intact sidecar still reports `status: ok`;
compare `import_cursors` against source-file mtimes, or just rebuild:

```bash
lingtai-agent log rebuild "$AGENT_DIR"
```

`log rebuild` scans the known JSONL trace surfaces under the target agent:

- `logs/events.jsonl` → `events` (`source_kind='agent_events'`)
- `logs/token_ledger.jsonl` → `token_entries` (`source_kind='agent_token_ledger'`)
- `history/chat_history.jsonl` → `chat_entries` (`source_kind='agent_chat'`)
- `history/chat_history_archive.jsonl` → `chat_entries` (`source_kind='agent_chat_archive'`)
- `daemons/*/logs/events.jsonl` → `events` (`source_kind='daemon_events'`, `run_id=<daemon folder>`)
- `daemons/*/logs/token_ledger.jsonl` → `token_entries` (`source_kind='daemon_token_ledger'`, `run_id=<daemon folder>`)
- `daemons/*/history/chat_history.jsonl` → `chat_entries` (`source_kind='daemon_chat'`, `run_id=<daemon folder>`)

Run a read-only query. The CLI always prints JSON; pipe to `jq .` to
pretty-print when it is available:

```bash
lingtai-agent log query "$AGENT_DIR" \
  'SELECT id, ts, type, agent_address, substr(fields_json, 1, 240) AS fields
   FROM events
   ORDER BY ts DESC
   LIMIT 20' | jq .
```

## Schema quick reference

`events` indexes top-level agent runtime events and daemon run events:

| Column | Meaning |
|---|---|
| `id` | SQLite row id, not a stable cross-rebuild event identifier |
| `ts` | event timestamp as a numeric epoch-like value; ISO strings are parsed when possible |
| `type` | event `type` field, or daemon `event` field |
| `agent_address` | event `address` field when present |
| `agent_name_snapshot` | event `agent_name` field when present |
| `fields_json` | the remaining event fields as JSON text |
| `source_file` | JSONL file imported from |
| `source_offset` | byte offset in the JSONL source; unique with `source_file` |
| `source_line` | 1-based JSONL line number |
| `source_kind` | `agent_events`, `daemon_events`, or fallback kind |
| `scope` | `agent`, `daemon`, or `unknown` |
| `run_id` | daemon run folder name for daemon rows |
| `inserted_at` | sidecar insertion time |

`chat_entries` indexes agent and daemon chat-history JSONL rows:

| Column | Meaning |
|---|---|
| `id` | SQLite row id, not stable across rebuilds |
| `ts` | parsed numeric timestamp when a row has `ts`/`timestamp`, else `0` |
| `ts_text` | original timestamp text/value as stored in JSONL |
| `role` | chat role (`user`, `assistant`, etc.) when present |
| `kind` | LingTai daemon user-entry kind (`task`, `tool_results`, `followup`) when present |
| `turn` | daemon turn number when present |
| `content_text` | best-effort extracted plain text from `text` or content blocks |
| `entry_json` | full source chat row as JSON text |
| `source_file`, `source_offset`, `source_line` | source JSONL identity |
| `source_kind` | `agent_chat`, `agent_chat_archive`, `daemon_chat`, or fallback kind |
| `scope` | `agent`, `daemon`, or `unknown` |
| `run_id` | daemon run folder name for daemon rows |
| `inserted_at` | sidecar insertion time |

`token_entries` indexes agent and daemon token-ledger JSONL rows:

| Column | Meaning |
|---|---|
| `id` | SQLite row id, not stable across rebuilds |
| `ts` | parsed numeric timestamp when possible |
| `ts_text` | original `ts` value from JSONL |
| `input_tokens`, `output_tokens`, `thinking_tokens`, `cached_tokens` | token counters from the JSONL ledger row |
| `model`, `endpoint` | model/provider endpoint metadata when present |
| `source` | ledger source tag such as `main`, `daemon`, `tc_wake`, legacy `soul` (removed producer), or legacy/null |
| `em_id`, `run_id`, `api_call_id` | daemon/run/API attribution when present |
| `entry_json` | full source token-ledger row as JSON text |
| `source_file`, `source_offset`, `source_line` | source JSONL identity |
| `source_kind` | `agent_token_ledger`, `daemon_token_ledger`, or fallback kind |
| `scope` | `agent`, `daemon`, or `unknown` |
| `inserted_at` | sidecar insertion time |

Parent ledgers intentionally include daemon spend rows. If you query both
`agent_token_ledger` and `daemon_token_ledger` rows together, avoid double-counting
daemon calls that were mirrored into the parent ledger and the daemon-local ledger.
Filter by `source_kind`, `source`, `em_id`, or `run_id` according to the report you
need.

Maintenance tables:

- `schema_migrations(version, name, applied_at)` records sidecar schema version.
- `import_cursors(source_file, byte_offset, line_no, updated_at)` records the last
  rebuild/import cursor for each JSONL source.

## Query recipes

Recent events:

```sql
SELECT id, ts, type, source_kind, run_id, substr(fields_json, 1, 300) AS fields
FROM events
ORDER BY ts DESC
LIMIT 50;
```

Event type counts across agent + daemon events:

```sql
SELECT source_kind, type, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts
FROM events
GROUP BY source_kind, type
ORDER BY n DESC
LIMIT 50;
```

Per-tool result length aggregation (`$.result` holds the tool output text):

```sql
SELECT json_extract(fields_json, '$.tool_name') AS tool,
       COUNT(*) AS n,
       CAST(AVG(length(json_extract(fields_json, '$.result'))) AS INT) AS avg_result_len,
       MAX(length(json_extract(fields_json, '$.result'))) AS max_result_len,
       SUM(CASE WHEN length(json_extract(fields_json, '$.result')) > 5000 THEN 1 ELSE 0 END) AS over_5000
FROM events
WHERE type = 'tool_result'
  AND json_extract(fields_json, '$.result') IS NOT NULL
GROUP BY tool
ORDER BY n DESC
LIMIT 20;
```

Nearest-rank percentiles of tool result lengths (SQLite window functions):

```sql
WITH ranked AS (
  SELECT length(json_extract(fields_json, '$.result')) AS result_len,
         ROW_NUMBER() OVER (ORDER BY length(json_extract(fields_json, '$.result'))) AS rn,
         COUNT(*) OVER () AS n
  FROM events
  WHERE type = 'tool_result'
    AND json_extract(fields_json, '$.result') IS NOT NULL
)
SELECT MAX(CASE WHEN rn <= CAST(n * 0.50 + 0.5 AS INTEGER) THEN result_len END) AS p50,
       MAX(CASE WHEN rn <= CAST(n * 0.90 + 0.5 AS INTEGER) THEN result_len END) AS p90,
       MAX(CASE WHEN rn <= CAST(n * 0.95 + 0.5 AS INTEGER) THEN result_len END) AS p95,
       MAX(CASE WHEN rn <= CAST(n * 0.99 + 0.5 AS INTEGER) THEN result_len END) AS p99,
       MAX(result_len) AS max_len
FROM ranked;
```

Trace one `tool_call_id` lifecycle. A full lifecycle typically spans
`tool_call_received` -> `tool_reasoning` -> `tool_call_normalized` ->
`tool_call_approved` -> `tool_call` -> `tool_call_dispatch_start` ->
`tool_call_dispatch_done` -> `tool_result` -> `tool_result_durable_log_visible`
-> `tool_result_model_visible` (daemon runs prefix some of these with
`daemon_` and carry `run_id`):

```sql
SELECT ts, type, source_kind, run_id,
       json_extract(fields_json, '$.tool_name') AS tool,
       substr(fields_json, 1, 160) AS fields
FROM events
WHERE json_extract(fields_json, '$.tool_call_id') = 'call_00_XXXX'
   OR json_extract(fields_json, '$.tool_trace_id') = 'call_00_XXXX'
ORDER BY ts;
```

Find large/spilled tool results. `tool_result_spilled` rows keep the real size
in `$.original_char_count` and point to `$.spill_path`:

```sql
SELECT id, ts, type,
       json_extract(fields_json, '$.tool_name') AS tool,
       COALESCE(json_extract(fields_json, '$.original_char_count'),
                length(json_extract(fields_json, '$.result'))) AS result_len,
       json_extract(fields_json, '$.spill_path') AS spill_path
FROM events
WHERE type IN ('tool_result', 'tool_result_spilled')
  AND json_extract(fields_json, '$.result') IS NOT NULL
ORDER BY result_len DESC
LIMIT 20;
```

Recent chat-history entries:

```sql
SELECT id, source_kind, run_id, role, kind, turn, substr(content_text, 1, 400) AS text
FROM chat_entries
ORDER BY id DESC
LIMIT 50;
```

Join daemon tool events with daemon chat rows by `run_id`:

```sql
SELECT e.run_id, e.ts, e.type, json_extract(e.fields_json, '$.name') AS tool,
       c.role, c.turn, substr(c.content_text, 1, 240) AS chat
FROM events e
LEFT JOIN chat_entries c ON c.run_id = e.run_id AND c.turn = json_extract(e.fields_json, '$.turn')
WHERE e.source_kind = 'daemon_events'
ORDER BY e.ts DESC
LIMIT 100;
```

Search for errors or failures:

```sql
SELECT id, ts, source_kind, run_id, type, substr(fields_json, 1, 500) AS fields
FROM events
WHERE lower(type) LIKE '%error%'
   OR lower(type) LIKE '%fail%'
   OR lower(fields_json) LIKE '%error%'
   OR lower(fields_json) LIKE '%traceback%'
ORDER BY ts DESC
LIMIT 100;
```

Look for notification storms:

```sql
SELECT type, COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts
FROM events
WHERE type LIKE 'notification%'
   OR fields_json LIKE '%notification%'
GROUP BY type
ORDER BY n DESC;
```

Search chat-history text:

```sql
SELECT source_kind, run_id, role, turn, substr(content_text, 1, 500) AS text
FROM chat_entries
WHERE lower(content_text) LIKE '%sqlite%'
ORDER BY id DESC
LIMIT 100;
```

Token usage by ledger source kind:

```sql
SELECT source_kind, source,
       COUNT(*) AS calls,
       SUM(input_tokens) AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(thinking_tokens) AS thinking_tokens,
       SUM(cached_tokens) AS cached_tokens
FROM token_entries
GROUP BY source_kind, source
ORDER BY input_tokens DESC;
```

Main-agent token usage without daemon rows from the parent ledger:

```sql
SELECT COUNT(*) AS calls,
       SUM(input_tokens) AS input_tokens,
       SUM(output_tokens) AS output_tokens,
       SUM(thinking_tokens) AS thinking_tokens,
       SUM(cached_tokens) AS cached_tokens
FROM token_entries
WHERE source_kind = 'agent_token_ledger'
  AND COALESCE(source, '') != 'daemon'
  AND em_id IS NULL
  AND run_id IS NULL;
```

Inspect one event's full JSON payload:

```sql
SELECT id, type, fields_json
FROM events
WHERE id = 123;
```

Use SQLite JSON functions when available:

```sql
SELECT
  type,
  json_extract(fields_json, '$.tool') AS tool,
  json_extract(fields_json, '$.error') AS error
FROM events
WHERE type LIKE 'tool_%'
ORDER BY ts DESC
LIMIT 50;
```

If JSON functions are unavailable in the local SQLite build, fall back to
`fields_json LIKE ...` and inspect the returned JSON text.

## Source discovery

Before trajectory mining, discover what data exists in the sidecar. The
sidecar replaces the old `find`-based JSONL scanning with SQL:

```sql
-- Schema discovery: what keys appear in fields_json?
SELECT json_each.key, COUNT(*) AS n
FROM events, json_each(events.fields_json)
GROUP BY json_each.key
ORDER BY n DESC
LIMIT 30;
```

```sql
-- What source families are present, and over what span?
SELECT scope, source_kind, source_file, COUNT(*) AS n,
       MIN(ts) AS earliest, MAX(ts) AS latest
FROM events
GROUP BY scope, source_kind, source_file
ORDER BY n DESC;
```

The `source_kind` values and their JSONL origins are listed under `log rebuild`
above and in the three schema tables.

## Workflow: investigate a suspected runtime problem

1. Identify the agent directory. If unsure, use the `.lingtai/<agent>` directory
   shown in the agent's identity/pad or ask the orchestrator.
2. Stop the target agent if exact complete history matters, then run
   `lingtai-agent log rebuild "$AGENT_DIR"`. Otherwise begin with `doctor` and
   live event queries.
3. Start broad: event/source-kind counts and recent rows.
4. Narrow by time/type/text. Include `source_kind` and `run_id` in queries when
   daemon evidence matters.
5. Cross-check surprising findings against source JSONL (`logs/events.jsonl`,
   `history/chat_history*.jsonl`, daemon subdirectories) before filing bugs or
   making claims.
6. When reporting, quote minimal evidence and apply the redaction rules below.

---

## Trajectory mining

Systematic mining of these traces into validated improvement candidates —
manifest policy, cheap-model/daemon strategy, prompt templates, the finding
schema and confidence rubric, the digest template, output routing, periodic
mode, and the 10-step on-demand procedure — is owned by the sibling reference
`../trajectory-mining/SKILL.md`. The queries below are its mechanical first
pass; the redaction rules further down apply to every excerpt it feeds an LLM.

## Metrics and slicing recipes

Run cheap aggregations before any LLM call. These are free signal. Start with the
event-type and source-kind counts from **Query recipes** above, then add:

**Tool call / result summary:**

```sql
SELECT
  json_extract(fields_json, '$.tool') AS tool,
  json_extract(fields_json, '$.name') AS name,
  type,
  COUNT(*) AS n
FROM events
WHERE type LIKE 'tool_%'
GROUP BY tool, name, type
ORDER BY n DESC
LIMIT 20;
```

**Tool error clusters:**

```sql
SELECT
  json_extract(fields_json, '$.error') AS error,
  COUNT(*) AS n
FROM events
WHERE fields_json LIKE '%error%'
  AND type LIKE 'tool_%'
GROUP BY error
ORDER BY n DESC
LIMIT 20;
```

**Latency gaps (> 30s between events):**

```sql
WITH ordered AS (
  SELECT
    ts,
    type,
    ts - LAG(ts) OVER (ORDER BY ts) AS gap_seconds
  FROM events
  WHERE ts > 0
)
SELECT ts, type, ROUND(gap_seconds, 1) AS gap_seconds
FROM ordered
WHERE gap_seconds > 30
ORDER BY gap_seconds DESC
LIMIT 30;
```

**Context pressure / legacy stamina traces:**

```sql
SELECT id, ts, type, substr(fields_json, 1, 400) AS fields
FROM events
WHERE type LIKE '%context%'
   OR type LIKE '%pressure%'
   OR type LIKE '%molt%'
   OR type LIKE '%spill%'
   OR type LIKE '%overflow%'
   OR type LIKE '%stamina%'  -- legacy logs only
ORDER BY ts DESC
LIMIT 50;
```

**Daemon lifecycle:**

```sql
SELECT run_id, type, COUNT(*) AS n,
       MIN(ts) AS first_ts, MAX(ts) AS last_ts
FROM events
WHERE source_kind = 'daemon_events'
GROUP BY run_id, type
ORDER BY run_id, n DESC;
```

**Auth / env failures:**

```sql
SELECT id, ts, type, substr(fields_json, 1, 400) AS fields
FROM events
WHERE lower(fields_json) LIKE '%auth%'
   OR lower(fields_json) LIKE '%token%'
   OR lower(fields_json) LIKE '%credential%'
   OR lower(fields_json) LIKE '%unauthorized%'
   OR lower(fields_json) LIKE '%forbidden%'
ORDER BY ts DESC
LIMIT 30;
```

## Chunking and slicing

Never dump large private event logs into an LLM. Use these SQL slicing
strategies:

**Time-window slicing:**

```sql
SELECT id, ts, type, source_kind, substr(fields_json, 1, 300) AS fields
FROM events
WHERE ts BETWEEN :start_ts AND :end_ts
ORDER BY ts;
```

**Event-family slicing:**

```sql
SELECT id, ts, type, source_kind, substr(fields_json, 1, 300) AS fields
FROM events
WHERE type IN ('tool_call', 'tool_result', 'error', 'timeout')
ORDER BY ts;
```

**Anomaly-window excerpts (±30 rows around a suspicious event):**

```sql
WITH ranked AS (SELECT id, ROW_NUMBER() OVER (ORDER BY ts) AS rn FROM events)
SELECT e.*
FROM events e
JOIN ranked r ON r.id = e.id
WHERE r.rn BETWEEN (SELECT rn FROM ranked WHERE id = :suspicious_id) - 30
             AND (SELECT rn FROM ranked WHERE id = :suspicious_id) + 30
ORDER BY e.ts;
```

**Deduplication / signature hashing:**

```sql
SELECT
  substr(type || '|' || json_extract(fields_json, '$.tool') || '|'
         || json_extract(fields_json, '$.error'), 1, 120) AS sig,
  COUNT(*) AS n,
  MIN(ts) AS first_ts,
  MAX(ts) AS last_ts
FROM events
GROUP BY sig
ORDER BY n DESC
LIMIT 30;
```

---

## Redaction and privacy rules

Apply these in order, before any LLM call:

1. **Redact tokens and credentials**: replace any value matching
   `(token|key|secret|password|credential|oauth)[":=\s]+[^\s",]{8,}` with
   `[REDACTED]`.
2. **Redact message bodies**: if an event field contains human-written message
   text, summarize rather than quote unless exact wording is necessary for the
   finding.
3. **Redact file paths containing usernames**: replace `/Users/<name>/` with
   `/Users/[USER]/`.
4. **Redact IP addresses and internal hostnames**: replace with `[HOST]`.
5. **Quote minimum evidence**: cite event type, timestamp/line range, and
   redacted field names. Do not dump entire event objects.
6. **No side effects without approval**: the output of trajectory mining is a
   recommendation digest. Do not create files, issues, commits, PRs, scheduled
   jobs, or agent refreshes.

## Pitfalls

Beyond the safety contract above:

- Do not treat `log.sqlite` as a coordination database. It is an observability
  index, not agent state.
- Do not rebuild a live agent by bypassing the CLI lock; that risks racing the
  runtime logger.
- Do not assume `id` survives rebuilds. Use `source_file/source_offset`, time,
  `run_id`, and surrounding context for durable references.
- If a query returns fewer rows than expected on a live agent, that is the WAL
  snapshot caveat in the safety contract — stop/rebuild or inspect JSONL.

## Scripts

### event_summary.py

A standalone Python script that summarizes a LingTai `log.sqlite` file. It reads
database contents without modifying them, makes no network requests, and requires
no secrets. SQLite may create or update read-support `-wal`/`-shm` sidecars.

```bash
python3 scripts/event_summary.py "$AGENT_DIR/logs/log.sqlite"
python3 scripts/event_summary.py "$AGENT_DIR/logs/log.sqlite" --source-kind daemon_events
```

Also accepts `--hours N` and `--format json`. It runs the mechanical first-pass
queries above — event type counts, tool call summaries, error clusters, latency
gap analysis, source kind breakdown, time range, and schema key discovery — all
via read-only SQL.
