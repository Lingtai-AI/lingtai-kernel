---
name: daemon-forensics
description: >
  Nested daemon-manual reference for durable run artifacts, state/result
  inspection, transcripts, token ledgers, and interpreting exit 143/SIGTERM.
version: 1.4.0
last_changed_at: 2026-09-08T00:00:00Z
related_files:
- src/lingtai/tools/daemon/manual/SKILL.md
- src/lingtai/tools/daemon/run_dir.py
- src/lingtai/tools/daemon/runtime.py
maintenance: |
  Tracks the daemon artifact-forensics topic; update when run files or
  terminal/error reporting changes.
---

# Daemon Forensics Reference

Read this when a run needs evidence beyond the notification or `check` result.
The run directory is durable evidence, not a disposable workspace.

## Durable run layout

New tasks get `daemons/em-<id>/` under the parent working directory (or the
standalone state root). Returned id, `daemon.json.run_id`, and folder name
identify the same new run; legacy ids can be ambiguous, so prefer exact run ids.
`reclaim` cancels all running manager-owned work but leaves its files. A molt clears conversation
context; it does not wipe durable stores or run folders.

```text
daemons/em-<id>/
├── daemon.json             # identity, state, call parameters, previews
├── artifacts.json          # metadata-only manifest, written at terminal time
├── result.txt              # full result or bounded error text
├── .prompt                 # constructed system prompt
├── history/chat_history.jsonl
└── logs/{events,token_ledger}.jsonl
```

`artifacts.json` contains run-relative `{path, size, mtime, role}` entries plus
`state`, `result_path`, `error_path`, `artifacts_total`, and `truncated`. It
contains paths and metadata, never file contents. `check` prefers this
manifest and can produce a bounded fallback for live or legacy runs. Avoid
secret-bearing filenames.

## Disclosure order

1. Read the terminal notification and call `daemon(action="check", input={"id": "<run_id>"})`.
2. Open its durable `result_path` or `error_path`; use `.prompt` when the task
   or selected context needs verification.
3. Read `logs/events.jsonl`, `history/chat_history.jsonl`, or
   `logs/token_ledger.jsonl` only for the specific forensic question.

For a known legacy run, `check` or direct inspection may be needed even when
`list` omits it. An omitted ledger item is not proof of deletion or failure.

## State and failure fields

In `daemon.json`, inspect `state` (`running`, `done`, `failed`, `cancelled`,
`timeout`), `current_tool`, `turn`, `tool_call_count`, `last_output_at`,
`result_preview`, `result_path`, `error`, and `elapsed_s`. LingTai token totals
are in `tokens`; external CLI usage is separate and remains in run artifacts.
Native transcripts record task, assistant, tool-result, and follow-up entries;
CLI model transcripts may instead live in the vendor session store. CLI usage
is `cli_tokens`, not parent/kernel token-ledger spend; never sum both lanes as
if they were comparable. Read JSONL line by line, not as one JSON document.

## Suspected stall

A concrete progress question permits a bounded `check` before completion.
Compare state, tool/count, recent events and output timestamps over a meaningful
interval for this task; native transcript progress and CLI `last_output_at` are
different signals. A slow tool or quiet model is not by itself a stall. Do not
run completion-poll loops, infer failure from silence, or reclaim on a hunch.
If intervention is warranted, confirm its authority and **all** affected runs.
Normal waiting relies on terminal notifications; do not add a recurring timer.
Only if completion delivery itself is unverified and work remains pending, use
the Shell manual's one-shot wake route, sized to the expected task duration;
inspect progress on that wake instead of repeatedly re-arming it.

## Exit 143 / SIGTERM

A shell-style 143 usually represents `128 + SIGTERM`; a direct subprocess may
report `-15`. Neither proves a failed assertion, the sender, or even signal
termination by itself (a program can explicitly exit 143). Match the actual
supervisor receipt and events. A watchdog, reclaim, host shutdown, or the task's
own process tree may be responsible; a timeout or turn-limit failure need not
produce 143. Do not blame parent molt: context shedding is not cancellation.

Before rerunning, inspect `daemon.json`, physical `result.txt`, and recent events;
partial work may already suffice. Match configured limits and explicit cancel
records. If continuation is needed, keep the old artifacts and re-check scope
and execution-body instructions: native terminal runs cannot resume, while some
CLI runs support `ask`. No automatic rerun or model/backend switch is authorized.
Report the observed termination and evidence-backed cause, or say it is unknown.

## Safety boundary

Inspection is read-only. Do not infer liveness from one quiet snapshot, delete
run evidence while diagnosing, print credential values, or treat a partial
143 result as a reason to widen the parent task. Consent-gated footprint and
deletion procedure belongs in `../cleanup/SKILL.md`.
