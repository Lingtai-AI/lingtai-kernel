# 三役所揭之内核真问题

**日期**：2026-04-30
**作者**：lingtai-expert（协调者，亲自核验源码）
**方法**：从 13 化身之报告中过滤内核（`src/lingtai/`、`src/lingtai_kernel/`）真实缺陷。所有 file:line 均于今日直接 grep/read 源码核验。

---

## 一、Confirmed bugs（已实证）

### C1. `system("show")` / `status()` 缺 `state` 字段

- **症**：agent 调 `system("show")` 不知自身处于 IDLE/ACTIVE/ASLEEP 何态。需自行猜。
- **位**：`src/lingtai_kernel/base_agent.py:1725`（`def status()`）至行 1790（返回 dict）。返回 dict 无 `state` 字段。同文件行 1503 `_build_manifest()` 含 `"state": self._state.value`。两处不一。
- **证**：test-core-init（`test-result-core-init.md`）+ 今日 grep 确认：`_build_manifest()` 在行 1503 含 `"state": self._state.value`，但 `status()` 在行 1725-1790 构建返回 dict 时**不含**此字段。
- **调用点**：`status()` 仅两处调用——`base_agent.py:1696`（写 .status.json）与 `intrinsics/system.py:88`（`system("show")` handler）。加 `state` 为纯加法，不破任何现用。
- **修**：于 `status()` 返回 dict（行 1753 之 `"runtime":` 块内）增 `"state": self._state.value`，与 `stamina_left` 等动态字段同列。
- **影响**：1 文件 1 行。system intrinsic + base_agent。
- **优**：P1（功能不正确——agent 不知自身状态，影响决策）

### C2. error format 不统一

- **症**：5 个文件工具的错误返回格式不一致。`read` 用 `{"status": "error", "message": ...}`，`write/edit/glob/grep` 用 `{"error": ...}`。调用方（agent LLM prompt）需做两套解析。
- **位**：
  - `src/lingtai/core/read/__init__.py:42,50,52` — `{"status": "error", "message": ...}`
  - `src/lingtai/core/write/__init__.py:40,47` — `{"error": ...}`
  - `src/lingtai/core/edit/__init__.py:41,50,52,55,57,65` — `{"error": ...}`
  - `src/lingtai/core/glob/__init__.py:39,47` — `{"error": ...}`
  - `src/lingtai/core/grep/__init__.py:42,67` — `{"error": ...}`
- **证**：test-file-tools（`test-result-file-tools.md`）+ 今日 grep 确认
- **修**：统一为 `{"status": "error", "message": ...}`（read 已用此格式，余四者应改之）
- **影响**：4 文件，约 12 行
- **优**：P2（边角——agent 通常处理得不错，但增加 prompt 歧义）

### C3. bash `subprocess.run` 无进程组杀——超时后孙进程泄漏

- **症**：bash 命令超时时，仅杀直接子 shell。后台/孙进程存活为孤儿。例：`bash -c 'sleep 300 & echo "child_pid=$!"; wait'` 2 秒超时后，`sleep 300` 仍运行。
- **位**：`src/lingtai/core/bash/__init__.py:190`（`subprocess.run(..., shell=True, timeout=...)`）、行 211（`except subprocess.TimeoutExpired`）。无 `os.setsid`、无 `os.killpg`、无进程组管理。
- **证**：test-bash（`test-result-bash.md` §2.3）——PID 29131（`sleep 300`）确认超时后仍运行，手动 kill 方止。今日 grep `os.setsid`、`os.killpg` 零匹配确认。
- **修**：`subprocess.run()` 加 `preexec_fn=os.setsid`；`TimeoutExpired` handler 内加 `os.killpg(os.getpgid(proc.pid), signal.SIGTERM)`。
- **影响**：1 文件，约 3 行。需注意跨平台兼容（Windows 无 `os.setsid`）。
- **优**：P1（功能不正确——超时机制不彻底，后台进程泄漏为真实运维风险）

### C4. bash sandbox 相对路径解析以进程 CWD 而非 agent dir 为基

- **症**：传 `working_dir="./logs"` 被 sandbox 拒绝，报 "must be under agent working directory"。`./logs` 实为 agent 子目录，但 `Path("./logs").resolve()` 以 Python 进程 CWD（通常为项目根）为基解析，逃出 sandbox。
- **位**：`src/lingtai/core/bash/__init__.py:174-187`。行 178 `resolved = str(Path(cwd).resolve())` 以进程 CWD 为基。行 179 `sandbox = str(Path(self._working_dir).resolve())` 以 agent dir 为基。两者基不同。
- **证**：test-bash（`test-result-bash.md` §3.3）+ 今日源码确认。错误消息 "must be under agent working directory" 具误导性——路径实际在 sandbox 内，但解析方式有误。
- **修**：相对路径应先锚定至 agent dir 再 resolve：`resolved = str((Path(self._working_dir) / cwd).resolve())`。或要求传绝对路径并更新 README。
- **影响**：1 文件，1-3 行
- **优**：P2（边角——agent 通常传绝对路径，但相对路径理应工作）

