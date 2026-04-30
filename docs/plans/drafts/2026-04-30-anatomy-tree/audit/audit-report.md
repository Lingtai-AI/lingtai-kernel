# Anatomy §Source 审计总报告

**日期**：2026-04-30  
**审计范围**：49 叶树 README.md 中 §Source 引用表  
**审计方法**：7 化身并行，各自逐行核实 §Source 表中每一行号引用  
**kernel 源码**：`/Users/huangzesen/Documents/GitHub/lingtai-kernel/src/`  
**audit-daemon-mcp** 尚未归，标注 PENDING

---

## 总览

| 审计身 | 叶数 | 引用数 | ✅ | ⚠️ (≤3行偏) | ❌ (>3行偏或错误) | 根因 |
|---|---|---|---|---|---|---|
| audit-file-tools | 5 | 46 | 46 | 0 | 0 | 全准 |
| audit-shell-codex | 6 | 49 | 46 | 3 | 0 | off-by-1 |
| audit-llm | 6 | 38 | 11 | 3 | 24 | **+4行**：adapter docstring 增长 |
| audit-core-init | 9 | 75 | 24 | 38 | 13 | **+2行**：文件头部增加 |
| audit-avatar-mail | 10 | 81 | 68 | 12 | 1 | **+2行** handshake.py；1处完全错误 |
| audit-psyche-lib-viz-web | 6 | 50 | 40 | 2 | 8 | **+25行** psyche/__init__.py |
| audit-daemon-mcp | 7 | — | ⏳ | ⏳ | ⏳ | PENDING |
| **合计(6身)** | **42** | **339** | **235** | **58** | **46** | |

**总准确率**：69% ✅ · 17% ⚠️ · 14% ❌

---

## 五类 §Source 漂移

### ① +4行：LLM adapter docstring 增长

| 文件 | 偏移 | 受影响叶 |
|---|---|---|
| `lingtai/llm/adapters/anthropic.py` | +4 | llm/anthropic |
| `lingtai/llm/adapters/gemini.py` | +4 | llm/gemini |
| `lingtai/llm/adapters/deepseek.py` | +4 | llm/deepseek |
| `lingtai/llm/adapters/openrouter.py` | +4 | llm/openrouter |

**根因**：四文件模块 docstring 各增长 4 行，导致后续所有函数/类下移 4 行。  
**修法**：`sed -i 's/\(:[0-9]*\)/加4修正/' README.md`（精确匹配，逐文件处理）

### ② +2行：lingtai_kernel/ 多文件头部增加

| 文件 | 偏移 | 受影响叶 |
|---|---|---|
| `base_agent.py` | +2 | core 多叶、init 多叶 |
| `session.py` | +2 | core/agent-state-machine |
| `workdir.py` | +2 | core/network-discovery, core/config-resolve |
| `handshake.py` | +2 | avatar/handshake-files, mail/peer-send |
| `services/mail.py` | +2 | mail 多叶 |

**根因**：文件头部新增了 2 行（import 或注释）。  
**修法**：batch sed +2

### ③ +2行：handshake.py 系统偏移

与②同一根因，但值得单独列出因为影响 3 个叶。

| 函数 | 声称行 | 实际行 |
|---|---|---|
| `resolve_address` | 13-22 | 15-24 |
| `is_agent` | 25-27 | 27-29 |
| `is_human` | 30-36 | 32-38 |
| `is_alive` | 39-55 | 41-57 |

**修法**：handshake.py 所有引用 +2

### ④ +25行：psyche/__init__.py 大幅增长

| 函数 | 声称行 | 实际行 | 偏移 |
|---|---|---|---|
| `PsycheManager` | 66 | 91 | +25 |
| `_lingtai_update` | 112 | 137 | +25 |
| `_lingtai_load` | 120 | 145 | +25 |
| `_pad_edit` | 150 | 175 | +25 |
| `_pad_append` | 244 | 269 | +25 |
| `_pad_load` | 290 | 315 | +25 |
| `_context_molt` (delegation) | 317 | 342 | +25 |
| `setup()` hook registration | 334 | 359 | +25 |

**根因**：`_APPEND_LIST_PATH`, `_APPEND_TOKEN_LIMIT`, `_resolve_path`, `_read_append_content`, `_is_text_file` 等新成员在 `PsycheManager` 类之前插入，导致类及所有方法下移 25 行。  
**修法**：手工逐条修正 core-memories/README.md §Source 表

### ⑤ 完全错误：mailbox-core _list_inbox/_read_ids

| 引用 | 声称行 | 实际行 | 说明 |
|---|---|---|---|
| `_list_inbox` / `_read_ids` | 26 | 135 / 162 | 行 26 是空白行。函数已被移到文件后部。 |

**根因**：函数在文件中被重组移动。  
**修法**：重写该 §Source 行

---

## 逐身详报

### 1. audit-file-tools（5 叶 · 46 引用）

| 叶 | ✅ | ⚠️ | ❌ |
|---|---|---|---|
| read | 10 | 0 | 0 |
| write | 8 | 0 | 0 |
| edit | 10 | 0 | 0 |
| glob | 8 | 0 | 0 |
| grep | 10 | 0 | 0 |

