# Bash (Shell Execution)

> **Capability:** bash
> **Module:** `lingtai/core/bash/__init__.py`

---

## What

Bash is the shell command execution capability. It runs arbitrary commands via `subprocess.run(shell=True)`, returns stdout/stderr/exit_code, and enforces a file-based allow/deny policy that parses pipes, chains, and subshells to extract every command name before checking.

Bash is a **capability** (not intrinsic) — it must be explicitly opted into because not every agent should have shell access.

---

## Contract

### Setup

```
setup(agent, policy_file=None, yolo=False) → BashManager
```

Three modes, in priority order:
1. `yolo=True` → `BashPolicy.yolo()` — no restrictions (see `bash/yolo/`)
2. `policy_file=<path>` → `BashPolicy.from_file(path)` — custom JSON policy
3. Neither → `BashPolicy.from_file(_DEFAULT_POLICY_FILE)` — default denylist at `bash_policy.json`

The resolved policy summary is appended to the tool description injected into the system prompt.

### Tool schema

| Parameter | Type | Required | Default | Notes |
|---|---|---|---|---|
| `command` | string | **yes** | — | Shell command to execute |
| `timeout` | number | no | 30 | Seconds before `TimeoutExpired` |
| `working_dir` | string | no | agent working dir | Must be under agent's working directory (see `bash/sandbox/`) |

### Return shape

**Success:**
```json
{"status": "ok", "exit_code": <int>, "stdout": "<string>", "stderr": "<string>"}
```

**Error:**
```json
{"status": "error", "message": "<reason>"}
```

Error cases: empty command, policy denial, working_dir sandbox violation, timeout, subprocess exception.

### Output truncation

Both stdout and stderr are capped at `max_output` (default 50,000 chars). If exceeded, the output is truncated with a suffix: `"\n... (truncated, {total} chars total)"`.

### Policy system

See `BashPolicy` class. Two modes:
- **Denylist mode** (only `deny` key in JSON): everything allowed except denied commands.
- **Allowlist mode** (`allow` key present): only listed commands allowed; `deny` ignored.

Command extraction (`_extract_commands`) handles: `|`, `&&`, `||`, `;`, newlines, `$(...)` subshells, backtick subshells, and env-var prefix skipping (`FOO=bar cmd` → checks `cmd`).

### Execution

`subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)` — the command runs as a shell subprocess in the resolved working directory.

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Module docstring + usage | `lingtai/core/bash/__init__.py` | 1-10 |
| Default policy file constant | `lingtai/core/bash/__init__.py` | 26 |
| Schema definition | `lingtai/core/bash/__init__.py` | 32-51 |
| `BashPolicy` class | `lingtai/core/bash/__init__.py` | 55-143 |
| `BashPolicy.yolo()` | `lingtai/core/bash/__init__.py` | 81-83 |
| `BashPolicy.is_allowed()` + `_extract_commands()` | `lingtai/core/bash/__init__.py` | 99-143 |
| `BashManager` class + `handle()` | `lingtai/core/bash/__init__.py` | 146-214 |
| Working dir sandbox validation | `lingtai/core/bash/__init__.py` | 176-187 |
| `subprocess.run` call | `lingtai/core/bash/__init__.py` | 190-197 |
| Output truncation | `lingtai/core/bash/__init__.py` | 200-203 |
| `setup()` entry point | `lingtai/core/bash/__init__.py` | 217-255 |
| Default policy JSON | `lingtai/core/bash/bash_policy.json` | full file |

---

## Related

| Leaf | Relationship |
|---|---|
| `bash/yolo` | Yolo mode: `BashPolicy.yolo()` — no allow, no deny, everything permitted |
| `bash/sandbox` | Working directory containment: `working_dir` must resolve under agent's dir |
| `bash/kill` | Timeout behavior: `subprocess.TimeoutExpired` on timeout, no explicit kill cascade |
| `codex` | Separate capability; codex is knowledge storage, bash is execution |
