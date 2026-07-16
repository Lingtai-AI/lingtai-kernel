---
name: daemon-interactive-terminal-contract
description: Capability-local raw byte-stream terminal ownership contract for hidden interactive Claude.
status: active
contract_version: 1
related_files:
  - src/lingtai/tools/daemon/interactive_terminal/__init__.py
  - src/lingtai/tools/daemon/interactive_terminal/ANATOMY.md
  - src/lingtai/adapters/posix/interactive_terminal.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/tools/daemon/claude_interactive.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/daemon/CONTRACT.md
  - src/lingtai/tools/daemon/DAEMON_CONTRACT.md
  - src/lingtai/tools/daemon/ANATOMY.md
  - tests/test_interactive_terminal_port.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_lifecycle_daemon_shutdown.py
maintenance: |
  Keep this capability-local Contract and its reciprocal Anatomy links aligned
  with the Port, POSIX adapter, bridge, and daemon lifecycle composition. This
  is not a widening of DaemonProcessPort and must not become a universal
  process/platform/terminal abstraction.
---
# Interactive terminal Port Contract

## Scope

`InteractiveTerminalPort` is the narrow mechanism boundary for the retained,
hidden interactive Claude route. It carries one immutable direct command,
opaque child handles, and raw terminal bytes. It does not know Claude hooks,
probe policy, auth/trust decisions, prompt framing, transcript/result state,
usage, notifications, MCP, or JSON run artifacts.

`InteractiveTerminalCommand` freezes `argv`, `cwd`, optional environment, and
explicit terminal dimensions. The default dimensions are exactly 120 columns
by 40 rows. `InteractiveTerminalExit` preserves the raw child return code and
the first local termination reason. No shell string is accepted or built by
the Port.

## Operations

- `spawn(command, group_id=None)` creates a child attached to a real terminal
  and returns only an opaque handle.
- `read(handle, deadline=None)` yields arbitrary raw byte chunks and ends with
  an empty chunk at terminal EOF. It never decodes, line-buffers, or strips
  ANSI/control bytes. A deadline is a monotonic deadline used only to bound
  this read call.
- `write(handle, data)` writes bytes to terminal input and must handle partial
  writes. It never interprets prompt or control framing.
- `wait(handle, timeout=None)` reaps the child and returns an exit receipt;
  timeout raises `TimeoutError` without terminating the child.
- `terminate(handle, reason=None)` uses bounded process-group TERM/KILL
  ownership and returns an exit receipt. The first non-`None` local reason is
  retained across later waits or termination attempts.
- `terminate_group` and `terminate_all` sweep only handles owned by this Port
  and return the number targeted. A concurrent terminal release may make a
  snapshot entry stale; remaining entries are still swept.
- `release(handle)` is idempotent and removes/closes terminal resources only
  after the child is reaped. It returns `False` for a live child. The persistent,
  manager-injected Port keeps a live handle owned for a later group/all sweep;
  manager-owned final release is non-killing.

## Composition invariant

`ClaudeInteractiveBridge` never imports or constructs a terminal adapter. Runtime
composition must inject one persistent `InteractiveTerminalPort` owned by the
`DaemonManager` before the bridge runs. A missing injection fails loudly and
deterministically before managed-workspace or harness preparation and before any
terminal spawn. This keeps every live handle registered with the manager so a
bounded TERM/KILL that leaves a child live remains reachable by later group/all
sweeps.

The POSIX adapter owns only `pty.openpty`, slave stdio attachment,
`start_new_session`, terminal dimensions, master byte I/O, scoped child
termination, reaping, and terminal-resource release. In ordinary manager
composition it starts a fresh private session and may terminate that private
process group. Detached execution passes `start_new_session=False` and marks
the handle `INHERITED_SUPERVISOR_GROUP`: interactive Stop, timeout, error,
cancel, group, and all operations signal/reap only the exact PTY child, never
the execution-host interpreter/caller. Supervisor exact-run reclaim alone may
reclaim the inherited group. The detached adapter may publish the immutable
PID/PGID/start-identity/termination-scope observation used by the headless Port before the
bridge begins reading. If observation/state publication fails, it terminates
and reaps that exact child, removes the registry entry, closes the PTY master,
and re-raises transactionally. Bridge policy remains in
`claude_interactive.py`. Windows ConPTY is not implemented by this slice.
