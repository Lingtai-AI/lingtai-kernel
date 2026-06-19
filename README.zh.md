# 灵台 LingTai Python 运行时 / SDK

> [English](README.md) | [中文](README.zh.md) | [文言](README.wen.md)

LingTai 的 Python 运行时与 SDK：提供 agent runtime、`lingtai.kernel` 最小内核、能力系统、CLI host、MCP addons、以及可选 native helper。完整交互式体验请使用 [TUI](https://github.com/Lingtai-AI/lingtai)。

## 安装

```bash
pip install lingtai
```

## CLI

`lingtai-agent` 用来从工作目录启动和运行 agent：

```bash
lingtai-agent run /path/to/agent/
lingtai-agent check-caps
```

日常创建、配置、监控 agent，推荐使用 TUI：

```bash
brew install lingtai-ai/lingtai/lingtai-tui
```

## 包结构

本仓库发行一个 Python distribution（`lingtai`），其中包含若干职责分明的包：

| 包 / 模块 | 作用 |
|---|---|
| `lingtai.kernel` | 最小 runtime：`BaseAgent`、内置工具协议、LLM protocol、mail、logging。 |
| `lingtai` | batteries-included 运行时：Agent、能力系统、LLM adapters、MCP/addon 集成；同时保留顶层便捷导出。 |
| `lingtai_cli` | Python CLI host / product assembly 层；`lingtai-agent` 与 `lingtai-cli` 的入口。 |
| `lingtai.mcp_servers` | vendored curated addons：IMAP、Telegram、Feishu、WeChat、WhatsApp、cloud mail 等。 |

Python import root 仍是 `lingtai`；仓库/发布方向是 `lingtai-sdk`，但不会改成 `import lingtai_sdk`。

## 能力

LingTai runtime 可组合视觉、网页搜索、文件读写、bash、知识、技能、内心/身份、avatar、daemon、MCP/addons 等能力。具体能力由 agent manifest、runtime options 与工具 guard 共同决定。

## Agent = 目录

```text
/agents/wukong/
  .agent.lock               # 独占锁
  .agent.heartbeat          # 存活证明
  .agent.json               # manifest
  system/
    covenant.md             # 受保护指令
    pad.md                  # 工作手记
  mailbox/
    inbox/                  # 收信
    outbox/                 # 待发
    sent/                   # 已发记录
  logs/
    events.jsonl            # 结构化事件日志
```

路径即身份；agent 之间通过 mailbox 文件系统通信。

## 相关项目

- Python runtime / SDK: <https://github.com/Lingtai-AI/lingtai-sdk>
- TUI / portal: <https://github.com/Lingtai-AI/lingtai>
- Website / manifesto: <https://lingtai.ai>

## 许可

Apache-2.0 — Zesen Huang 与 LingTai-AI contributors。
