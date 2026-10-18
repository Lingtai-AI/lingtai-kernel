---
related_files:
  - src/lingtai/kernel/tool_result_summary.py
  - src/lingtai/tools/workflow/CONTRACT.md
  - src/lingtai/tools/workflow/BEHAVIORS.md
  - src/lingtai/tools/workflow/manual/SKILL.md
  - src/lingtai/tools/workflow/__init__.py
  - src/lingtai/tools/workflow/core.py
  - src/lingtai/adapters/workflow_cli.py
  - src/lingtai/adapters/tool_plugin_host.py
  - src/lingtai/kernel/tool_plugin/ANATOMY.md
  - src/lingtai/tools/ANATOMY.md
  - tests/test_workflow.py
maintenance: |
  Keep the owner Contract, Behavior tasks and manual reciprocal. Verify changed
  code citations and update both owner twins when structure or policy changes.
---
# Workflow

Independent opt-in ordered stage coordinator with an external daemon CLI boundary.

## Components

- `__init__.py:74` declares the static official identity; its binder composes
  the CLI adapter and retains one manager across refresh (WF001–WF003).
- `core.py:16` owns `WorkerBatches`, the external batch Port;
  `core.py:69` owns ordered policy in `WorkflowManager` (WF001–WF003).
- `src/lingtai/adapters/workflow_cli.py:16` implements the Port with safe argv
  to the public daemon CLI, never private daemon engine imports. Its
  `_stage_task_files` bridges host-contained inputs into private digest blobs
  and derived CLI payloads/source maps (WF003).
- `manual/SKILL.md` teaches run/check/stop and explicit paused continuation.
- `tests/test_workflow.py` contains mock-worker contract tests, an actual
  read-only CLI subprocess probe and real Agent/full-prompt construction proof.

## Connections

The hand-edited tools registry exposes workflow only on explicit opt-in. The
kernel registrar reserves its name. The binder receives only workdir,
notifications, shutdown and the workflow current/retain-manager slot through
`src/lingtai/adapters/tool_plugin_host.py`. Manager policy calls the injected
batch Port; only the outer adapter speaks daemon CLI JSON/argv.

## Composition

Parent: `src/lingtai/tools/ANATOMY.md`; declaration boundary:
`src/lingtai/kernel/tool_plugin/ANATOMY.md`. No child components. The paired
Contract owns promises; BEHAVIORS WF001–WF003 guard them.

## State

`workflow/<run_id>/` owns immutable plan revisions, stage batch JSON, atomic
state/history/results and a private `daemon/` state root. Adapter-owned
`daemon/workflow-inputs/` retains exact-byte digest blobs, derived payloads and
source maps; the daemon still owns canonical input snapshots. One monitor thread,
transition lock and stop event per run are process-local. No process restart
replay or automatic worker reconstruction. Existing daemon roots are untouched.

## Notes

This is not an executor framework or a replacement daemon tool. Host-contained
task files freeze at per-stage staging, not submission; preset/skill references
retain existing daemon semantics. JSON snapshots are not a new filesystem
sandbox. Native Windows/live-provider proof is not claimed.
