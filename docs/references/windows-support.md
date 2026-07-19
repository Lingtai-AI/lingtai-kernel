---
related_files:
- src/lingtai/kernel/base_agent/CONTRACT.md
- src/lingtai/kernel/base_agent/ANATOMY.md
- src/lingtai/kernel/workdir_lease/CONTRACT.md
- src/lingtai/kernel/refresh_watcher/CONTRACT.md
- src/lingtai/tools/bash/CONTRACT.md
- src/lingtai/tools/avatar/CONTRACT.md
- src/lingtai/tools/daemon/CONTRACT.md
- src/lingtai/adapters/windows/ANATOMY.md
- .github/workflows/kernel-windows-pr.yml
- .github/workflows/shell-windows-pr.yml
maintenance: |
  Capability manual for per-platform runtime support (agent-runtime.compat.v1);
  reciprocally linked from base_agent/CONTRACT.md and base_agent/ANATOMY.md
  related_files. This matrix is the only publishable Windows-support statement:
  update the affected row (never a global flag) in the same change that adds,
  certifies, or withdraws a platform capability, and keep each row's evidence
  column pointing at real test suites/CI lanes.
---

# Windows support — capability matrix

LingTai kernel platform support is published **per capability**, never as one
all-or-nothing "Windows supported" flag. Each row names its owning Contract,
its state on native Windows, and the evidence that backs the claim. States:

- **supported** — production adapter selected on Windows; shared contract
  tests pass on both platforms; native Windows receipts exist in CI.
- **implemented / native-CI-gated** — production Windows adapter and
  cross-platform wiring/exact-argument tests are merged; the mechanism tier
  runs natively in the Windows CI lane on this repository's pull requests, so
  the row's native receipt is the PR's CI run rather than a long-lived branch
  history. Treat as supported once that lane is green on current main.
- **unsupported (fail-loud)** — no Windows implementation by design; the
  owning selector/composition gate raises with an exact reason. Never a
  silent no-op.

The normative composition rule is `agent-runtime.compat.v1` in
[`base_agent/CONTRACT.md`](../../src/lingtai/kernel/base_agent/CONTRACT.md).

| Capability | Owning contract | Windows state | Mechanism | Evidence |
|---|---|---|---|---|
| Workdir lease | `kernel/workdir_lease/CONTRACT.md` | implemented / native-CI-gated | `msvcrt` byte 0/length 1 on `.agent.lock` (TUI-probe interop range) | `tests/test_workdir_lease.py` shared suite + exact-argument pin everywhere; native contention/crash/probe tier in `kernel-windows-pr.yml` |
| Agent construction (`build_agent`) | `kernel/base_agent/CONTRACT.md` | implemented / native-CI-gated | platform selectors; portable transports | `tests/test_agent.py`, `tests/test_workdir_lease.py` CLI-root pins; native run in `kernel-windows-pr.yml` |
| Launch/stop lifecycle (CLI host) | `kernel/base_agent/CONTRACT.md` | implemented / native-CI-gated | SIGINT/SIGBREAK → `.suspend`; CIM duplicate scan | `tests/test_process_scan.py`, `tests/test_cli.py`; native lifecycle smoke in `kernel-windows-pr.yml` |
| Agent presence / heartbeat | `kernel/agent_presence/CONTRACT.md` | implemented / native-CI-gated | portable filesystem transport | `tests/test_agent_presence.py` on both platforms via CI |
| Notification store | `kernel/notification_store/CONTRACT.md` | implemented / native-CI-gated | portable atomic-replace transport | `tests/test_notification_store*.py` via CI |
| Event journal | `kernel/event_journal/CONTRACT.md` | implemented / native-CI-gated | portable append + SQLite sidecar | `tests/test_services_logging.py` et al. via CI |
| Mail transport | `kernel/mail_transport/CONTRACT.md` | implemented / native-CI-gated | portable filesystem protocol (UTF-8 writes pinned) | `tests/test_mail_transport.py`, `tests/test_filesystem_mail.py`, `tests/test_portable_adapter_encoding.py` via CI |
| Migration workspace | `kernel/migrate/CONTRACT.md` | implemented / native-CI-gated | portable PID-suffixed atomic replace | `tests/test_kernel_migrate.py` via CI |
| Git snapshot / source revision | `kernel/snapshot/CONTRACT.md` | implemented / native-CI-gated | portable `git` argv subprocess | `tests/test_snapshot.py`, `tests/test_git_init.py` via CI |
| Lifecycle clock | `kernel/lifecycle_clock/CONTRACT.md` | supported | portable `time` adapters | shared contract tests on both platforms |
| Refresh watcher / relaunch | `kernel/refresh_watcher/CONTRACT.md` | implemented / native-CI-gated | detached creation flags; CIM observe; handle liveness; `.suspend` graceful stop | `tests/test_refresh_watcher_windows.py` wiring everywhere + native tier in CI |
| CPR relaunch | `kernel/base_agent/CONTRACT.md` (`process-identity.v1`) | implemented / native-CI-gated | module-form spawn with detached creation flags; presence-asserted success | `tests/test_process_scan.py` spawn-kwargs pin; native lifecycle smoke in CI |
| Headless shell | `tools/bash/CONTRACT.md` | supported | PowerShell 7 dialect; Job Object trees; `msvcrt` state lock | `tests/test_shell_windows_native.py` in `shell-windows-pr.yml` (pre-existing lane) |
| Daemon (headless backends) | `tools/daemon/CONTRACT.md` | implemented / native-CI-gated | Job-Object private scope / exact-child inherited scope; byte-range ledger lock; inheritable-handle capsule | `tests/test_daemon_windows_*.py` wiring everywhere + native tier in CI |
| Avatar launcher | `tools/avatar/CONTRACT.md` | implemented / native-CI-gated | detached creation flags; forceful terminate mapping (documented) | `tests/test_avatar_launcher_windows.py` wiring everywhere + native tier in CI |
| Interactive terminal (ConPTY) | `tools/daemon/interactive_terminal/CONTRACT.md` | unsupported (fail-loud) | none — POSIX PTY only; ConPTY deferred by design | daemon composition injects no interactive port on Windows; interactive backends refuse with an exact reason |
| WeChat poller account lock | `mcp_servers/wechat/lockfile.py` | unsupported (fail-loud) | none — `fcntl` only | `UnsupportedPlatformError` at poller startup (GH#83 rationale) |

## Cross-repository interop

The LingTai TUI's duplicate-launch probe (TUI PR #687) checks byte 0, length 1
of `.agent.lock` with a non-creating exclusive attempt: conflict→Block,
missing→Allow, other→Unknown. The kernel's Windows lease holds exactly that
range, so a live kernel agent is observable to the TUI while leased. The
kernel-side half of the interop proof is the native byte-0 probe test in
`tests/test_workdir_lease.py`; a two-repository native run on one Windows host
remains the outstanding cross-repo receipt before the mutual minimal-lifecycle
claim (`agent-runtime.compat.v1` stays `proposed` until then).

## Reading this matrix honestly

"implemented / native-CI-gated" rows became real in one change-set developed
on POSIX: their mechanism code paths are exercised natively by
`kernel-windows-pr.yml` on pull requests touching them. A row only graduates
to "supported" wording once that lane has run green on current `main`. If a
native run falsifies a row, narrow that row (or mark it unsupported) rather
than weakening its owning contract.