---

## 二、Suspected bugs（疑而未证）

### S1. Dark Parts: superseded turn 丢诊断

- **症**（原报告）：新消息到达时覆盖当前 turn，诊断信息（如部分 tool results）丢失。
- **证**：今日 grep `_run_loop`（base_agent.py:913-1038）发现 `_concat_queued_messages`（行 1154-1178）合并而非覆盖。需进一步实测才能确认是否真有丢弃。INCONCLUSIVE。
- **裁**：**需实测**——理论可能，但当前代码显示消息被合并而非覆盖。
- **优**：待实测后定

### S2. Dark Parts: daemon token 归属不一致

- **症**（原报告）：daemon（分神）的 token 使用归属到父代 ledger 的方式可能不一致。
- **证**：今日未深入查 `daemon/__init__.py` 双账本机制（`_dual_ledger`）。test-psyche-daemon-mcp 实测确认双写正常，但"归属一致性"需对照父 ledger 与子 ledger 的数值。
- **裁**：**待查**——test PASS 不代表数值完全对齐。
- **优**：P2

### S3. parallel bash 调用失败

- **症**：单 turn 内 dispatch 4 个独立 bash 调用，全部失败，报 `health_check:pre_send_pairing` 错误。逐一重试则成功。
- **位**：内核运行时循环——`_PARALLEL_SAFE_TOOLS` 在 `base_agent.py:76` 为空集，bash 不在并行安全集合中。`tool_executor.py:99-106` 检查此集合。若 bash 调用被意外并行执行或有竞态。
- **证**：test-bash §6.1。4 个并行调用全部失败；逐一成功。
- **裁**：**需更多实测**——可能是 by design（bash 不支持并行），但错误消息令人困惑。
- **优**：P2

---

## 三、Behavioral mismatches（文档与代码不一致）

### B1. glob `**` 行为

- **文档言**：`**` in the pattern works because `os.walk` already recurses
- **代码实**：`fnmatch` 无 `**` 语义；`*` 在 fnmatch 中匹配 `/`，所以 `*.py` 即可递归匹配。`**/*.py` 在 POSIX 上反而**窄化**结果（要求路径含 `/`）。
- **谁该改**：文档已由 test-file-tools 自主修正 ✅

### B2. codex `_inject_catalog()` 延迟更新

- **文档言**：submit/consolidate/delete 后 catalog 更新
- **代码实**：`_inject_catalog()` 在 submit/consolidate/delete 后的更新是延迟的（下轮加载才生效）
- **谁该改**：文档应注明延迟
- **证**：test-bash 超使命测 codex 时发现

### B3. bash 截断后缀格式

- **文档言**：`"... (truncated, {total} chars total)"`
- **代码实**（今日核）：`src/lingtai/core/bash/__init__.py:201-203` — `f"\n... (truncated, {len(result.stdout)} chars total)"`
- **裁**：**文档与代码一致**。原始报告称后缀为 `[truncated — 50035 bytes total]`，但今日核源码显示代码实际用 `... (truncated, N chars total)` 格式。**误报。**移至第四节。

### B4. mail `from` 显示不一致

- **文档言**：未明确说明 `check` 与 `read` 之 `from` 格式差异
- **代码实**：`check` 用 `_inject_identity()` 富化格式 `"agent_name (address)"`；`read` 用原始 `from` 字段
- **谁该改**：文档应注明差异
- **证**：test-mail §1.3

### B5. mail scheduling 失败发送计入总数

- **文档言**：at-most-once 保证
- **代码实**：`src/lingtai/core/email/__init__.py:719` 注释 "at-most-once: increment before send"。行 720-722 在发送前递增 `sent` 计数器。失败的发送也被计数。
- **谁该改**：设计层面决定——是改代码（发送后递增）还是加 `failed` 字段提升可观察性
- **证**：audit-avatar-mail.md 语义审计

### B6. `read` 二进制文件错误泄露 Python 内部

- **文档言**：raise a generic read error
- **代码实**：二进制文件返回 `'utf-8' codec can't decode byte 0x80 in position 128: invalid start byte`——暴露 Python codec 内部信息
- **谁该改**：代码——catch `UnicodeDecodeError` 并返回更干净的消息
- **证**：test-file-tools §2 (R5)

---

## 四、Verified working（误报作终）

### V1. `_cancel_event` 机制

- **原疑**：CancelledError 无 rollback
- **验后**：cancel 逻辑在 `base_agent.py:1308-1309`，`_cancel_event.is_set()` → `clear()` + early return。**机制正常。**原 Dark Parts S1 引用的 `session.py:1463` 已失效——代码重构后 `session.py` 仅 455 行，无 `turn_loop` 或 `_end_turn`。