**额外发现**：edit handler 直接用 `agent._file_io.read()` + `agent._file_io.write()` 而非 `LocalFileIOService.edit()`（file_io.py:80-93 存在但是死代码）。README 已正确描述。

### 2. audit-shell-codex（6 叶 · 49 引用）

| 叶 | ✅ | ⚠️ | ❌ |
|---|---|---|---|
| bash | 12 | 0 | 0 |
| bash/yolo | 5 | 1 | 0 |
| bash/sandbox | 6 | 1 | 0 |
| bash/kill | 5 | 1 | 0 |
| codex | 10 | 0 | 0 |
| codex/oauth | 8 | 0 | 0 |

⚠️ 均为 off-by-1（方法签名行、docstring 结束行、备份赋值行）。

**额外产出**：GitHub Issue #2——"codex" 命名冲突，blast radius 3 文件。

### 3. audit-llm（6 叶 · 38 引用）

| 叶 | ✅ | ⚠️ | ❌ |
|---|---|---|---|
| anthropic | 1 | 0 | 6 |
| gemini | 1 | 0 | 6 |
| deepseek | 1 | 1 | 4 |
| openai | 5 | 0 | 0 |
| openrouter | 1 | 1 | 6 |
| minimax | 2 | 1 | 2 |

**根因一致**：4 个 adapter（anthropic/gemini/deepseek/openrouter）的 module docstring 各增长 4 行。openai 和 minimax 未受影响。

**额外产出**：Dark Parts Catalog（15 发现含 3 Critical）；Sync Beacons（6 adapter）；LLM Index README。

### 4. audit-core-init（9 叶 · 75 引用）

| 叶 | ✅ | ⚠️ | ❌ |
|---|---|---|---|
| agent-state-machine | 2 | 5 | 2 |
| network-discovery | 4 | 5 | 0 |
| config-resolve | 1 | 5 | 2 |
| preset-materialization | 2 | 4 | 1 |
| preset-allowed-gate | 2 | 4 | 1 |
| venv-resolve | 2 | 2 | 1 |
| init-schema | 4 | 4 | 3 |
| molt-protocol | 2 | 3 | 2 |
| wake-mechanisms | 5 | 6 | 1 |

**系统偏移**：base_agent.py +2 行（头部增加）；session.py +2；workdir.py +2。部分引用还受 TOP_OPTIONAL 省略影响（声称行 vs 实际行差 5 行）。

### 5. audit-avatar-mail（10 叶 · 81 引用）

| 叶 | ✅ | ⚠️ | ❌ |
|---|---|---|---|
| avatar/spawn | 12 | 0 | 0 |
| avatar/boot-verification | 3 | 3 | 0 |
| avatar/shallow-vs-deep | 7 | 0 | 0 |
| avatar/handshake-files | 3 | 6 | 0 |
| mail/dedup | 4 | 0 | 0 |
| mail/atomic-write | 5 | 0 | 0 |
| mail/scheduling | 11 | 0 | 0 |
| mail/identity-card | 5 | 2 | 0 |
| mail/mailbox-core | 6 | 1 | **1** |
| mail/peer-send | 12 | 0 | 0 |

**系统偏移**：handshake.py 全部函数 +2 行。  
**唯一 ❌**：mail/mailbox-core 的 `_list_inbox`/`_read_ids` 引用完全错误（行 26→实际 135/162）。

### 6. audit-psyche-lib-viz-web（6 叶 · 50 引用）

| 叶 | ✅ | ⚠️ | ❌ |
|---|---|---|---|
| psyche/soul-flow | 10 | 0 | 0 |
| psyche/inquiry | 5 | 2 | 0 |
| psyche/core-memories | 2 | 0 | **8** |
| library/paths-resolution | 8 | 0 | 0 |
| vision/multimodal | 7 | 0 | 0 |
| web_search/fallback | 8 | 0 | 0 |

**严重偏移**：psyche/__init__.py 全部 8 处引用 +25 行。  
**inquiry**：`.inquiry` 文件处理 block 范围 807-837→实际 809-852。

### 7. audit-daemon-mcp（7 叶）——PENDING

尚驰。daemon 4 叶 + mcp 3 叶。

---

## 修复建议优先级

| 优先级 | 修什么 | 修法 | 影响 |
|---|---|---|---|
| P0 | ⑤ mailbox-core 完全错误 | 手工：line 26→135/162 | 1叶 |
| P1 | ④ psyche +25行 | 手工逐条修正 | 1叶8行 |
| P2 | ① LLM +4行 | batch sed | 4叶~24行 |
| P3 | ②③ Core/handshake +2行 | batch sed | ~10叶~50行 |
| P4 | ⚠️ off-by-1 | 低优先，可后续修 | 多叶 |

---

## 产出文件索引

| 文件 | 路径 |
|---|---|
| 本报告 | `audit/audit-report.md` |
| file-tools | `audit/audit-file-tools.md` |
| shell-codex | `audit/audit-shell-codex.md` |
| llm | `audit/audit-llm.md` |
| core-init | `audit/audit-core-init.md` |
| avatar-mail | `audit/audit-avatar-mail.md` |
| psyche-lib-viz-web | `audit/audit-psyche-lib-viz-web.md` |
| daemon-mcp | `audit/audit-daemon-mcp.md`（PENDING） |
