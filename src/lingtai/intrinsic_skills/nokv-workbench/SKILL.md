---
last_changed_at: 2026-07-19T00:00:00Z
name: nokv-workbench
description: >
  Thin routing manual for NoKV-controlled workbenches. Use when an agent is
  asked to persist task inputs, scripts, outputs, logs, provenance, or run
  manifests through the `workbench_*` MCP tools instead of ordinary local
  file writes. Covers MCP registration, directory layout, append and edit
  discipline, conditional reads, cross-workbench queries, commit discipline,
  leased checkpoint snapshots with naming, renewal, discovery, point-in-time
  reads, and safe restore-to-fork recovery.
version: 0.4.0
tags: [nokv, mcp, workbench, artifacts, provenance, snapshots, checkpoints, leases, restore]
related_files:
- src/lingtai/intrinsic_skills/nokv-workbench/assets/PREFLIGHT.md
- src/lingtai/intrinsic_skills/nokv-workbench/assets/init-snippet.json
- src/lingtai/intrinsic_skills/nokv-workbench/assets/mcp_registry.example.jsonl
maintenance: |
  Tracks the tool/capability behavior it teaches; update when that tool's behavior changes.
---

# NoKV Workbench

Use this skill when a task must write durable artifacts through NoKV rather
than the local LingTai workdir. The authoritative control surface is the NoKV
MCP server started in workbench profile. This skill is only the operating
manual.

## MCP registration

Add one line to the per-agent `mcp_registry.jsonl` (copy
`assets/mcp_registry.example.jsonl`):

```json
{"name":"nokv-workbench","summary":"NoKV-controlled workbench artifact namespace.","transport":"stdio","command":"/path/to/nokv","args":["--server-bind","127.0.0.1:7777","--object-backend","rustfs","--s3-bucket","nokv-lingtai-workbench","mcp","--profile","workbench","--workbench-root","/agents/{agent_id}/wb"],"source":"local-nokv"}
```

Activate the same `command` and `args` from `init.json` under
`mcp.nokv-workbench` with `"type": "stdio"` — copy `assets/init-snippet.json`.
Where the registry file lives, how to edit it, and how `system(action="refresh")`
applies the change belong to `mcp-manual`; only the NoKV-specific arguments
are documented here.

Keep the global NoKV flags before `mcp`, and set them to the same metadata
server and object-store bucket used by the running NoKV service. If your
deployment uses a non-default S3/RustFS endpoint or credentials, add the
matching `--s3-*` flags here as well. Developer deployment steps live in
`assets/PREFLIGHT.md`, not here.

### Per-agent root (tenant isolation)

Each agent gets its own workbench root so agents cannot see or clobber each
other's workbenches on a shared NoKV server. Use the `{agent_id}` placeholder
in `--workbench-root`; LingTai expands it at MCP launch to the agent's stable
address (its `.lingtai/<agent>` directory name):

```
--workbench-root /agents/{agent_id}/wb   ->   /agents/scout/wb   (for agent "scout")
```

`{agent_address}` is an alias for the same value, and `{agent_dir}` expands to
the agent's absolute working directory. The same registry line therefore works
verbatim for every agent — no per-agent editing. This is path-scoped isolation
enforced by the NoKV MCP (an agent's tools can only address paths under its own
root); it is not a server-side ACL, which matches LingTai's local trust model.
Record the resolved owner in each run manifest (for example, `"owner": "scout"`
for agent `scout`) so provenance is explicit. Do not expect placeholders to expand
inside committed run manifests; the committed `run_manifest.json` also embeds the
full `workbench_path`, which already contains the owning agent id.

The MCP tools are intentionally prefixed with `workbench_` so they do not replace
LingTai's local `read`, `write`, `edit`, `grep`, or `glob` tools.

## Layout

Each workbench id maps to `<workbench-root>/<id>/` (with the per-agent root
above, `/agents/<agent_id>/wb/<id>/`) with these sections:

| Section | Contents |
|---|---|
| `input` | task event payloads, dataset references, parameters |
| `scripts` | analysis code, notebooks, reproducibility scripts |
| `outputs` | plots, CSVs, derived datasets, reports |
| `logs` | agent-facing trace excerpts and tool-call evidence |
| `metadata` | provenance, run manifests, audit references |

