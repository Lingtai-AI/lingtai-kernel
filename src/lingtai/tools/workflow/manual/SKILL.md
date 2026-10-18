---
name: workflow-manual
description: Ordered daemon batches from a snapshotted JSON workflow file.
related_files:
  - src/lingtai/tools/workflow/ANATOMY.md
  - src/lingtai/tools/workflow/CONTRACT.md
  - src/lingtai/tools/workflow/__init__.py
  - src/lingtai/tools/task_card/manual/SKILL.md
maintenance: |
  Keep run/check/stop, continuation and failure guidance aligned with the owner
  Contract and focused workflow tests. No live plugin activation instructions.
---
# Workflow

Independent opt-in capability (`capabilities={"workflow": {}}` at host
construction); daemon stays available directly. This manual describes repository
composition, not permission to change a live agent's configuration.

## Run

Author a temporary JSON file with ordinary file operations, then call
`workflow(action="run", input={"file":"plan.json"}, reasoning="authorized work",
summarize=false)`. Relative files resolve against the agent workdir.

```json
{"auto_continue":false,"stages":[
  {"tasks":[{"task":"First authorized task", "preset":"directly-loadable-preset"},
            {"task":"Parallel authorized task", "preset":"directly-loadable-preset"}]},
  {"tasks":[{"task":"Next authorized task", "preset":"directly-loadable-preset"}],
   "pause_after":true}
]}
```

Stages are ordered, tasks within a stage are one parallel daemon batch. Stage
fields are only `tasks`, optional `backend`, positive `max_turns`, positive
finite `timeout` seconds, and boolean `pause_after`. Task objects are the existing
daemon CLI inputs (including its tools/preset/skills/MCP/task_files mechanisms),
not a new executor language. Standalone native tasks need a directly loadable
preset; parent tools, MCP registrations and provider credentials are NOT inherited.
Use only tasks/presets/backend already authorized by the host/human.

The top level allows only `stages` and optional boolean `auto_continue` (false
by default). Set true only when automatic success-only progression is authorized.
A stage's `pause_after:true` forces a handoff even with auto enabled. There is
no separate review/accept action. The submitted plan and per-stage batch payload
are snapshotted under `workflow/<run_id>/`; subsequent source edits/deletion
have no effect on accepted JSON. `task_files` paths resolve relative to the host
agent workdir (not the plan's directory), or may be absolute inside that workdir.
Resolved paths, including symlink targets, must stay inside the host workdir.
Immediately before each stage's CLI dispatch, the adapter reads these inputs
with the daemon's 1,000,000-byte-per-file, UTF-8/no-NUL limits and atomically
stages exact bytes in the workflow's private root. Input bytes freeze **then**,
not at initial plan submission: future stages can consume files produced by
previous stages. Editing/deleting an input after staging cannot change that
stage's input. The daemon CLI still validates task counts/annotations and owns
its canonical input snapshots. No workers are started by staging alone.

Only the derived CLI payload's `task_files` paths change. Original plan/stage
JSON, labels and roles remain unchanged; digest-named staged files, derived
payloads and `.sources.json` maps preserve original/resolved source paths for
reconciliation under `<run>/daemon/workflow-inputs/`. Partial staging after a
refusal is only private scratch, not worker acceptance; never replay ambiguous
work. Other referenced files are not frozen by workflow. In particular,
`preset` and `skills` retain standalone daemon semantics: relative paths use the
private daemon root, not main's workdir. Use already-authorized absolute (or
home-relative) preset/skill paths for files outside that root; they are not
copied, and mutable preset/skill contents are not snapshotted by workflow.

## Check and continue

Run returns a workflow run_id, NOT worker completion. Main can be IDLE during
auto progression; no successful automatic-boundary wakes. Pause, final outcome,
failure and uncertainty publish durable system notifications. After notification,
call `check` with `{"run_id":"wf-..."}` for stage index, plan snapshots, actual
daemon IDs/run IDs, result/error paths and notification errors. Do not busy-poll.

At `paused`, edit a new temporary plan with an identical completed stage prefix
and revised remaining stages, then call `run` with both file and the SAME run_id.
Completed prefix and history are immutable. This only dispatches the next stage;
it never retries a failed/uncertain stage or repeats completed external effects.

## Stop and uncertainty

Call `stop` with the workflow run_id. Stop intent is monotonic: a paused run
with a pending stop cannot continue, and stopped runs cannot resume. It blocks
next stages and reclaims only that workflow's private daemon root, never main's
or another workflow's.
Stop waits for an already-admitted CLI call/check sequence (each subprocess has
a 60-second timeout); it does not interrupt the subprocess or kill workers.
`stopped` means the daemon CLI returned `reclaimed` (cancellation requested), NOT
proof that external processes/effects have ceased. `stop_failed` means reclaim
was not confirmed. There is no direct-kill fallback. Inspect preserved refs;
already-completed effects are not rolled back. A stage failure blocks progression
but siblings may still run: explicitly stop to reclaim them when appropriate.

In-process refresh retains one monitor. Host shutdown ends progression without
killing detached workers. After process loss/reconstruction, active work becomes
`uncertain`, never automatically restarts. CLI timeout, malformed/ambiguous
acceptance or inspection errors also become uncertain. Inspect the private root
with existing CLI `check <daemon-id> --state-root <run>/daemon`; do not blindly
submit the same work under a new ID. Reconcile external effects before authorizing
new work. This slice intentionally has no automatic recovery/retry engine.

## Presentation

Use the existing [Task Card](../../task_card/manual/SKILL.md) for a short,
free-form, agent-authored account of the workflow process and progress. Update
it on meaningful state changes; choose wording suited to the work, without a
fixed template or generated diagram. Retire the card at completion; do not poll
the workflow just to refresh it.

Root summarize defaults on. Run/stop normally have short receipts; check can
include bulky worker results. Use summarize=false for exact IDs/paths, receipts,
and this manual. Summaries are lossy; preserved snapshots/results are canonical.
