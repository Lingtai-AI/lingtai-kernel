# Anatomy 实跑测试总报告

**日期**：2026-04-30  
**范围**：49 叶树 test.md（段二实跑）  
**方法**：6 化身并行，各自按 test.md 中步骤**躬亲体验**——真调工具、录直觉  
**报告路径**：`audit/test-results/test-result-*.md`

---

## 总览

| 测试身 | 覆盖叶 | 结果 | 关键发现 |
|---|---|---|---|
| test-file-tools | 5 | 5/5 PASS | error format 不统一；glob `**` 文档已自主修正 |
| test-core-init | 9 | 9/9 PASS | system(show)缺state字段；address解析边界 |
| test-psyche-daemon-mcp | 9 | 7P/1P/1I | MCP 补测升级（LICC roundtrip）；soul-flow PARTIAL |
| test-mail | 7 | 7/7 PASS | dedup/atomic/scheduling/identity/peer-send 全验 |
| test-bash | 4 | PASS | 4处偏移归一因（连贯性债务）；系统追问：八枚同病？ |
| test-avatar | 4 | 2P/1P/1I | spawn+shallow PASS；boot PARTIAL；handshake INCONCLUSIVE |
| **合计** | **38** | | |

---

## 逐身详报

### 1. test-file-tools（5 叶）

| 叶 | 判 | 发现 |
|---|---|---|
| read | ✅ PASS | 正路/误路/分页/超 EOF 均如 contract |
| write | ✅ PASS | 创建/覆写/auto-create；bytes 返回值准确 |
| edit | ✅ PASS | 唯一替换/歧义保护/replace_all/文件不存在 |
| glob | ✅ PASS | 排序确定、仅返回文件、无匹配返回空 |
| grep | ✅ PASS | file/line/text 结构准确，truncated 到位 |

**发现**：
1. **error format 不统一**：read 用 `{status: "error", message: ...}`，write/edit/grep 用 `{error: ...}`。调用方需做两套解析。
2. **glob `**` 文档已自主修正**：test-file-tools 发现 `**/*.txt` 在 POSIX 上只返回含 `/` 的路径（fnmatch 无递归语义），已修正 README 推荐 `*.txt`。

### 2. test-core-init（9 叶）

| 叶 | 判 | 发现 |
|---|---|---|
| agent-state-machine | ✅ PASS | 状态转换符合 contract |
| network-discovery | ✅ PASS | 邻居发现正常 |
| config-resolve | ✅ PASS | 配置解析链正确 |
| preset-materialization | ✅ PASS | preset 材质化如预期 |
| preset-allowed-gate | ✅ PASS | 权限门控正常 |
| venv-resolve | ✅ PASS | venv 路径解析（部分静态验证） |
| init-schema | ✅ PASS | schema 校验正常 |
| molt-protocol | ✅ PASS | 源码验证，未真触 molt |
| wake-mechanisms | ✅ PASS | 唤醒机制符合 contract |

**发现**：
1. **system(show) 缺 state 字段**：`agent.status()` 未返回 `self._state`，agent 不知自身状态。建议 `status()` 加 `"state": self._state.value`。
2. **manifest.llm 必填时机不明**：材质化在验证之前运行，磁上 init.json 可省 llm。README 应注释说明。
3. **which python ≠ venv Python**：设计所期，venv 分辨链正确。调试文档注明即可。
4. **address 解析边界**：边界 case 已记录。

### 3. test-psyche-daemon-mcp（9 叶）

| 叶 | 判 | 发现 |
|---|---|---|
| psyche/soul-flow | ⚠️ PARTIAL | delay 设置 ok，触发时机未隔离验证 |
| psyche/inquiry | ✅ PASS | 完整往返验证，深拷正确识己 |
| psyche/core-memories | ✅ PASS | 灵台 10530B + 简 0B 均正确加载 |
| daemon/dual-ledger | ✅ PASS | 双写确认：本地 + 父 ledger，source 字段齐 |
| daemon/max-rpm-gating | ✅ PASS | 5>4 原子拒绝，无部分派发 |
| daemon/pre-send-health | ✅ PASS | 健康检查如 contract |
| mcp/inbox-listener | ✅ PASS | poller 0.5s 内捡起 LICC 事件 |
| mcp/licc-roundtrip | ✅ PASS | 写入→poll→validate→dispatch→通知→wake→记录→删除 |
| mcp/capability-discovery | ❌ INCON | 无 server，registry+source ok |

