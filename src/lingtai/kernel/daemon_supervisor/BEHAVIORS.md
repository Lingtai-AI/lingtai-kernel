---
name: daemon-supervisor-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/kernel/daemon_supervisor/CONTRACT.md
  - src/lingtai/kernel/daemon_supervisor/ANATOMY.md
  - src/lingtai/kernel/daemon_supervisor/__init__.py
  - src/lingtai/kernel/daemon_supervisor/manifest.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  detached-supervisor behavior clause changes, update the guarding LABT here in
  the same change.
---
# Detached Daemon Supervisor Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/kernel/daemon_supervisor/CONTRACT.md` (one supervisor owns one run;
terminal truth; idempotent notification; secret boundary). Pinned pytest
commands must run from the repo root with the project's Python.

## Behavior DS001 — one supervisor owns one run from birth through terminal state and publishes one idempotent notification

- **id**: DS001
- **title**: one supervisor owns one run from birth through terminal state and publishes one idempotent notification
- **guards**: `daemon-supervisor` § Runtime promise
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a scratch run directory `<scratch>`; the runtime venv interpreter
- **estimate**: ≈ 30 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_daemon_detached_supervisor.py -q` and capture the outcome.
2. Run `python -m pytest tests/test_daemon.py -q` and capture the outcome.
3. Inspect a real detached run's run directory: confirm the manifest/log files contain no resolved credentials and that exactly one terminal notification event is published for the run.

### Expected evidence
- [ ] Step 1: the detached-supervisor suite passes, pinning real detached launch, parent shutdown survival, identity mismatch, timeout/reclaim, and control ack/race truth.
- [ ] Step 2: the daemon lifecycle suite passes (including completion/MCP/preset/skills reconstruction paths).
- [ ] Step 3: manifest, control, and log files contain no resolved credentials (only environment/config references), and terminal notification idempotency holds — one notification per run.

### Pass / Fail
Pass when both suites pass and the run directory shows the secret boundary and one terminal notification. Fail on credential leakage into durable files, on a second terminal notification for one run, or on a supervisor that does not survive parent exit; record the evidence trail in the task report.
