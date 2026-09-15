---
name: agent-readme-contract
tool: agent-readme
contract_version: 1
related_files:
  - src/lingtai/kernel/agent_readme/BEHAVIORS.md
  - src/lingtai/kernel/agent_readme/ANATOMY.md
  - src/lingtai/kernel/agent_readme/__init__.py
maintenance: |
  The agent README folder role split (README.md vs substrate.md) and its
  ownership/maintenance boundary. Every agent-observable behavior clause
  MUST be guarded by a LABT in the sibling BEHAVIORS.md (tridirectional
  loop); when this contract changes, update BEHAVIORS.md and ANATOMY.md
  together.
---
# agent-readme CONTRACT

> 固定 agent 文件夹 `README.md` 与 `substrate.md` 的角色分工、所有权与维护边界。
> 替代 #1252 的 agent-manual CONTRACT v1（重生成器 + MANUAL.md）的语义。

## 1. 角色
Guarded by: [AR001](BEHAVIORS.md#behavior-ar001)


| 文件 | 位置 | 面向 | 角色 |
|---|---|---|---|
| `README.md` | agent 文件夹根 | 外界（人、新接触者、工具） | **入口 + 目录导航**：导航 agent 文件夹的固有结构。**不承载 agent 身份（名字）**，不承载任何动态数据。 |
| `substrate.md` | agent 的 `system/`（镜像 kernel `prompts/substrate/substrate.md`） | agent/机器 | **机制渐进式披露入口**：身体机制（扩展/生命周期/通信/记忆/空闲/工具分级）的 resident 操作模型 + 深挖指引。 |

## 2. 所有权与生成

- `README.md`：kernel-owned。**运行时生成**：模板位于 kernel repo `agent_readme/` 包；**refresh / molt / BaseAgent 构造时检查 staleness**（文件缺失、模板版本变化 → 重新生成；构造挂载保证 avatar 首用前即有 README）。全英文；**不含 agent 名占位**（agent 名字不是 README 的责任）。接管无版本头既有文件时先落 `README.md.bak` 并记事件，不静默覆盖。
- `substrate.md`：kernel-owned。源是 `src/lingtai/prompts/substrate/substrate.md`；agent 文件夹里的副本由 kernel 既有 prompt-source 机制同步。

## 3. 边界

- README **不**复制 substrate 的机制内容；substrate **不**复制 README 的目录导航。二者以链接/指引互相指向。
- README **不**放 agent 名、live snapshot、身份元数据（provider/model/heartbeat 等动态值）；动态真相由 `init.json` / `.agent.json` / `.status.json` / `system/pad.md` 承载，README 只在 Notes 节以文字提及这些真相文件。
- README 不是纯目录文件：目录表每行带「是什么 / 何时打开」解释。
- 无 agent 侧 overlay；无运行时 secret 渲染（模板不含 secret 值）。

## 4. 渐进式披露路径

```
外界/人 ──► agent 根 README.md（目录导航入口）
                    │
                    ▼
          system/substrate.md（机制入口）
                    │
        ┌───────────┼───────────────┐
        ▼           ▼               ▼
  system-manual  substrate-manual  各专项 manual
  (router)       (扩展形态)        (context/notification/...)
```

## 5. 契约测试（最小）

1. 初始化/refresh/molt 后 agent 根存在 `README.md`，且含指向 `system/substrate.md` 的相对路径。
2. staleness 检查：README 缺失或模板版本变化时重新生成；文件内容与模板一致。
3. `README.md` 不含 agent 名与任何 live 动态值（无 provider/model/heartbeat 等运行时替换字段）。
4. substrate frontmatter `related_files` 含 `agent_readme/CONTRACT.md`（反向链接维护）。