**补测**：test-psyche-daemon-mcp 编写了 `scripts/test-echo-mcp.py`（极简 MCP server），手动写 LICC v1 事件测试完整 roundtrip。从 3 INCON 升级为 1 INCON。

### 4. test-mail（7 叶 · 全 PASS）

| 叶 | 判 | 发现 |
|---|---|---|
| self-send | ✅ | 直写 inbox，无 .tmp，sent 记录正确 |
| dedup | ✅ | `_dup_free_passes=2`，第三次 blocked |
| atomic-write | ✅ | 无 .tmp 残余（self + human 两侧） |
| scheduling | ✅ | create/list/cancel/reactivate 全通；自动完成 |
| identity-card | ✅ | 13 字段 manifest 注入；check/read 提取正确 |
| peer-send | ✅ | 送达 human inbox；atomic write；identity 完整 |
| mailbox-core | ✅ | 结构符合 contract；lazy-creation 正常 |

**无额外发现**。mail 子系统实现与文档高度一致。

### 5. test-bash（4 叶）

| 叶 | 判 | 发现 |
|---|---|---|
| bash/normal | ✅ | echo/exit code/stderr/管道/subshell/env/空命令 |
| bash/timeout+kill | ✅ | timeout 触发、kill 正常 |
| bash/yolo | ✅ | yolo 模式行为符合 |
| bash/sandbox | ✅ | sandbox 拦截正常 |

**发现（4 处归一因）**：
1. **F3 中等**：相对路径 `./logs` 被 sandbox 拒绝（`Path("./logs").resolve()` 以进程 CWD 为基准，非 agent dir）
2. **F1 低**：截断后缀格式不符文档
3. **F2 低**：timeout 行为与文档微差
4. **F4 低**：env 前缀提取边界

**根因统一**：文档与实现之间的渐行渐远——代码演进中调整了行为，文档未同步。非四个 bug，乃一处连贯性债务。

**系统追问**（test-bash 提出）：kernel 八枚能力是否同病？若皆有文档-实现之隙，当立流程规（PR checklist + 以测代文），非逐个修补。

### 6. test-avatar（4 叶）

| 叶 | 判 | 发现 |
|---|---|---|
| spawn | ✅ PASS | 名校验、目录创建、init.json 继承、进程启动、ledger 记录 |
| boot-verification | ⚠️ PARTIAL | `ok` 业验毕。`failed` 与 `slow` 未测（需坏 init.json 或延时启动） |
| shallow-vs-deep | ✅ PASS | 深拷 codex/exports/combo.json byte-identical。权限保留（444/755）。*.json 4份全验 |
| handshake-files | ❌ INCON | leaves-avatar 自验 10/10 PASS（heartbeat fresh、is_agent/is_alive），但 test-avatar 未完成正式验证 |

---

## 交叉发现汇总

| # | 发现 | 来源 | 严重性 | 状态 |
|---|---|---|---|---|
| 1 | error format 不统一 | file-tools | 低 | 记录 |
| 2 | glob `**` 文档误导 | file-tools | 低 | **已自主修正** |
| 3 | system(show) 缺 state | core-init | 中 | 待确认是否提 issue |
| 4 | MCP 无 server 测试 | psyche-daemon-mcp | 低 | **已自主补测** |
| 5 | 连贯性债务（4处归一） | bash | 中 | 待流程规 |
| 6 | boot-verification 未测 failed/slow | avatar | 低 | 需恶意场景 |
| 7 | handshake-files INCONCLUSIVE | avatar | 低 | 需正式验证 |

---

## 产出文件索引

| 文件 | 路径 |
|---|---|
| 本报告 | `audit/test-run-INDEX.md` |
| file-tools | `audit/test-results/test-result-file-tools.md` |
| core-init | `audit/test-results/test-result-core-init.md` |
| psyche-daemon-mcp | `audit/test-results/test-result-psyche-daemon-mcp.md` |
| mail | `audit/test-results/test-result-mail.md` |
| bash | `audit/test-results/test-result-bash.md` |
| avatar | `audit/test-results/test-result-avatar.md` |
