# C1 Patch — `status()` 缺 `state` 字段

**Bug**：REAL-BUGS.md C1
**文件**：`src/lingtai_kernel/base_agent.py`
**提议者**：lingtai-expert
**日期**：2026-04-30

---

## 1. 改前

`base_agent.py` 行 1756-1770（今日核）：

```python
        return {
            "identity": {
                "address": str(self._working_dir),
                "agent_name": self.agent_name,
                "mail_address": mail_addr,
            },
            "runtime": scrub_time_fields(
                self,
                {
                    "current_time": now_iso(self),
                    "started_at": self._started_at,
                    "uptime_seconds": round(uptime, 1),
                    "stamina": self._config.stamina,
                    "stamina_left": round(stamina_left, 1) if stamina_left is not None else None,
                },
                keys=("current_time", "started_at", "uptime_seconds", "stamina", "stamina_left"),
            ),
```

---

## 2. 改后

```python
        return {
            "identity": {
                "address": str(self._working_dir),
                "agent_name": self.agent_name,
                "mail_address": mail_addr,
            },
            "runtime": scrub_time_fields(
                self,
                {
                    "current_time": now_iso(self),
                    "started_at": self._started_at,
                    "uptime_seconds": round(uptime, 1),
                    "stamina": self._config.stamina,
                    "stamina_left": round(stamina_left, 1) if stamina_left is not None else None,
                    "state": self._state.value,
                },
                keys=("current_time", "started_at", "uptime_seconds", "stamina", "stamina_left"),
            ),
```

**变更**：仅新增一行 `"state": self._state.value,`（行 1768 位置，在 `"stamina_left"` 之后）。

---

## 3. 理由

**为何加于 `runtime` 块？**

- `state`（ACTIVE/IDLE/ASLEEP/STUCK/SUSPENDED）是运行时动态属性，与 `stamina_left`、`uptime_seconds` 等同属"此刻之态"。
- 同文件 `_build_manifest()` 在行 1503 已含 `"state": self._state.value`，但 `status()` 缺失。两处应一致。
- 加于 `runtime` 块而非顶层，保持 `{identity, runtime, tokens}` 三段结构整洁。

**`scrub_time_fields` 安全性？**

- `scrub_time_fields` 仅处理 `keys=()` 中列出的时间字段。`"state"` 不在此列，会原样透传。无需改 `scrub_time_fields`。

---

## 4. 影响验证

### 何处用 `status()` 返回？

今日 grep 确认，`status()` 仅两处调用：

| 调用点 | 位置 | 用途 |
|---|---|---|
| `self.status()` | `base_agent.py:1696` | 写 `.status.json` |
| `agent.status()` | `intrinsics/system.py:88` | `system("show")` handler |

### 加 `state` 是否破现有调用？

- **`.status.json`**：JSON 写入，消费者（portal、外部监控）读 dict。新增字段为纯加法，不破任何现有字段。
- **`system("show")`**：handler 直接 `json.dumps(result)` 返回给 agent。agent 收到的 JSON 多一个字段，LLM 会自然读取。不破。
- **无 test 需改**：现有 test 不检查 `status()` 返回的完整字段集（test-core-init 只验 `.agent.json`）。

### 需否连改 system intrinsic？

否。`intrinsics/system.py:88` 只是透传 `agent.status()` 的返回值，无需改。

---

## 5. 改动 scope

- **仅此一文一处**：`base_agent.py` 行 1768 位置加 1 行。
- **无需连改** system intrinsic、.agent.json、.status.json 或 test。
- **总改动**：1 文件，1 行。

---

## 6. 验证方式

改后可运行：

```python
# 在 agent 运行时调用
result = agent.status()
assert "state" in result["runtime"]
assert result["runtime"]["state"] in ("active", "idle", "asleep", "stuck", "suspended")
```

或直接 `system("show")` 检查返回 JSON 含 `runtime.state`。
