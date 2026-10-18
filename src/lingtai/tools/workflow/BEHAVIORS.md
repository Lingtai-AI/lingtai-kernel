---
name: workflow-behaviors
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/workflow/CONTRACT.md
  - src/lingtai/tools/workflow/ANATOMY.md
  - src/lingtai/tools/workflow/core.py
  - tests/test_workflow.py
maintenance: |
  Keep WF001–WF003 aligned with policy and focused evidence. These recipes use
  mocks/read-only subprocesses and do not imply live provider conformance.
---
# Workflow behavior tasks

## Behavior WF001 — ordered parallel batches and authorized automatic continuation

- **id**: WF001
- **guards**: [workflow § Behavior](CONTRACT.md#behavior), ordering and notifications
- **runner**: agent with shell access to the repository
- **prerequisites**: candidate checkout and active Python with pytest; no provider needed
- **estimate**: 2 minutes

### Steps

1. From repository root run `python -m pytest -q tests/test_workflow.py -k 'ordered_parallel or daemon_failure or shutdown'` with the active venv Python.
2. Inspect fixture assertions: first stage is one two-task batch; a single
   completed worker does not advance; second batch starts without main check/run;
   successful automatic boundary publishes no wake, terminal completion does.

### Expected evidence

- [ ] Tests pass; temporary plan deletion does not change dispatched batches.
- [ ] Failure/shutdown blocks later dispatch and visible state remains truthful.

### Pass / Fail

Pass only with exit 0 and both assertions verified. This is mock-worker evidence,
not live model quality or provider integration proof.

## Behavior WF002 — paused edits preserve completed effects and ambiguity never replays

- **id**: WF002
- **guards**: [workflow § Behavior](CONTRACT.md#behavior), continuation and recovery
- **runner**: agent with shell access to the repository
- **prerequisites**: same isolated pytest environment as WF001
- **estimate**: 2 minutes

### Steps

1. Run `python -m pytest -q tests/test_workflow.py -k 'paused or ambiguous or reconstruction or timeout'`.
2. Verify revised remaining tasks dispatch once, completed prefix mutation fails,
   actual daemon refs/history and snapshots remain unchanged; CLI ambiguity and
   lost monitors never dispatch a replacement batch.

### Expected evidence

- [ ] Exit 0 and preserved run refs across both pause modes.
- [ ] No acceptance failure or reconstruction is treated as worker completion.

### Pass / Fail

Fail on replay, modified completed prefix, lost refs or any nonzero test exit.

## Behavior WF003 — scoped stop, strict envelope and opt-in real host construction

- **id**: WF003
- **guards**: [workflow § Behavior](CONTRACT.md#behavior), stop and isolation
- **runner**: agent with shell access to the repository
- **prerequisites**: same isolated pytest environment as WF001
- **estimate**: 2 minutes

### Steps

1. Run `python -m pytest -q tests/test_workflow.py -k 'stop_scope or stop_intent or task_files or strict or real_agent or real_cli or check_nonterminal'`.
2. Verify an in-flight dispatch/stop race cannot start another stage and reclaim
   targets only the generated workflow root. Check stop failure stays visible.
   The scheduled paused-continuation regression suspends stop immediately after
   publishing intent and also exercises intent racing admission; no later batch
   may dispatch, and pending intent must never clear.
   Task-file probes use the actual daemon validator/materializer (no emanate or
   provider): host-relative/absolute inputs stage successfully, labels/roles and
   original JSON survive, deleted host inputs do not affect frozen bytes, and
   out-of-host/symlink escapes and invalid text are refused before CLI calls.
3. Verify the actual Agent builds its full prompt, installs the manual and exposes
   both daemon and opt-in workflow; binding again retains the same manager.

### Expected evidence

- [ ] Exit 0, scoped argv and no direct-kill path.
- [ ] Strict input rejection before side effects; read-only actual candidate CLI
  check uses no workers/provider. Real Agent uses a mocked service, not a live LLM.

### Pass / Fail

Fail on wrong-root cancellation, racing next-stage dispatch, default-on workflow,
missing daemon/manual, or startup/prompt exception.
