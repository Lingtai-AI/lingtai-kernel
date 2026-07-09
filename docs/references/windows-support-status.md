# Native Windows / PowerShell support status

**Status: PR-candidate stack — not merged, not released.**

This page tracks the state of running the LingTai kernel and CLI natively on
Windows (real `powershell.exe` / ConPTY, not WSL or Cygwin). It exists so that
developers and users can see honestly what is covered today, what is only
partially covered, and what still needs validation on a real native Windows host.

Read this before assuming a workflow works on Windows.

## What "native Windows" means here

- **Native Windows** — a Windows Python interpreter driving Windows shells
  (`powershell.exe`) and Windows process/console APIs directly.
- **Not covered by this page** — WSL, Cygwin, MSYS, or Git-Bash environments.
  Those present a POSIX-ish surface and are closer to the Linux path; they are
  out of scope for the claims below.

## Headline caveat

At the time of writing (2026-07-09), every item below lives in an **open
pull-request stack** (candidates `#810`–`#821`). None of it is merged to `main`
or shipped in a released `lingtai` package. Treat this as **"support is being
built,"** not **"support is available."** Do not cite this page as a guarantee
of Windows compatibility for a released version. If the stack is merged or
released, update this page before claiming support.

There is also **no full end-to-end validation on a real native Windows host yet.**
Most of the stack is verified with unit tests that monkeypatch or fake the
Windows APIs (`msvcrt`, ConPTY, process-control calls). The one exception is the
bash smoke suite (see `#816` below), which runs real PowerShell on a GitHub
`windows-latest` runner. Fake-API tests confirm the code *branches* correctly on
Windows; they do not prove behavior against the real OS.

## Support matrix

Legend:

- **Candidate** — implemented in an open PR, verified only by unit tests that
  fake/monkeypatch the Windows APIs. Not validated on real Windows.
- **Candidate (CI-validated)** — implemented in an open PR and exercised by real
  PowerShell on a `windows-latest` CI runner.
- **Not supported** — no native-Windows path exists yet.
- **Needs real-Windows validation** — applies to every row: no manual end-to-end
  run on a real native Windows host has been signed off.

| Area | Native Windows behavior | Status | Source |
|---|---|---|---|
| Daemon import / module load | Daemon package imports and loads on native Windows without POSIX-only assumptions | Candidate | `#810` |
| Headless daemon CLI backends | Headless daemon CLI backends run on native Windows | Candidate | `#812` |
| Interactive Claude console | Interactive Claude uses a ConPTY / `pywinpty` path on Windows | Candidate | `#814` |
| Agent-facing bash shell | Bash tool runs commands via explicit `powershell.exe`, with Windows-safe async / cancel / process handling | Candidate (CI-validated) | `#815`, `#816` |
| Bash smoke suite | Real PowerShell: sync stdout capture, async poll-to-done, cancel of `Start-Sleep`, nested `working_dir` | Candidate (CI-validated) | `#816` |
| Lifecycle / detached spawn | Detached-spawn and process-control safety helper for Windows | Candidate | `#817` |
| Glob separators | Forward-slash / Windows separator normalization in glob patterns | Candidate | `#818` |
| Scheduled work docs | Windows Task Scheduler guidance in the bash manual | Candidate (docs) | `#820` |
| WeChat poller lock | `msvcrt`-based poller lock for the WeChat MCP server on Windows | Candidate | `#821` |
| Full end-to-end run on real Windows | A packaged agent booting and running on a real Windows machine | Not supported / unvalidated | — |

The exact `#816` CI coverage is: on `windows-latest` with Python 3.12, real
PowerShell passes `test_sync_powershell_captures_stdout`,
`test_async_powershell_completes_and_poll_returns_output`,
`test_cancel_long_sleep_returns_cancelled`, and
`test_nested_working_dir_is_accepted_on_windows_paths`
(see `.github/workflows/windows-smoke.yml` and
`tests/test_windows_native_bash_smoke.py`).

## Acceptance checklist (before claiming native Windows is supported)

This is the bar for turning "candidate" into "supported." At the time of
writing, none of these is met on `main`.

- [ ] The `#810`–`#821` stack is merged to `main`.
- [ ] The changes ship in a released `lingtai` package version.
- [ ] Daemon import/load verified on a real Windows machine, not just fake-API tests.
- [ ] Headless daemon CLI backends run through a real agent boot on real Windows.
- [ ] Interactive Claude driven through a real ConPTY / `pywinpty` session on real Windows.
- [ ] Bash async / cancel / nested `working_dir` confirmed against real PowerShell
      beyond the four CI smoke cases (broader command coverage, error paths).
- [ ] Lifecycle detached-spawn / process control exercised against real Windows
      process APIs, including teardown and orphan cleanup.
- [ ] Glob normalization checked against real Windows paths (drive letters, UNC).
- [ ] WeChat poller lock validated with real `msvcrt` under contention on Windows.
- [ ] An end-to-end run — boot an agent, run tools, exchange mail — completed on a
      real Windows host and its result recorded here.

## Non-goals

- This page does not promise WSL, Cygwin, MSYS, or Git-Bash support.
- It does not claim performance parity with Linux/macOS.
- It does not commit to a release date for native Windows support.
- Passing the fake-API unit tests is explicitly **not** treated as proof of
  real-Windows behavior.

## How to help

If you can run this stack on a real Windows machine, the most useful
contribution is validation evidence against the acceptance checklist above:
what you ran, on which Windows version, and the exact output. See
[`SUPPORT.md`](../../SUPPORT.md) for what to include when reporting.
