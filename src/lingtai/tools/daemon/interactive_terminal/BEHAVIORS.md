---
name: interactive-terminal-behavior-tests
behavior_version: 1
labt_version: 2
contract: CONTRACT.md
anatomy: ANATOMY.md
related_files:
  - src/lingtai/tools/daemon/interactive_terminal/CONTRACT.md
  - src/lingtai/tools/daemon/interactive_terminal/ANATOMY.md
  - src/lingtai/tools/daemon/interactive_terminal/__init__.py
  - src/lingtai/adapters/posix/interactive_terminal.py
maintenance: |
  Created during the every-contract-needs-behaviors sweep. Keep this file
  reciprocal with CONTRACT.md and ANATOMY.md (tridirectional loop): when an
  interactive-terminal behavior clause changes, update the guarding LABT here
  in the same change.
---
# Interactive Terminal Port Behavior Tests

Self-contained agent behavior tasks guarding the observable behavior clauses of
`src/lingtai/tools/daemon/interactive_terminal/CONTRACT.md` (opaque handles,
raw byte reads ending with an empty chunk, deadline-bounded reads, wait
timeout without termination). Pinned pytest commands must run from the repo
root with the project's Python.

## Behavior IT001 — spawn returns only an opaque handle, read yields raw bytes and ends with an empty chunk, and wait timeout does not terminate the child

- **id**: IT001
- **title**: spawn returns only an opaque handle, read yields raw bytes and ends with an empty chunk, and wait timeout does not terminate the child
- **guards**: `daemon-interactive-terminal-contract` § Operations
- **runner**: any LingTai agent with `shell` access to this repository
- **prerequisites**: a clean checkout of `<repo>`; a POSIX host with `pty` available (or a Windows runner using the port tests' native lane)
- **estimate**: ≈ 20 minutes

### Steps
1. From `<repo>`, run `python -m pytest tests/test_interactive_terminal_port.py -q` and capture the outcome.
2. Spawn a child that echoes bytes through the Port and confirm the returned value is an opaque handle (never a PID/process object), that `read` returns arbitrary raw byte chunks, and that the final chunk is empty at terminal EOF.
3. Call `wait(handle, timeout=0.1)` on a child that stays alive and confirm `TimeoutError` is raised and the child is not terminated; then `terminate` the handle and confirm an exit receipt with the first non-`None` local reason retained.

### Expected evidence
- [ ] Step 1: the interactive-terminal port suite passes, pinning spawn/read/write/wait/terminate/release semantics and the default dimensions 120x40.
- [ ] Step 2: spawn returns only an opaque handle; read never decodes, line-buffers, or strips ANSI/control bytes and ends with an empty chunk at EOF.
- [ ] Step 3: a `wait` timeout raises `TimeoutError` without terminating the child; `terminate` uses bounded process-group TERM/KILL ownership; `release` is idempotent and returns `False` for a live child.

### Pass / Fail
Pass when the suite passes and the opaque-handle/raw-byte observations hold. Fail on a spawn exposing a process handle, on decoded or stripped read output, or on a wait timeout that kills the child; record the evidence trail in the task report.