Do not write LingTai runtime state here. `.agent.lock`, heartbeat files,
mailbox, `.notification/`, `.mcp_inbox/`, and `logs/events.jsonl` stay in the
local LingTai workdir.

## Workflow

1. Create the workbench with `workbench_create`:

```json
{"id":"spedas-task-001"}
```

2. Write inputs, scripts, outputs, and evidence with
   `workbench_put_file`. Pass `replace=true` only when intentionally
   replacing a prior artifact. When `section` is set, `path` is relative to
   that section: use `section="outputs", path="spectrum.csv"`, not
   `path="outputs/spectrum.csv"`.

3. Grow logs and journals with `workbench_append` — one file per stream,
   appended record by record:

```json
{"id":"spedas-task-001","section":"logs","path":"tool_calls.jsonl","text":"{\"tool\":\"bash\",\"status\":\"ok\"}\n"}
```

   Append creates the file if it does not exist. Concurrent appends to the
   same file are retried automatically with backoff; under sustained
   contention a conflict error can still surface — retry the append once
   before treating it as a coordination bug. Do not re-upload a growing file
   with `put_file replace=true`, and do not hand-number segment files — both
   are obsolete workarounds.

4. Make small in-place changes with `workbench_edit` (same semantics as the
   local `edit` tool: `old_string` must match exactly once unless
   `replace_all=true`; the error texts match too). Reserve
   `put_file replace=true` for whole-file replacement.

5. Commit only after required outputs are present:

```json
{
  "id": "spedas-task-001",
  "manifest": {
    "task": "spedas-task-001",
    "inputs": ["input/event.json", "input/dataset-ref.json"],
    "scripts": ["scripts/analysis.py"],
    "outputs": ["outputs/plot_001.png", "outputs/spectrum.csv"],
    "logs": ["logs/tool_calls.jsonl"],
    "provenance": "metadata/provenance.json"
  }
}
```

`workbench_commit` publishes `metadata/run_manifest.json`. In v0 this file
is the completion marker. A workbench without it is not complete.

6. Snapshot the committed workbench with `workbench_snapshot`, choosing a
   `ttl_days` that outlasts how long the handoff must stay restorable — see
   "Checkpoints and leases" for naming, lease, renewal, and citation rules.

## Checkpoints and leases

A `workbench_snapshot` is a **leased checkpoint**, not a permanent archive.
Every snapshot carries a lease; once the lease expires, NoKV's background
collector reaps the checkpoint and its point-in-time view is gone. This is the
failure mode that strands handoffs — a `snapshot_id` cited in a note, then
unreachable a couple of days later because nobody extended it.

**The default lease is 7 days, but only when you mint through this tool.** Pass
`ttl_days` to `workbench_snapshot` to choose the lease (default 7, maximum 90),
along with a `name`:

```json
{"id":"spedas-task-001","name":"final-v1","ttl_days":30}
```

The response adds `name`, `lease_expires_at` (the reap deadline), and an
`expiry_warning` when the lease is short, alongside the usual `snapshot_id` and
`read_version`. **A snapshot minted with the bare NoKV CLI outside this tool
gets only the 1-hour default lease** — never hand a raw-CLI snapshot to a
handoff.

**Name your checkpoints.** `name` is a workbench-scoped alias you can renew,
list, read, and restore by instead of memorizing an opaque `snapshot_id`. The
alias remains useful only while the checkpoint lease is live; it is not an
archive or an independent durability guarantee.

In final reports or handoff notes, cite the checkpoint `name`, its
`snapshot_id`, and the returned `lease_expires_at` so a later reader knows both
what to restore and when it reaps.

### Renew before a handoff outlives the lease

If a checkpoint must survive longer than its lease — a cross-session handoff, a
note someone opens next week — call `workbench_snapshot_renew` **before** you
commit or hand off, with the same argument shape as `workbench_snapshot` above.
Renew is **extend-only**: it pushes `lease_expires_at` further out and never
pulls it in, so a `ttl_days` that would shorten the lease is ignored. Address
the checkpoint by `name` or `snapshot_id`.

