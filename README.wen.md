# 灵台 Python 运行时与 SDK

> [English](README.md) | [中文](README.zh.md) | [文言](README.wen.md)

灵台者，器灵所居之方寸也。此仓承其 Python 运行之身：内核、能力、命令、诸 addon 与原生辅器，皆归一发行包 `lingtai`。

## 安装

```bash
pip install lingtai
```

## 命令

`lingtai-agent` 者，启一器灵于其工作目录也：

```bash
lingtai-agent run /path/to/agent/
lingtai-agent check-caps
```

若欲图形设定、巡看、起灭诸灵，当用 TUI：

```bash
brew install lingtai-ai/lingtai/lingtai-tui
```

## 包之分职

| 名 | 职 |
|---|---|
| `lingtai.kernel` | 至简内核：`BaseAgent`、固有诸器、LLM 之约、传书与日志。 |
| `lingtai` | 具足运行时：Agent、能力、适配器、MCP 与 addon。 |
| `lingtai_cli` | Python 命令宿主，组装运行时而启之。 |
| `lingtai.mcp_servers` | 内置诸 addon：IMAP、Telegram、飞书、微信、WhatsApp、cloud mail 等。 |

其 Python 引入之根仍曰 `lingtai`；仓与发行之名从今称 `lingtai-sdk`，非令用者改作 `import lingtai_sdk`。

## 灵台之制

```text
/agents/wukong/
  .agent.lock               # 独占之锁
  .agent.heartbeat          # 存活之证
  .agent.json               # 清单
  system/
    covenant.md             # 盟约
    pad.md                  # 手简
  mailbox/
    inbox/                  # 所受之书
    outbox/                 # 待发之书
    sent/                   # 已发之录
  logs/
    events.jsonl            # 事记
```

路径即名，目录即身；诸灵以信匣传书，不假共享内存。

## 相关

- Python runtime / SDK: <https://github.com/Lingtai-AI/lingtai-sdk>
- TUI / portal: <https://github.com/Lingtai-AI/lingtai>
- 灵台文: <https://lingtai.ai>

## 许可

Apache-2.0。Zesen Huang 与 LingTai-AI 诸贡献者共持。
