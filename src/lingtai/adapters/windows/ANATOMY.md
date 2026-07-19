---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/kernel/workdir_lease/ANATOMY.md
  - src/lingtai/kernel/workdir_lease/CONTRACT.md
  - src/lingtai/kernel/refresh_watcher/ANATOMY.md
  - src/lingtai/adapters/windows/__init__.py
  - src/lingtai/adapters/windows/_win32.py
  - src/lingtai/adapters/windows/workdir_lease.py
  - src/lingtai/adapters/windows/refresh_watcher.py
  - src/lingtai/adapters/windows/refresh_watcher_process.py
  - src/lingtai/adapters/windows/refresh_watcher_entrypoint.py
  - src/lingtai/adapters/windows/process_scan.py
  - src/lingtai/adapters/windows/avatar_launcher.py
  - src/lingtai/adapters/windows/daemon_supervisor.py
  - src/lingtai/adapters/windows/daemon_supervisor_entrypoint.py
  - src/lingtai/adapters/windows/daemon_execution_child_entrypoint.py
  - src/lingtai/adapters/windows/daemon_resume_owner_entrypoint.py
  - src/lingtai/adapters/windows/process_identity.py
  - src/lingtai/kernel/daemon_supervisor/CONTRACT.md
  - src/lingtai/adapters/windows/powershell.py
  - src/lingtai/adapters/windows/powershell_process.py
  - src/lingtai/adapters/windows/powershell_state_lock.py
  - src/lingtai/tools/bash/ANATOMY.md
  - src/lingtai/tools/bash/CONTRACT.md
  - src/lingtai/tools/avatar/ANATOMY.md
  - src/lingtai/tools/avatar/CONTRACT.md
  - src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
maintenance: |
  Keep related_files repo-relative, duplicate-free, and linked to real files.
  Keep parent/child anatomy links bidirectional. Code is the structural source of
  truth: update this anatomy in the same change that moves files, symbols,
  connections, composition, or state. Verify every changed citation and run the
  architecture-document validation before merge.
  Follow the root Anatomy/Contract pairing rule, report mismatches, and do not duplicate or auto-fix the rule here.
---
# Windows Adapter Anatomy