### V2. mail 全链路

- **原疑**：mail 可能有并发/去重问题
- **验后**：test-mail 7/7 PASS。dedup/atomic/scheduling/identity/peer-send 全验。**无问题。**

### V3. avatar spawn 流程

- **原疑**：spawn 可能有初始化竞态
- **验后**：test-avatar spawn PASS。名校验、目录创建、init.json 继承、进程启动、ledger 记录均正常。**无问题。**

### V4. bash 截断后缀格式

- **原疑**：后缀格式和单位（chars vs bytes）与文档不符
- **验后**：今日核 `bash/__init__.py:201-203`，实际格式 `"... (truncated, {len(result.stdout)} chars total)"` 与文档一致。**误报。**

### V5. codex 命名冲突

- **原疑**：codex ID 冲突导致数据丢失
- **验后**：test-bash codex 实测零偏差。**无问题。**（此为 policy issue，非 runtime bug，见附。）

---

## 五、Summary

| 类 | 数量 | 详情 |
|---|---|---|
| **Confirmed bugs** | **4** | C1 status缺state（P1）、C2 error format不统一（P2）、C3 孙进程泄漏（P1）、C4 sandbox相对路径（P2） |
| Suspected | 3 | S1 superseded turn待实测、S2 daemon token待查、S3 parallel bash待查 |
| Behavioral mismatch | 5 | B1已修、B2-B6待文档/代码更新 |
| Verified working | 5 | V1-V5 误报作终 |

**底线**：三役所揭之内核真实可修之 bug，**4 条**。
- C1 可即修（1 行）
- C3 可即修（~3 行，需考虑跨平台）
- C2 需统一 4 文件（~12 行）
- C4 需改路径解析逻辑（1-3 行）

内核整体健壮——实跑 38 项测试中无一崩溃、无一数据丢失。

---

## 附：未混入此报告之事

- 行号漂移（属 anatomy 自身，非内核）
- codex 命名冲突（policy issue，非 runtime bug；GitHub Issue #2 已提）
- avatar 越界添新叶/代码（人已另裁）
- Sync Beacons / LLM Index README（额外产出，与 bug 无关）
- Deep avatar `system/` copy 是 no-op（defense-in-depth，非 bug）
- MCP 无能力声明时静默不活动（by design，非 bug）
- orphaned `.tmp` 文件（设计关注点——需 crash 于精确时刻，累积慢。proposal 已有实现方案）

---

## 附：Dark Parts Catalog 15 条之归宿

原始 Dark Parts Catalog 15 条（含 3 Critical）由 audit-llm 产出，原始文件未存于磁。以下为逐条裁决：

| # | 原始描述 | 归类 | 裁决 |
|---|---|---|---|
| 1 | CancelledError 无 rollback | ~~Critical~~ | **V1 已失效**——代码重构，session.py 无 turn_loop |
| 2 | superseded turn 丢诊断 | Critical | **S1 待实测**——代码显示合并而非覆盖 |
| 3 | daemon token 归属不一致 | Critical | **S2 待查**——需对账双 ledger 数值 |
| 4-6 | （其余 12 条行号漂移相关） | anatomy | **非内核**，不入此报 |
| 7-9 | （文档 column header 不齐） | anatomy | **非内核** |
| 10-12 | （frontmatter 风格之争） | anatomy | **非内核** |
| 13 | （adapter 源码引用偏移 +4） | anatomy | **已由 audit-llm 修复** |
| 14 | （LLM 缺失引用） | anatomy | **已由 audit-llm 补入** |
| 15 | （minimax class 截断） | anatomy | **已由 audit-llm 修正** |

**结论**：Dark Parts 15 条中，仅 3 条涉内核（#1-3），其中 #1 已失效，#2/#3 待深入查。无新增 confirmed bug。

---

## 附：raw-bug-candidates.md 13 条对照

| # | 描述 | 本报告归类 |
|---|---|---|
| 1 | bash sandbox 相对路径 | **C4** |
| 2 | parallel bash 失败 | **S3** |
| 3 | system(show) 缺 state | **C1**（合并） |
| 4 | bash 截断后缀格式 | **V4 误报** |
| 5 | error format 不统一 | **C2**（合并） |
| 6 | mail from 显示不一致 | **B4** |
| 7 | orphaned .tmp 文件 | **附·未混入** |
| 8 | 孙进程泄漏 | **C3** |
| 9 | scheduling 失败计数 | **B5** |
| 10 | read 二进制错误泄露 | **B6** |
| 11 | deep copy no-op | **附·未混入** |
| 12 | MCP 静默不活动 | **附·未混入** |
| 13 | .tmp 清理脚本手动 | **附·未混入**（同 #7） |
