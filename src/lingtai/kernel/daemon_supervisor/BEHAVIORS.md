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
  - src/lingtai/adapters/posix/daemon_mach.py
  - tests/test_daemon_mach.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when a
  detached-supervisor behavior clause changes, update the guarding LABT here in
  the same change.
---
> Test-reference maintenance (2026-10-12): `retired-test:` identifiers below name removed pytest coverage, not runnable selectors or replacement conformance. Product promises are unchanged; manual acceptance remains necessary where automated coverage was surrendered.

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
2. **Automated step unavailable: pytest recipe retired (2026-10-12); no replacement conformance claimed** and capture the outcome.
3. Inspect a real detached run's run directory: confirm the manifest/log files contain no resolved credentials and that exactly one terminal notification event is published for the run.

### Expected evidence
- [ ] Step 1: the detached-supervisor suite passes, pinning real detached launch, parent shutdown survival, identity mismatch, timeout/reclaim, and control ack/race truth.
- [ ] Step 2: the daemon lifecycle suite passes (including completion/MCP/preset/skills reconstruction paths).
- [ ] Step 3: manifest, control, and log files contain no resolved credentials (only environment/config references), and terminal notification idempotency holds — one notification per run.

### Pass / Fail
Pass when both suites pass and the run directory shows the secret boundary and one terminal notification. Fail on credential leakage into durable files, on a second terminal notification for one run, or on a supervisor that does not survive parent exit; record the evidence trail in the task report.

## Behavior DS002

- **id**: DS002
- **title**: Darwin rendezvous preserves descriptor transfer without a local socket file
- **guards**: [daemon-supervisor § Adapters](CONTRACT.md#adapters)
- **runner**: an agent authorized to run deterministic local process fixtures
- **prerequisites**: macOS 15.7.3; candidate checkout; Python test environment;
  owned project scratch only. No model/provider calls are needed.
- **estimate**: approximately one minute for focused automated checks

### Steps
1. Run `python -m pytest -q tests/test_daemon_mach.py tests/test_daemon_manager_socket_fallback.py`.
2. Inspect the native tests: ensure SCM_RIGHTS payload and receiver EOF are
   checked, not merely an ACK; verify wrong peer identity is refused before
   capsule bytes and the listener survives rejected requests.
3. Check repeated successful/failed rendezvous against both open-FD and Mach
   port-name counts; malformed/oversized requests must not retire the listener.

### Expected evidence
- Native capsule and adopted FD round trip succeeds; rejected peers receive no
  capsule bytes. All observed native rights and FDs return to baseline.
- Restart retires the old service; unsupported-path selection stays inside the
  project. Non-Darwin behavior is not represented as validated by these tests.

### Pass / Fail
Pass only if the focused suites pass on the stated host. Reject any silent FD
omission, unexpected local-file fallback, leaked right, or accepted wrong peer.
Actual filesystem-level acceptance additionally needs owned exFAT/APFS process
fixtures; these unit tests alone do not prove absence of every system write.
