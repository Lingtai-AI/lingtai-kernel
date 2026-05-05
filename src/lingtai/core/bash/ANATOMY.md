# core/bash

Bash capability — shell command execution with file-based policy. Adds the
ability to run shell commands. This is a capability (not intrinsic) because
not every agent should have shell access — it's a powerful ability that should
be explicitly opted into.

## Components

- `bash/__init__.py` — the entire capability in a single file. `get_description` (`bash/__init__.py:28-29`), `get_schema` (`bash/__init__.py:32-51`), `setup` (`bash/__init__.py:217-255`). Two core classes: `BashPolicy` (`bash/__init__.py:55-143`) for command filtering, `BashManager` (`bash/__init__.py:146-214`) for execution.
- `bash/bash_policy.json` — default denylist policy shipped with the kernel. Denies destructive (`rm`, `rmdir`, `shred`, `dd`), privilege escalation (`sudo`, `su`, `doas`), permission changes (`chmod`, `chown`, `chgrp`), disk management (`mount`, `umount`, `mkfs`, `fdisk`), package managers (`apt`, `apt-get`, `yum`, `dnf`, `brew`), process control (`kill`, `killall`, `pkill`, `shutdown`, `reboot`, `systemctl`), network (`nc`, `ncat`), and code execution (`eval`, `exec`).

## Public API

The `bash` tool exposes a single action:

| Parameter      | Type     | Description |
|----------------|----------|-------------|
| `command`      | string   | Shell command to execute (required) |
| `timeout`      | number   | Timeout in seconds (default: 30) |
| `working_dir`  | string   | Working directory for execution (default: agent's working dir) |

Returns `{status, exit_code, stdout, stderr}` on success, or `{status: "error", message}` on failure.

## Internal Module Layout

```
bash/__init__.py
  ├── BashPolicy                     — command execution policy
  │   ├── __init__(allow, deny)      — two modes: allowlist (if allow present) or denylist
  │   ├── from_file(path)            — loads policy from JSON file
  │   ├── yolo()                     — creates a policy that allows everything
  │   ├── describe()                 — human-readable summary of policy rules
  │   ├── is_allowed(command)        — checks command against policy
  │   ├── _check_single(cmd)         — checks a single command name
  │   └── _extract_commands(command) — parses pipes, chains, subshells to extract all command names
  │
  ├── BashManager                    — execution manager
  │   ├── __init__(policy, working_dir, max_output) — stores policy + config
  │   └── handle(args)               — validates policy, runs subprocess, truncates output
  │
  └── setup(agent, policy_file, yolo) — resolves policy, registers bash tool
```

## Key Invariants

- **Two policy modes:** Allowlist mode (when `allow` key is present in policy) — only listed commands permitted, everything else blocked. Denylist mode (only `deny` key) — everything allowed except denied commands. The mode is implicit.
- **Pipe-aware command extraction:** `_extract_commands()` parses `|`, `&&`, `||`, `;`, newlines, `$()`, backticks, and env-var prefixes to find every command name in a compound expression.
- **Working directory sandbox:** `working_dir` is validated to be under the agent's working directory. Paths are resolved and checked with `startswith(sandbox + "/")`.
- **Output truncation:** `max_output = 50_000` chars. Both stdout and stderr are truncated with a note showing total length.
- **Subprocess isolation:** Commands run via `subprocess.run(shell=True, capture_output=True, text=True, timeout=...)` in the agent's working directory by default.
- **Policy file location:** Default policy is `bash/bash_policy.json` (shipped with the kernel). Can be overridden via `policy_file` arg or bypassed with `yolo=True`.

## Dependencies

- `lingtai.i18n` — `t()` for localized strings
- `lingtai_kernel.base_agent.BaseAgent` — agent type (TYPE_CHECKING only)

## Composition

- **Parent:** `src/lingtai/core/` (capability package).
- **Siblings:** `daemon/`, `avatar/`, `mcp/`, `library/`, `codex/`.
- **Manual:** `bash/manual/SKILL.md` — operational guide for agents (currently focused on scheduled / cron-driven work — when to schedule, the wake-by-mailbox-drop contract, hygiene rules, OS-specific recipes for launchd / systemd / crontab, and debugging walkthroughs).
- **Kernel hooks:** `setup()` is called during capability initialization; `BashManager.handle()` is registered as the `bash` tool handler.
