---
related_files:
  - src/lingtai/tools/daemon/interactive_terminal/__init__.py
  - src/lingtai/tools/daemon/interactive_terminal/CONTRACT.md
  - src/lingtai/adapters/posix/interactive_terminal.py
  - src/lingtai/adapters/posix/ANATOMY.md
  - src/lingtai/tools/daemon/claude_interactive.py
  - src/lingtai/tools/daemon/__init__.py
  - src/lingtai/tools/daemon/ANATOMY.md
  - src/lingtai/tools/daemon/CONTRACT.md
  - src/lingtai/tools/daemon/DAEMON_CONTRACT.md
  - tests/test_interactive_terminal_port.py
  - tests/test_daemon_claude_interactive_backend.py
  - tests/test_lifecycle_daemon_shutdown.py
maintenance: |
  Keep related_files repo-relative and bidirectional with the capability
  Contract and neighboring adapter/daemon Anatomy files. This document maps
  local ownership; behavioral promises live in CONTRACT.md.
---
# Interactive terminal capability Anatomy

This capability-local package separates raw interactive-terminal transport from
Claude bridge policy. `__init__.py` defines immutable command/exit values, an
opaque handle, and the byte-stream `InteractiveTerminalPort`. The POSIX
implementation is `src/lingtai/adapters/posix/interactive_terminal.py`.

## Ownership map

- `InteractiveTerminalCommand` owns immutable `argv`, `cwd`, environment, and
  120x40 dimensions; it carries no backend policy.
- `InteractiveTerminalPort` owns only child/terminal transport and group/all
  ownership. It has no run-directory or notification effects.
- `PosixInteractiveTerminalAdapter` owns the PTY master/slave, child session,
  raw master reads/writes, bounded process-group termination, reaping, and
  terminal resource release.
- `ClaudeInteractiveBridge` keeps terminal probes, auth/trust refusal and
  managed-workspace trust, bracketed paste, hooks/FIFO, transcript projection,
  state/result policy, and cancellation/timeout classification. It accepts only
  an injected Port; it never imports or constructs a private terminal adapter.
  Missing injection is rejected before workspace, harness, or spawn work.
- `DaemonManager` composes one persistent POSIX adapter, passes it to initial/
  resume interactive bridge workers, and sweeps its group/all registry during
  timeout, reclaim, and parent stop. Its non-killing final `release()` leaves a
  stubborn live handle owned for a later sweep. It does not parse terminal bytes.

The interactive Port is deliberately not `DaemonProcessPort`: headless CLI
transport is line-oriented text with separate stderr and has no terminal input
or PTY dimensions. There is no universal Platform, ProcessSupervisor, Shell,
CommandRunner, or Terminal framework here.
