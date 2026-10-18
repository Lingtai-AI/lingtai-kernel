---
name: workflow
contract_version: 1
root_contract: CONTRACT.md
related_files:
  - src/lingtai/kernel/tool_plugin/CONTRACT.md
  - src/lingtai/tools/workflow/ANATOMY.md
  - src/lingtai/tools/workflow/BEHAVIORS.md
  - src/lingtai/tools/workflow/manual/SKILL.md
  - src/lingtai/tools/workflow/core.py
  - src/lingtai/tools/workflow/__init__.py
  - src/lingtai/adapters/workflow_cli.py
  - src/lingtai/tools/CONTRACT.md
  - tests/test_workflow.py
maintenance: |
  Keep the owner twins, manual and behaviors reciprocal. Update policy, worker
  Port, adapter and focused tests together when ordering or recovery changes.
---
# Ordered workflow

## Purpose

An independent opt-in declared tool, not a replacement for daemon. Only ordered
single-line stages of authorized daemon tasks are supported; no DAG, shell,
Web, MCP executor, plan CRUD, review action, or retry framework.

## Ports and adapter

`WorkflowManager` owns run/check/stop policy. `WorkerBatches` is the earned
external worker-process boundary: dispatch accepts one whole batch, inspect
returns worker state and refs, cancel reclaims only the supplied isolated root.
`DaemonCliBatches` implements it with safe argv, the current Python executable,
explicit cwd and the existing public `daemon` CLI. Acceptance is not completion.
The adapter bridges host-contained `task_files` into the private root just before
stage dispatch: bounded UTF-8/no-NUL reads, atomic digest-named exact-byte files,
a derived payload and source-path map. Only derived `task_files` paths change;
submitted plan/stage JSON and annotations remain unchanged. The CLI owns final
validation and canonical snapshots; count/annotation rules are not duplicated.
Preset/skill references retain standalone private-root resolution, not staging.
The binder receives workdir, notifications, shutdown and a current/retain manager
slot only, never the Agent. The slot prevents duplicate monitors across refresh.

## Behavior

Guarded by [WF001](BEHAVIORS.md#behavior-wf001).

Each submitted JSON plan is snapshotted; deleting/editing its temporary source
cannot alter accepted work. One stage is active per workflow; its tasks use one
parallel batch. Only all-worker `done` advances. Auto-continuation defaults off;
when explicitly authorized it runs while main is IDLE without stage-by-stage
wakes. `pause_after` forces a deliberate boundary. Pauses, terminal outcomes,
and uncertainty publish existing durable system notifications, with publication
failure visible in check. Failure stops progression (siblings may still run).

Guarded by [WF002](BEHAVIORS.md#behavior-wf002).

`run(file, run_id)` continues only a paused boundary, preserving completed
prefix, history, actual daemon refs and all submissions. No failed/uncertain
retry or blind replay. Dispatch intent persists before the subprocess. Lost
monitor/reconstruction, malformed acceptance, CLI timeout or inspection failure
stops visibly as uncertain. A process restart does NOT resume active work.
In-process refresh retains the same manager/monitor. Host shutdown stops
continuation, not detached workers; inspect refs and explicitly stop/reconcile.

Guarded by [WF003](BEHAVIORS.md#behavior-wf003).

Stop intent is set before waiting on the per-run transition lock and is never
cleared. Paused continuation rejects pending intent; intent racing admission
remains visible to the monitor's pre-dispatch guards. Stopped runs cannot resume.
It prevents later stages, serializes against dispatch, and invokes only CLI reclaim on
`workflow/<run_id>/daemon`. A `reclaimed` receipt means cancellation requested,
NOT proof every external process/effect ceased. Reclaim failure is stop_failed;
there is no direct-kill fallback. Completed effects are never undone. Paths,
submissions, stage index, acceptance, results and errors remain under the run.
The authority boundary is the trusted local host/file author, not a sandbox
against a malicious process mutating run state or symlinks.
