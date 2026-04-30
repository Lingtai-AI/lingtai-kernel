# YOLO Mode (Unrestricted Execution)

> **Capability:** bash / yolo
> **Module:** `lingtai/core/bash/__init__.py`

---

## What

Yolo mode removes all command restrictions. When enabled, `BashPolicy.is_allowed()` always returns `True` — every command, including `rm`, `sudo`, `kill`, `eval`, is permitted without question.

Yolo mode is the "nuclear option" for agents that need full shell access. It bypasses both denylist and allowlist policies entirely.

---

## Contract

### Activation

```python
# In setup():
BashManager(policy=BashPolicy.yolo(), working_dir=...)
```

Yolo is activated in one of two ways:
1. **Explicit**: `agent.add_capability("bash", yolo=True)` in init config.
2. **Implicit**: If an agent's `init.json` has `capabilities.bash` with `{yolo: true}`.

### `BashPolicy.yolo()` internals

```python
@classmethod
def yolo(cls) -> "BashPolicy":
    return cls()  # allow=None, deny=None
```

Returns a `BashPolicy` with both `_allow` and `_deny` set to `None`. In `is_allowed()`:

```python
def is_allowed(self, command: str) -> bool:
    if self._allow is None and self._deny is None:
        return True    # ← yolo lands here
```

### Policy description

`BashPolicy.describe()` returns empty string when both allow and deny are None. So the tool description in system prompt has **no policy summary** appended — the agent is not warned about restrictions because there are none.

### Security implications

| Aspect | Yolo | Default policy |
|---|---|---|
| `rm -rf /` | **Allowed** | Blocked (denylist: `rm`) |
| `sudo` | **Allowed** | Blocked |
| `eval` / `exec` | **Allowed** | Blocked |
| `kill`, `killall` | **Allowed** | Blocked |
| `apt`, `brew` | **Allowed** | Blocked |
| `nc`, `ncat` | **Allowed** | Blocked |
| Package install | **Allowed** | Blocked |

Yolo does **not** change the working directory sandbox (`bash/sandbox/`) — `working_dir` validation still applies even in yolo mode. The sandbox is a separate enforcement layer.

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| `BashPolicy.__init__` (allow=None, deny=None) | `lingtai/core/bash/__init__.py` | 66-69 |
| `BashPolicy.yolo()` factory | `lingtai/core/bash/__init__.py` | 81-83 |
| `is_allowed()` early return when both None | `lingtai/core/bash/__init__.py` | 104-105 |
| `describe()` empty return | `lingtai/core/bash/__init__.py` | 86-88 |
| `setup()` yolo branch | `lingtai/core/bash/__init__.py` | 235-236 |
| Default policy file (what yolo bypasses) | `lingtai/core/bash/bash_policy.json` | full file |

---

## Related

| Leaf | Relationship |
|---|---|
| `bash` (parent) | Yolo is one of three policy modes; parent documents the full policy system |
| `bash/sandbox` | Working dir containment still applies in yolo mode |
| `bash/kill` | Yolo allows `kill`/`killall` commands (they're in the default denylist) |
