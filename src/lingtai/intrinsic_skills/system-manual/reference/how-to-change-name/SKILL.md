---
name: how-to-change-name
description: >
  Nested system-manual reference for changing a live LingTai agent workdir
  basename/address on POSIX while preserving immutable identity. Read this
  after loading system-manual when a cooperative suspend, same-parent atomic
  rename, JSONC runtime-path rebase, verified resume, or recovery is needed;
  Windows is explicitly out of scope.
version: 1.0.0
tags: [lingtai, system-manual, posix, rename, suspend, resume, recovery]
last_changed_at: 2026-07-20T00:00:00Z
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/identity.py
- src/lingtai/kernel/process_match.py
- src/lingtai/adapters/posix/workdir_lease.py
- src/lingtai/cli.py
- src/lingtai/kernel/config_resolve.py
- tests/test_how_to_change_name.py
- tests/test_how_to_change_name_additional.py
- tests/test_how_to_change_name_e2e.py
maintenance: |
  Keep this procedure aligned with lifecycle suspend semantics, POSIX lease and
  process matching, identity invariants, and the executable helper's receipts.
---

# How to change an agent name (POSIX)

This procedure changes the agent's **workdir basename and address**. It does
not change the true `agent_name` (真名) or `agent_id`; the true name is
immutable. External peer addresses, contacts, ledgers, and history are not
mass-rewritten. They may contain the old address and are reported for the
operator to update deliberately.

Windows is out of scope for this first version. Do not use this procedure on a
network filesystem or a live directory that is not the local POSIX agent
workdir.

## Command

From an already external operator (recommended):

```sh
python /path/to/change_name.py --foreground /absolute/old-basename new-basename
```

A normal invocation runs the complete read-only preflight and then hands the
operation to a detached external supervisor before creating `.suspend`:

```sh
python /path/to/change_name.py /absolute/old-basename new-basename
```

The handoff command returns after the supervisor starts. Follow the printed
supervisor log and inspect `logs/name-change.json`; the receipt is written under
the old directory for pre-rename failures and under the new directory after an
atomic rename. `--timeout` bounds shutdown and resume verification. The helper
is executable and has no Windows or generic system-tool API mode.

## What preflight proves

Before any lifecycle marker or rename, the helper checks all of the following:

- POSIX, canonical absolute existing old directory (no `..`, leaf symlink, or
  symlinked path component), safe single-segment new basename (the avatar
  policy: Unicode letters/digits/underscore/hyphen, no leading dot, max 64),
  same parent, and absent destination. If rejected, rerun with the canonical
  path printed by the helper.
- Parseable `init.json`/accepted JSONC and parseable `.agent.json`; captures a
  non-empty `agent_id` and true `agent_name`, and requires the old manifest
  address to equal the old basename.
- A fresh `.agent.heartbeat`, exactly one exact `python -m lingtai run <old>`
  (or anchored legacy console form) process, and a held POSIX `flock` on
  `.agent.lock`.
- A usable runtime, selected without calling `resolve_venv` and without
  creating, repairing, deleting, or rewriting configuration. Candidate order is
  configured `init.json` `venv_path`, inherited `LINGTAI_RUNTIME_PYTHON`, the
  current interpreter, and managed `~/.lingtai-tui/runtime/venv/bin/python`;
  each candidate must be executable and pass bounded `import lingtai`.
- Every absolute JSON string in `init.json` equal to or lexically beneath the
  old workdir is precomputed to point beneath the new workdir. Comments,
  formatting, trailing commas, relative paths, external paths, and unrelated
  files are preserved. The rewritten text is parsed before mutation. An
  in-workdir absolute `venv_path` is therefore rebased for the resumed launch.

## Supervisor phases and failure semantics

1. The detached supervisor revalidates the preflight, then touches `.suspend`.
   Suspend is cooperative: the agent consumes that marker, tears down, withdraws
   its heartbeat, and releases its lease.
2. It waits on a monotonic deadline for the exact old process to disappear, the
   heartbeat to be absent/stale, and the old lease to be acquirable. Timeout is
   a failure: the old directory remains and the receipt truthfully records that
   `.suspend` may still be present. Do not delete the marker or kill an
   unrelated PID.
3. It performs one same-parent atomic **no-replace** directory rename using
   Linux `renameat2(RENAME_NOREPLACE)` or macOS `renamex_np(RENAME_EXCL)`.
   An unavailable primitive or any destination race fails closed; an existing
   destination is never replaced. There is no copy/delete fallback, and a
   rename failure leaves the old directory intact.
4. After rename it atomically writes the precomputed init content, changes only
   the manifest `address` to the new basename (checking `agent_id` and true name
   first), and removes only stale `.suspend`, `.sleep`, `.interrupt`, and
   `.refresh` markers. Logs, history, contacts, ledgers, and external paths are
   untouched.
5. It launches detached `[rebased-runtime, -m, lingtai, run, new-absolute-dir]`
   with an append-only relaunch log, then requires the child to remain alive,
   match the exact new command, write a fresh post-launch heartbeat, and expose
   the new address with unchanged identity. A machine-readable success receipt
   includes phase, paths, PID, and log.

Any post-rename launch, heartbeat, manifest, or verification failure leaves the
new directory in place. There is **no automatic rollback and no deletion**.
The operator should inspect the receipt and relaunch log, repair only the
reported local issue, then retry against the new directory using its current
basename. A stale `.suspend` is a recovery signal, not proof of success. If the
old directory is absent and the new directory exists, treat the operation as
possibly already renamed and consult the receipt; never run a second rename.

## Recovery checklist

- Read `logs/name-change.json` and the named supervisor/relaunch log.
- For shutdown timeout, confirm the old exact PID and lease state. If the old
  process later finishes stopping, restart/CPR the **old** path and require a
  fresh heartbeat before rerunning; preflight intentionally rejects a stopped
  target.
- For an atomic rename failure, the old directory remains but its agent has
  already stopped. Fix the reported same-parent permission/conflict, CPR the old
  path, verify it is live, then rerun.
- For post-rename failure, use the new path, preserve the receipt, and fix the
  runtime/config or stale marker named by the log. First determine whether the
  new agent is already live; otherwise CPR the new path. Verify `agent_id`, true
  `agent_name`, address, and in-workdir runtime paths before any retry.
- Update peer-facing addresses deliberately after local success. Do not mass
  rewrite external contacts, ledgers, or history.
