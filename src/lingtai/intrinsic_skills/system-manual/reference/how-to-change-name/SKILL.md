---
name: how-to-change-name
description: >
  Nested system-manual reference for renaming one POSIX agent workdir/address.
  Use through the Project CLI after stopping known external writers; Windows,
  network filesystems, and bulk address rewrites are out of scope.
version: 1.1.0
last_changed_at: "2026-08-27T00:00:00Z"
tags: [lingtai, system-manual, posix, rename]
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py
- src/lingtai/cli.py
- src/lingtai/cli_project.py
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/identity.py
- tests/test_how_to_change_name.py
- tests/test_how_to_change_name_e2e.py
- tests/test_project_creation.py
maintenance: |
  Keep this small POSIX procedure aligned with the executable's preflight,
  suspend, no-replace rename, and resume behavior. Preserve truthful recovery:
  this procedure must never claim success before restart evidence is complete.
---

# Change an agent address (POSIX v1)

This changes exactly one workdir basename and its derived address. It does
**not** change the true `agent_name`, `agent_id`, or nickname, and it does not
rewrite contacts, history, ledgers, or other agents' old addresses.

## Public Project CLI

Use the foreground Project-management command with an absolute canonical
workdir and a safe new basename:

```text
lingtai-agent project rename --agent-dir /absolute/old-address --new-address new-address --no-known-external-writers [--timeout SECONDS]
```

`--no-known-external-writers` is an explicit operator confirmation that any
separately launched MCP/LICC or other process that can write using the old
absolute path has been stopped. Do not pass it while such a writer is known.
The command also refuses another visible local command line carrying the old
path, but a process-table scan cannot prove arbitrary environment-only writers
are absent. The command waits in the foreground and returns success only after
its restart proof completes; it does not emit a JSON success shortcut.

The bundled helper is the same implementation. Its default starts a detached
supervisor and therefore reports only that the supervisor started, not rename
success. Use `--foreground` when the caller must await terminal evidence:

```sh
python /path/to/change_name.py /absolute/old-address new-address \
  --no-known-external-writers --foreground
```

## V1 contract

This slice is POSIX-only and supports only Linux `renameat2(RENAME_NOREPLACE)`
or macOS `renamex_np(RENAME_EXCL)`. Unsupported platforms or unavailable native
primitives refuse **before** `.suspend` is touched. It requires:

- a canonical non-symlink source directory and an absent, non-symlink sibling
  destination whose basename is one non-dot letters/digits/underscore/hyphen
  segment of at most 64 characters;
- non-symlink identity/config/liveness/lease files, strict-JSON `init.json`, a
  matching `manifest.agent_name`, an absolute `venv_path`, and a runtime that
  imports `lingtai` without writing bytecode;
- exactly one live `python -m lingtai run <old>` process, a fresh heartbeat, and
  a genuinely held exclusive lease; process-scan failure or no quiescence is a
  refusal, never proof of absence; and
- no nonterminal, malformed, symlinked, or recovery-marked detached daemon run
  under the agent's own `daemons/` state.

After preflight it touches `.suspend`, waits for the exact runtime to disappear,
heartbeat to become stale, and a freshly acquired lease to remain held through
one native no-replace directory move. It then rebases only an in-workdir
`venv_path`, changes only `.agent.json.address`, removes `.suspend`, and starts
the new workdir. Success additionally requires the new exact process, a fresh
post-launch heartbeat, the new manifest address, and unchanged `agent_id` and
`agent_name`.

There is no copy/delete fallback, overwrite, automatic rollback, venv creation,
JSONC rewrite, alias/forwarding address, generic filesystem rename API, or
rewrite of unrelated paths. Old peer addresses intentionally stop resolving.

## Recover honestly

A failure before the native directory move leaves the old directory in place; it
may be suspended, so restart that same old path before a separately chosen retry.
A failure after the move leaves the new directory and its log at
`new-address/logs/change-name.log`; inspect and repair that new path. A CLI
launch failure or any nonzero helper outcome is not success: inspect the
retained path(s), do not silently retry, and do not delete either directory.
