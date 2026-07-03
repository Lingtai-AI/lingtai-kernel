---
last_changed_at: 2026-07-03T01:26:14-07:00
name: nokv-workbench
description: >
  Thin routing manual for NoKV-controlled workbenches. Use when an agent is
  asked to persist task inputs, scripts, outputs, logs, provenance, or run
  manifests through the `workbench_*` MCP tools instead of ordinary local
  file writes. Covers MCP registration, directory layout, append and edit
  discipline, conditional reads, cross-workbench queries, commit discipline,
  and snapshot references.
version: 0.2.0
tags: [nokv, mcp, workbench, artifacts, provenance, snapshots]
---

# NoKV Workbench

Use this skill when a task must write durable artifacts through NoKV rather
than the local LingTai workdir. The authoritative control surface is the NoKV
MCP server started in workbench profile. This skill is only the operating
manual.

## MCP registration

Register the MCP with a per-agent `mcp_registry.jsonl` line like:

```json
{"name":"nokv-workbench","summary":"NoKV-controlled workbench artifact namespace.","transport":"stdio","command":"/path/to/nokv","args":["--server-bind","127.0.0.1:7777","--object-backend","rustfs","--s3-bucket","nokv-lingtai-workbench","mcp","--profile","workbench","--workbench-root","/agents/{agent_id}/wb"],"source":"local-nokv"}
```

Activate it from `init.json`:

```json
{
  "mcp": {
    "nokv-workbench": {
      "type": "stdio",
      "command": "/path/to/nokv",
      "args": ["--server-bind", "127.0.0.1:7777", "--object-backend", "rustfs", "--s3-bucket", "nokv-lingtai-workbench", "mcp", "--profile", "workbench", "--workbench-root", "/agents/{agent_id}/wb"]
    }
  }
}
```

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

```text
input/
scripts/
outputs/
logs/
metadata/
```

Use the sections consistently:

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

1. Create the workbench:

```json
{"id":"spedas-task-001"}
```

with `workbench_create`.

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

6. Snapshot the committed workbench with `workbench_snapshot` and cite the
returned `snapshot_id` and `read_version` in final reports or handoff notes.

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
  list-then-stat sweep.
- `workbench_aggregate` computes count/sum/avg/min/max with `group_by` over
  the same fields. `workbench_catalog` lists the queryable fields; until
  custom indexes exist it always returns the built-in set above, so calling
  it is rarely necessary.
- **Content search stays with `workbench_grep`** — `workbench_search`
  matches paths and metadata, not file contents.

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