This narrow package contains the production native-Windows adapters for
Core-owned and capability-owned Ports: the workdir lease, the refresh-watcher
outer/process/entrypoint trio, the duplicate-launch process scan, the avatar
launcher, the detached daemon supervisor (with its three entrypoint mirrors
and the process-incarnation identity), and the shell capability's PowerShell
dialect, Job Object process adapter, and state lock. It is an implementation-only Anatomy with no
independent local Contract; for the Anatomy/Contract pairing rule its unique
owning Core component Contract is
`src/lingtai/kernel/workdir_lease/CONTRACT.md` (this Anatomy is listed only in
that Contract's `related_files`). Each adapter implements its owning Port
rather than defining a separate behavioral promise: shell promises are owned
by `src/lingtai/tools/bash/CONTRACT.md`, avatar promises by
`src/lingtai/tools/avatar/CONTRACT.md`, refresh-watcher promises by
`src/lingtai/kernel/refresh_watcher/CONTRACT.md`, and lease/scan promises by
`src/lingtai/kernel/workdir_lease/CONTRACT.md` /
`src/lingtai/kernel/base_agent/CONTRACT.md`. Every module here imports its
Windows mechanism (`msvcrt`, Win32 `ctypes` surfaces) lazily or guards it
behind an `os.name` check so the package stays importable on every platform;
only method execution requires Windows.

## Components

- `_win32` — shared low-level ctypes surface: `process_alive`
  (OpenProcess/GetExitCodeProcess — never `os.kill`, which terminates on
  Windows), `process_creation_identity` (`windows:<creation_filetime>`),
  `terminate_pid` (exact-PID `TerminateProcess`), and the
  `DETACHED_CREATIONFLAGS` spawn constant
  (`src/lingtai/adapters/windows/_win32.py`).
- `WindowsWorkdirLeaseAdapter` — exclusive workdir lease via `msvcrt.locking`
  byte 0/length 1 on `<workdir>/.agent.lock`, the frozen TUI-probe interop
  range (`src/lingtai/adapters/windows/workdir_lease.py`).
- `WindowsRefreshWatcherAdapter` — detached `-m` watcher handoff with the
  shared creation flags (`src/lingtai/adapters/windows/refresh_watcher.py`);
  `refresh_watcher_entrypoint.main` decodes/renders the Core policy and
  injects the workdir-bound process mechanism
  (`src/lingtai/adapters/windows/refresh_watcher_entrypoint.py`).
- `WindowsRefreshWatcherProcessAdapter` — watcher-local process mechanism:
  CIM `Win32_Process` command-line observation, handle-based liveness,
  detached replacement launch, `.suspend` graceful-stop channel, exact-PID
  forced stop (`src/lingtai/adapters/windows/refresh_watcher_process.py`).
- `WindowsAgentProcessScanAdapter` — one bounded CIM query yielding
  `(pid, command_line)` for the CLI duplicate-launch guard
  (`src/lingtai/adapters/windows/process_scan.py`).
- `WindowsAvatarLauncherAdapter` — avatar spawn with the shared creation
  flags; `terminate`/`force_terminate` are both documented-forceful
  `TerminateProcess` (`src/lingtai/adapters/windows/avatar_launcher.py`).
- `WindowsDaemonSupervisorAdapter` — detached daemon supervisor spawn with the
  inherited-handle one-shot capsule wire (`handle_list` +
  `LINGTAI_DAEMON_CAPSULE_HANDLE`), plus execution-child and resume-owner
  spawns (`src/lingtai/adapters/windows/daemon_supervisor.py`); the three
  entrypoint mirrors adopt the capsule handle to the shared fd wire and
  delegate to the mechanism-free POSIX read/dispatch logic
  (`daemon_supervisor_entrypoint.py`, `daemon_execution_child_entrypoint.py`,
  `daemon_resume_owner_entrypoint.py`).
- `process_identity` — Windows process-incarnation token
  (`windows:<creation_filetime>`) reached by delegation from
  `adapters/posix/process_identity.py`
  (`src/lingtai/adapters/windows/process_identity.py`).
- `PowerShellDialect` — PowerShell 7 command extraction, invocation shaping,
  and `state_key() == "powershell"` provenance
  (`src/lingtai/adapters/windows/powershell.py`).
- `WindowsShellAsyncProcessAdapter` — Job Object process-tree ownership:
  suspended spawn, job assignment, `NtResumeProcess`, active-process
  accounting, bounded tree cancellation, creation-time process identity
  (`src/lingtai/adapters/windows/powershell_process.py`).
- `WindowsShellStateLockAdapter` — cross-process shell job state lock via
  `msvcrt.locking` byte 0/length 1 on `<job_dir>/.state.lock`
  (`src/lingtai/adapters/windows/powershell_state_lock.py`).

## Connections

The shell adapters are selected by the outer `os.name == "nt"` branches of
`src/lingtai/adapters/shell.py`, `shell_process.py`, and
`shell_state_lock.py`. The workdir lease and refresh watcher are selected by
the `sys.platform == "win32"` branches of
`src/lingtai/adapters/workdir_lease.py` and
`src/lingtai/adapters/refresh_watcher.py`; the process scan by
`src/lingtai/adapters/process_scan.py`; the avatar launcher by the
`os.name == "nt"` branch of `src/lingtai/adapters/avatar_launcher.py`. The
Windows refresh adapter reuses the platform-neutral `build_watcher_env` from
the POSIX sibling as the single source of the env-overwrite policy
translation. Core never imports this package; composition roots reach it only
through those selectors.

## Composition

- **Parent wrapper:** `src/lingtai/ANATOMY.md`.
- **Owning contract:** `src/lingtai/kernel/workdir_lease/CONTRACT.md` (pairing
  owner); shell, avatar, refresh, and runtime promises live in their linked
  capability contracts.
- **POSIX sibling:** `src/lingtai/adapters/posix/ANATOMY.md` maps the POSIX
  implementations of the same Ports.

## State

The state lock and workdir lease own open byte-range-locked file handles while
held (`.state.lock`, `.agent.lock`). The async shell process adapter owns Job
Object handles and per-job `stdout.log`/`stderr.log` streams until release.
The refresh process adapter writes the supervised workdir's `.suspend` file as
its graceful-stop channel and appends replacement stderr to the requested log
path. The avatar launcher owns the child's stderr file handle only during
spawn. No adapter here persists state beyond those capability-owned files.

## Notes

The workdir-lease byte range (`.agent.lock`, byte 0, length 1) is a
cross-repository interop invariant with the TUI duplicate-launch probe; the
normative statement lives in `src/lingtai/kernel/workdir_lease/CONTRACT.md`.
The shell `.state.lock` uses the same mechanism but is a different,
capability-local lock — never evidence for the agent lease. Graceful process
stop on Windows is capability-defined (the refresh adapter's `.suspend`
channel); there is no deliverable SIGTERM, and `Popen.terminate()` is
forceful — adapters document that mapping instead of pretending a graceful
tier exists.
