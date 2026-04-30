# C2 Patch — error format 统一（4 文件 13 site）

**Bug**：REAL-BUGS.md C2
**文件**：`src/lingtai/core/write/__init__.py`、`edit/__init__.py`、`glob/__init__.py`、`grep/__init__.py`
**提议者**：lingtai-expert
**日期**：2026-04-30

---

## 0. 关键发现：tool_executor 漏捕错误

**此非仅格式美观问题——有功能后果。**

`tool_executor.py:194-196`：
```python
if isinstance(result, dict) and result.get("status") == "error":
    err_msg = result.get("message", "unknown error")
    collected_errors.append(f"{tc.name}: {err_msg}")
```

同文件行 380 并行路径亦然。

当 write/edit/glob/grep 返回 `{"error": "..."}` 时，tool_executor **不识别为错误**，`collected_errors` 静默漏过。虽然工具结果本身仍返予 LLM，但内核的错误追踪机制失灵。

`mail.py` 亦用 `{"error": ...}` 格式（行 106/388/390/410/452/475/480/502），同病。但人之令限于四文件工具，mail 另议。

---

## 1. 成功/失败路径格式对照

| 工具 | 成功返回 | 含 `status`? | 失败返回 |
|---|---|---|---|
| **read** | `{"content": ..., "total_lines": ..., "lines_shown": ...}` | ❌ | `{"status": "error", "message": ...}` ✅ |
| **write** | `{"status": "ok", "path": ..., "bytes": ...}` | ✅ | `{"error": ...}` ❌ |
| **edit** | `{"status": "ok", "replacements": ...}` | ✅ | `{"error": ...}` ❌ |
| **glob** | `{"matches": ..., "count": ...}` | ❌ | `{"error": ...}` ❌ |
| **grep** | `{"matches": ..., "count": ..., "truncated": ...}` | ❌ | `{"error": ...}` ❌ |
| **bash** | `{"status": "ok", "exit_code": ..., "stdout": ..., "stderr": ...}` | ✅ | `{"status": "error", "message": ...}` ✅ |

---

## 2. 改前/改后（每文件一段）

### 2.1 `write/__init__.py`

**改前**（行 40, 47）：
```python
            return {"error": "file_path is required"}
            return {"error": f"Cannot write {path}: {e}"}
```

**改后**：
```python
            return {"status": "error", "message": "file_path is required"}
            return {"status": "error", "message": f"Cannot write {path}: {e}"}
```

### 2.2 `edit/__init__.py`

**改前**（行 41, 50, 52, 55, 57, 65）：
```python
            return {"error": "file_path is required"}
            return {"error": f"File not found: {path}"}
            return {"error": f"Cannot read {path}: {e}"}
            return {"error": f"old_string not found in {path}"}
            return {"error": f"old_string found {count} times — use replace_all=true or provide more context"}
            return {"error": f"Cannot write {path}: {e}"}
```

**改后**：
```python
            return {"status": "error", "message": "file_path is required"}
            return {"status": "error", "message": f"File not found: {path}"}
            return {"status": "error", "message": f"Cannot read {path}: {e}"}
            return {"status": "error", "message": f"old_string not found in {path}"}
            return {"status": "error", "message": f"old_string found {count} times — use replace_all=true or provide more context"}
            return {"status": "error", "message": f"Cannot write {path}: {e}"}
```

### 2.3 `glob/__init__.py`

**改前**（行 39, 47）：
```python
            return {"error": "pattern is required"}
            return {"error": f"Glob failed: {e}"}
```

**改后**：
```python
            return {"status": "error", "message": "pattern is required"}
            return {"status": "error", "message": f"Glob failed: {e}"}
```

### 2.4 `grep/__init__.py`

**改前**（行 42, 67）：
```python
            return {"error": "pattern is required"}
            return {"error": f"Grep failed: {e}"}
```

**改后**：
```python
            return {"status": "error", "message": "pattern is required"}
            return {"status": "error", "message": f"Grep failed: {e}"}
```

---

## 3. 推荐方案：A 选

**A 选**（推荐）：失败用 `{"status": "error", "message": ...}`，成功保持原状不加 `"status": "ok"`。

理由：
- `tool_executor.py:194,380` 用 `result.get("status") == "error"` 检测错误。统一失败格式即修复 tool_executor 的漏捕。
- 成功路径加不加 `"status": "ok"` 是独立决策，不影响 tool_executor（它只关心 `== "error"`）。
- read 的成功路径无 `status` 但有 `content`；glob/grep 有 `matches`——成功格式各具特色，无需强统一。
- bash/system/mail 的成功路径已有 `status: "ok"`——但它们是不同复杂度的工具，不必强求文件工具跟进。

**B 选**（次优）：仅改失败路径——此即 A 选之实质。

**C 选**（反向统一）：失败改回 `{"error": ...}`——但会破坏 tool_executor 的错误检测，且 bash/system 已用 `{status: "error"}`，反向更乱。

**结论**：A 选。

---

## 4. 调用者破坏验证

### 4.1 tool_executor（内核）

`tool_executor.py:194-196` 和 `380` 均用 `result.get("status") == "error"` 检测错误。

**改后影响**：write/edit/glob/grep 的错误现在会被正确捕获入 `collected_errors`。**这是修复，不是破坏。**

### 4.2 agent prompt（LLM 消费者）

LLM 看到的是工具返回的 JSON dict。当前它看到 `{"error": "file_path is required"}`，改后看到 `{"status": "error", "message": "file_path is required"}`。LLM 两种格式都能理解，且 `{status, message}` 格式更清晰。

bash/system 已用此格式，LLM 已熟悉。**无破坏。**

### 4.3 现有 test

今日 grep 全部 tests/ 目录中 `"error"` 出现于 27 个测试文件。但细查后：

- **无 test 断言 write/edit/glob/grep 的 error dict shape**——这些 test 断言的 `"error"` 来自 mail、karma、library、avatar、daemon 等其他工具。
- `test_agent.py:253` 断言 `"error" in result` 但那是 mail（`test_mail_read_no_ids_returns_error`）。
- `test_library.py:223` 断言 `"error" in result` 但那是 library。
- 无任何 `test_file*`、`test_write*`、`test_edit*`、`test_glob*`、`test_grep*` 断言错误格式。

**结论：改后不破任何现有 test。**

---

## 5. 改动 scope

- **仅返回 dict 之改**：4 文件，13 site，每 site 改一行。
- **不连改** system prompt 之 capability 描述（tool schema 不含 error format）。
- **不连改** `mail.py`（人之令限于四文件工具，mail 同病但另议）。
- **不连改** i18n（`message` 字段的文本已是英文，read/bash 同用英文错误消息）。
- **总改动**：4 文件，13 行（每行 `{"error": X}` → `{"status": "error", "message": X}`）。

---

## 6. 实施建议

四文件 13 site 皆为机械替换：`{"error":` → `{"status": "error", "message":`。可逐文件 sed 或手工 edit。

顺序建议：先 write（2 site）→ glob（2 site）→ grep（2 site）→ edit（6 site）。

改后可跑 `python -m pytest tests/ -x -q` 确认无回归。