### Discover checkpoints with workbench_snapshot_list

Do not rely on a `snapshot_id` you remembered in a note — that note is exactly
what goes stale. `workbench_snapshot_list {"id":"spedas-task-001"}` enumerates
every checkpoint of the workbench with its `name`, `snapshot_id`,
`lease_expires_at`, and lifecycle `state` (`alive`, `expired`, or `reaped`).
Use it to see what is still restorable before you try to read history.

### Read history with at_snapshot

`workbench_stat`, `workbench_list`, and `workbench_read` accept an optional
`at_snapshot` (a `name` or `snapshot_id`) to view the workbench **as it was at
that checkpoint** instead of its current state:

```json
{"id":"spedas-task-001","section":"outputs","path":"spectrum.csv","at_snapshot":"final-v1"}
```

Historical `workbench_read` serves the checkpoint's bytes/text-lines. Reading a
checkpoint whose lease has expired fails **loudly**. Expiry is terminal:
expired checkpoints cannot be renewed, even if the background collector has not
yet removed the pin. Re-mint from the current committed state when that is still
useful; NoKV never revives a checkpoint whose historical records may already
have been reclaimed.

## Restore to a new workbench

Use `workbench_restore` when you need to continue from a live checkpoint. The
safe default is **restore-to-fork**: it creates a new workbench and never
overwrites the source workbench.

```json
{
  "id": "spedas-task-001",
  "at_snapshot": "final-v1",
  "destination_id": "spedas-task-001-restored"
}
```

The destination must not already contain unrelated work. A successful response
identifies the source checkpoint, destination path, and deterministic restore
operation. Repeating the same source, checkpoint, and destination is idempotent:
it returns the existing restored workbench instead of creating a second fork.
Using the same destination with a different checkpoint is a conflict.

The restored workbench contains the files visible at the checkpoint, is
independently writable, and writes `metadata/restore_manifest.json`. Its
`restored_from` object binds `workbench_id`, absolute source `path`, and
`snapshot_id`; the manifest also records the deterministic `operation_id`. The
destination does not inherit the source's checkpoint aliases. After success,
read this manifest and the required outputs before handing work to another
agent. The source workbench remains at its current state throughout the
restore.

Do not request an in-place rollback from normal agent workflows. The Workbench
contract exposed to agents supports restore-to-fork only; operator recovery
APIs are outside this skill.

### Handle structured checkpoint errors

Checkpoint and restore failures use a structured MCP error with `code`,
`message`, `retryable`, and `details`. Branch on `code`; do not parse the human
message:

| Code | Agent action |
|---|---|
| `SnapshotLeaseExpired` | Do not retry or attempt revival. Mint a new checkpoint from current state when appropriate. |
| `SnapshotRootMismatch` | Stop. The checkpoint belongs to another workbench root; never substitute or copy its id. |
| `SnapshotBindingChanged` | Refresh the MCP/workbench resolution and retry once because the root binding changed during the operation. |
| `SnapshotRenewContended` | Retry renewal with bounded backoff; the lease in the error response is not authoritative. |
| `RestoreInProgress` | Retry the exact restore-to-fork request with bounded backoff; the same operation is still preparing or cleaning staged state. |
| `RestoreDestinationConflict` | Choose a new empty `destination_id`, unless this was an exact retry of the same restore operation. |
| `RestoreResourceLimit` | Do not retry unchanged. Reduce the restore subtree or metadata payload reported in `details`, then create a new checkpoint and destination if needed. |

Honor the returned `retryable` flag when it is stricter than this table. A
failed renew never changes the locally recorded expiry; use the authoritative
pin returned by a successful renew or refresh with `workbench_snapshot_list`.

### Expired is not the same as data lost

An expired checkpoint loses only the **point-in-time view**, not your current
work. The
workbench's committed files stay put: after `workbench_commit`, the current
artifacts remain reachable with `workbench_read`, `workbench_list`, and
`workbench_stat` (no `at_snapshot`). Only the frozen historical view needs a
live lease. If a checkpoint has reaped, re-mint a fresh one from the current
committed state — the artifacts themselves are intact.

