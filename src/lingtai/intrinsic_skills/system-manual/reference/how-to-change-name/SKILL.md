---
name: how-to-change-name
description: >
  Nested system-manual reference for renaming a live POSIX agent workdir/address.
  Use after system-manual for the deliberately narrow suspend, no-replace rename,
  and resume procedure; Windows, network filesystems, and bulk address rewrites are out of scope.
version: 1.0.0
last_changed_at: "2026-07-20T00:00:00Z"
tags: [lingtai, system-manual, posix, rename]
related_files:
- src/lingtai/intrinsic_skills/system-manual/SKILL.md
- src/lingtai/intrinsic_skills/system-manual/reference/how-to-change-name/scripts/change_name.py
- src/lingtai/cli.py
- src/lingtai/kernel/base_agent/lifecycle.py
- src/lingtai/kernel/base_agent/identity.py
- tests/test_how_to_change_name.py
- tests/test_how_to_change_name_e2e.py
maintenance: |
  Keep this small POSIX procedure aligned with the executable's preflight, suspend, no-replace rename, and resume behavior.
---

# Change an agent address (POSIX v1)

This changes the workdir basename and derived address, **not** the true
`agent_name` or `agent_id`. It does not rewrite contacts, history, ledgers, or
other agents' old addresses.

## Run

Use an absolute canonical workdir path and a new safe basename:

```sh
python /path/to/change_name.py /absolute/old-address new-address
```

The default command first performs a read-only preflight, then starts a detached
supervisor so suspending the agent cannot kill the rename operation. Its log
starts at `old-address/logs/change-name.log` and moves with the directory. An
external operator can wait for the final result with `--foreground`.

## V1 contract

The helper is POSIX-only and requires a local, live agent with a fresh heartbeat,
held lease, exactly one `python -m lingtai run <old>` process, strict-JSON
`init.json`, matching `manifest.agent_name`, and an absolute `venv_path` that can
import `lingtai`. Its import probe sets `PYTHONDONTWRITEBYTECODE=1`.

It then:

1. touches `.suspend` and waits for the exact process to disappear, heartbeat to
   go stale, and lease to release; a failed `ps` scan is a failure, never proof
   that the process is absent;
2. renames once with Linux `renameat2(RENAME_NOREPLACE)` or macOS
   `renamex_np(RENAME_EXCL)`, so a destination that appears after preflight is
   never replaced;
3. rebases an in-workdir `venv_path`, changes only `.agent.json` `address`, and
   starts the rebased runtime; and
4. requires the new exact process, a post-launch heartbeat, and unchanged
   `agent_id`/`agent_name` before reporting success.

There is no copy/delete fallback, automatic rollback, venv creation, or repair
of unrelated config paths. A missing/invalid old path never writes to a
pre-existing destination.

## Recover honestly

Before rename failure leaves the old directory in place; it may be suspended, so
restart that same old path before retrying. After rename failure leaves the new
directory in place and the log there; inspect and repair that new path. Do not
retry against a missing old path or delete either directory automatically.
