# Kill (Timeout & Process Termination)

> **Capability:** bash / kill
> **Module:** `lingtai/core/bash/__init__.py`

---

## What

When a bash command exceeds its timeout, `subprocess.run()` raises `subprocess.TimeoutExpired`. The kernel does **not** implement explicit SIGTERM → SIGKILL escalation or orphan process group cleanup — it relies entirely on Python's `subprocess.run()` timeout semantics, which send SIGKILL to the child process on timeout.

---

## Contract

### Timeout mechanism

```python
# BashManager.handle(), lines 190-212
try:
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,   # ← this is the only timeout enforcement
        cwd=cwd,
    )
    # ... return success
except subprocess.TimeoutExpired:
    return {"status": "error", "message": f"Command timed out after {timeout}s"}
except Exception as e:
    return {"status": "error", "message": f"Command failed: {e}"}
```

### What happens on timeout

1. Python's `subprocess.run()` waits for `timeout` seconds.
2. On expiry, it sends **SIGKILL** (signal 9) to the **direct child process** (the shell started by `shell=True`).
3. `subprocess.TimeoutExpired` is raised.
4. The handler catches it and returns `{"status": "error", "message": "Command timed out after {N}s"}`.

### What does NOT happen

| Behavior | Implemented? | Notes |
|---|---|---|
| SIGTERM before SIGKILL | **No** | `subprocess.run()` with `timeout` sends SIGKILL directly |
| Process group kill | **No** | No `preexec_fn=os.setsid` or `start_new_session=True`; grandchildren may survive |
| Orphan cleanup | **No** | Grandchild processes spawned by the shell are not tracked or killed |
| Grace period | **No** | No configurable grace period between SIGTERM and SIGKILL |

### Implications

- **Grandchild processes may leak.** If a command spawns background processes (e.g., `sleep 100 &`), the SIGKILL to the shell may leave the grandchild running. The kernel has no mechanism to detect or clean up these orphans.
- **No graceful shutdown.** Commands cannot intercept the timeout signal to perform cleanup (e.g., write partial results, release locks). They are hard-killed.
- **Shell=True means one extra layer.** `subprocess.run(shell=True)` spawns `/bin/sh -c "command"`. The SIGKILL hits the shell, not necessarily the command inside it (which may be a grandchild).

### Default timeout

The default is 30 seconds (from the schema default at line 43). The agent can override via the `timeout` parameter.

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Default timeout in schema | `lingtai/core/bash/__init__.py` | 43 |
| Default timeout in handle() | `lingtai/core/bash/__init__.py` | 173 |
| `subprocess.run()` with timeout | `lingtai/core/bash/__init__.py` | 190-197 |
| `TimeoutExpired` catch | `lingtai/core/bash/__init__.py` | 211-212 |
| Generic exception catch | `lingtai/core/bash/__init__.py` | 213-214 |

---

## Related

| Leaf | Relationship |
|---|---|
| `bash` (parent) | Kill is bash's timeout handling behavior |
| `bash/yolo` | Yolo allows `kill`/`killall` commands (bypasses denylist); timeout handling is unchanged |
| `bash/sandbox` | Commands killed by timeout are still within the sandbox |
