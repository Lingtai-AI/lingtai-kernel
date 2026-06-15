---
name: daemon-forensics
description: >
  Nested daemon-manual reference for daemon artifact forensics: persistent
  daemons/em-* folders, daemon.json status fields, chat_history.jsonl,
  token_ledger.jsonl, events.jsonl, and how to inspect progress without guessing.
version: 1.0.0
---

# Daemon Forensics Reference

Nested daemon-manual reference. Open this when you need to inspect an emanation's
on-disk state, transcript, or token/event artifacts.

## Each emanation is a forensic mini-avatar

Every time you call `daemon(action="emanate", tasks=[...])`, each task gets a working folder under `daemons/` in your own directory. The folder is named:

    daemons/em-<N>-<YYYYMMDD-HHMMSS>-<6 hex>/

where `em-<N>` is the in-context handle (e.g. `em-3`). The handle resets to `em-1` after `reclaim`, but the timestamp+hash means historical folders never collide. **Folders persist forever** — `reclaim` only stops processes, not files. They're cleaned up incidentally when you molt (which wipes the working directory).

This means: when an emanation looks stuck, you can read its actual state instead of guessing. Don't kill it on a hunch — inspect first.

## Folder layout

```
daemons/em-3-20260427-094215-a1b2c3/
├── daemon.json                  ← versioned identity card + live status snapshot + visible call parameters
├── result.txt                   ← full terminal result when available
├── .prompt                      ← system prompt as built (forensic)
├── .heartbeat                   ← mtime touched on every write
├── history/
│   └── chat_history.jsonl       ← full LLM transcript
└── logs/
    ├── token_ledger.jsonl       ← per-call token usage
    └── events.jsonl             ← daemon_start, tool_call, tool_result, cli_output, daemon_done/...
```

Progressive disclosure starts with `daemon(action="list")`: it reads these
per-run JSON/files and returns a compact searchable index (metadata, visible call
parameters, prompt/result previews, paths). `daemon.json` carries a
`data_version`; if the file is missing, corrupt, or stale, list-time lazy
migration rebuilds a best-effort replacement from the folder name, `.prompt`,
`result.txt`, mtimes, and recent `events.jsonl`, recording a `migration`
reason. If the index is not enough, read the returned `.prompt` or
`result.txt` directly. Only then drop to full forensic grep over
`logs/events.jsonl`, `history/chat_history.jsonl`, or `logs/token_ledger.jsonl`.

## Inspection patterns

### "Is this emanation actually doing anything?"

Read `daemon.json` once. The fields you want:

- `state` — `running` / `done` / `failed` / `cancelled` / `timeout`
- `current_tool` — `"read"` / `"bash"` / null. If null while `state=running`, the emanation is waiting on the LLM. If non-null, it's executing that tool.
- `turn` — which LLM round the emanation is on
- `tool_call_count` — how many tool dispatches it has done
- `tokens` — running totals (lingtai backend only; stays at 0 for `claude-code` and `codex` backends — see "CLI backends" below)
- `last_output` / `last_output_at` — recent stdout/stderr from CLI backends
- `result_preview` / `result_path` — bounded terminal preview and full `result.txt` path after completion
- `elapsed_s` — wall clock since start

If `current_tool` is null AND `tool_call_count` hasn't changed for a while, the LLM is thinking — wait. If `current_tool` is set and stays set, that tool is slow (e.g., a big file read or a long bash command).

### "What has it figured out so far?"

Tail `history/chat_history.jsonl`. Each line is one role/turn entry:

- `{role: "user", kind: "task"}` — the original task
- `{role: "assistant", text: "..."}` — what the emanation said
- `{role: "user", kind: "tool_results"}` — what the tools returned
- `{role: "user", kind: "followup"}` — your `daemon(action="ask", ...)` messages

Read the most recent assistant text to see the latest progress narrative.

### "What did it spend?"

Either of:
- `daemon.json` field `tokens` — running totals across the whole run
- `logs/token_ledger.jsonl` — per-call entries, sortable by line

The same per-call entries are also in your own `logs/token_ledger.jsonl` (the parent's), tagged with `source: "daemon"` and `em_id`. Your lifetime token totals (what `sum_token_ledger` reports) include all daemon spend.

### "Why did it fail?"

Read `daemon.json`'s `error` field — `{type, message}`. For more depth, tail `logs/events.jsonl` for the `daemon_error` event and look at the preceding `tool_call`/`tool_result` entries to see what was happening just before the failure.
