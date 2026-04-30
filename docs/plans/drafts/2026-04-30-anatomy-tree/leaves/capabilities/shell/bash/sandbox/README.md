# Sandbox (Working Directory Containment)

> **Capability:** bash / sandbox
> **Module:** `lingtai/core/bash/__init__.py`

---

## What

The sandbox restricts where bash commands can execute. Every `working_dir` parameter — whether explicitly passed or defaulting to the agent's own directory — is validated to ensure it resolves to a path **under** the agent's working directory. This prevents agents from executing commands in arbitrary filesystem locations.

The sandbox is a **separate enforcement layer** from the command policy (allow/deny). It applies regardless of policy mode — even yolo mode cannot escape the sandbox.

---

## Contract

### Validation logic

```python
# BashManager.handle(), lines 176-187
resolved = str(Path(cwd).resolve())
sandbox  = str(Path(self._working_dir).resolve())
if not (resolved == sandbox or resolved.startswith(sandbox + "/")):
    return {
        "status": "error",
        "message": f"working_dir must be under agent working directory: {self._working_dir}",
    }
```

### Rules

1. **Default**: When `working_dir` is omitted from the tool call, `cwd` defaults to `self._working_dir` (the agent's working directory). This always passes validation trivially.

2. **Explicit subdirectory**: Paths like `{agent_dir}/subdir` resolve and pass `startswith(sandbox + "/")`.

3. **Exact match**: The agent's own directory passes (`resolved == sandbox`).

4. **Outside the sandbox**: Any path that does not resolve to a prefix of the sandbox path is rejected with `status: "error"`.

5. **Symlinks**: `Path.resolve()` follows symlinks before comparison. A symlink inside the sandbox pointing outside will be rejected.

6. **Trailing slashes**: `Path.resolve()` normalizes paths, so `"/foo/bar/"` and `"/foo/bar"` compare identically.

### What's blocked

| `working_dir` | Resolves to | Result |
|---|---|---|
| *(omitted)* | agent dir | ✅ allowed |
| `{agent_dir}/subdir` | under agent dir | ✅ allowed |
| `/tmp` | outside agent dir | ❌ error |
| `{agent_dir}/../../../etc` | outside agent dir | ❌ error |
| symlink → `/tmp` | follows to `/tmp` | ❌ error |

### Error response

```json
{
    "status": "error",
    "message": "working_dir must be under agent working directory: /path/to/agent"
}
```

### Edge case: invalid paths

If `Path(cwd).resolve()` raises `ValueError` or `OSError`, a generic `"Invalid working_dir path"` error is returned (lines 186-187).

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Default cwd assignment | `lingtai/core/bash/__init__.py` | 174 |
| Path resolution + sandbox check | `lingtai/core/bash/__init__.py` | 177-185 |
| Invalid path error handling | `lingtai/core/bash/__init__.py` | 186-187 |
| `BashManager.__init__` stores working_dir | `lingtai/core/bash/__init__.py` | 149-157 |
| `setup()` passes agent dir to manager | `lingtai/core/bash/__init__.py` | 244-247 |

---

## Related

| Leaf | Relationship |
|---|---|
| `bash` (parent) | Sandbox is one of bash's three sub-behaviors |
| `bash/yolo` | Yolo bypasses command policy but NOT sandbox — sandbox is orthogonal |
| `bash/kill` | Timeout kills happen within the sandboxed working directory |