## Concurrency

For the MVP, use a parent-created workbench and child-filled files:

- The parent agent creates the workbench, assigns section-relative paths,
  validates outputs, commits, and snapshots.
- Child or daemon agents only write the paths assigned by the parent. They do
  not create, commit, snapshot, or write `metadata/run_manifest.json`.
- Multiple writers may `workbench_append` to the same log file — appends are
  serialized by the server with automatic retry. Everything else stays
  disjoint: assign prefixes such as `outputs/agent-a/` and never let two
  agents `put_file` or `edit` the same path. Same-path `put_file` with
  `replace=false` intentionally fails with an exists conflict; treat that as
  a coordination bug, not a reason to bypass with `replace=true`.

## Read and search

Reading inside one workbench:

- `workbench_read` pages structured records (JSON, text lines) with
  `cursor`/`offset`/`limit` (limit up to 300 records). For polling or
  re-checking a file you already read, pass
  `if_none_match: <generation from the previous response>` — an unchanged
  file returns a tiny `not_modified` response instead of the full body.
  Exception: right after a context molt, read without `if_none_match`; you
  need the content back, not a not-modified marker.
- Files larger than the structured limit: use `format="bytes"` with
  `offset`/`limit` ranges (bytes come back base64-encoded), or
  `workbench_grep` to locate lines first.
- Record shape depends on content type: `.json` files parse into JSON
  records; `.jsonl`, `.log`, and other text files come back as `text_lines`
  records whose `value.text` holds the raw line — parse it yourself when the
  line is JSON.
- `workbench_grep` searches file bodies for case-insensitive **literal**
  substrings (not regex). Pass several alternatives at once with
  `patterns: ["营养", "食谱", "recipe"]` (OR semantics); a single `pattern`
  containing `|` is split into alternatives automatically. At most 16
  alternatives per call — batch synonyms accordingly. Filter files with
  `glob` (basename match, `*` and `?`, CJK-safe, e.g. `"*.md"`). `limit`
  accepts up to 300 matches. Narrow with `section`/`glob` first; huge result
  sets get compacted out of your context later.
- To read a past state instead of the current one, pass `at_snapshot` — see
  "Checkpoints and leases".

Searching across workbenches:

- `workbench_find` answers "which workbenches are committed / mention X in
  the manifest". It returns compact committed-state and manifest summaries;
  pass `include_manifest=true` only when the full
  `metadata/run_manifest.json` envelope is needed.
- `workbench_search` answers path- and metadata-level queries: predicates
  over the built-in fields with sort, projection, and facets. Omit `id` to
  search every workbench under your root; matches come back with
  `workbench_id`, `section`, and `relative_path`. Built-in queryable fields
  (no index registration needed): `path`, `name`, `kind`, `size_bytes`,
  `body.content_type`, `body.producer`, `body.manifest_id`. A single
  `workbench_search` with `facets: ["body.content_type"]` replaces a
  list-then-stat sweep. **It matches paths and metadata, not file contents —
  content search stays with `workbench_grep`.**
- `workbench_aggregate` computes count/sum/avg/min/max with `group_by` over
  the same fields. `workbench_catalog` lists the queryable fields; until
  custom indexes exist it always returns the built-in set above, so calling
  it is rarely necessary.

`workbench_search`, `workbench_grep`, `workbench_list`, `workbench_stat`,
and `workbench_read` return flat `section`, `relative_path`, and `path`
fields so follow-up calls can reuse `section` and `relative_path` directly
(`workbench_find` returns manifest summaries and `workbench_aggregate`
returns grouped values — neither carries per-file path fields). Use
LingTai's local `grep` for local workdir text and NoKV tools for workbench
artifacts.

## Commit checklist

Before calling `workbench_commit`, verify:

- `input/` has the task event and dataset references.
- `scripts/` has code or notebooks needed to reproduce the result.
- `outputs/` has the requested deliverables.
- `metadata/provenance.json` exists when provenance is required.
- `logs/` contains the evidence streams (appended files are fine).
- The manifest lists relative paths inside the workbench sections.
