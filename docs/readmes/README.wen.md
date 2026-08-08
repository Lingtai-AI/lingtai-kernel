---
related_files:
- README.md
- docs/readmes/README.zh.md
maintenance: |
  Must stay synced with the source-locale README.md; update together when English content changes.
---

<div align="center">

# lingtai-kernel 灵台内核

**驱 LingTai 器灵之 Python 运行时与 SDK。**

[![PyPI](https://img.shields.io/pypi/v/lingtai?color=%237dab8f)](https://pypi.org/project/lingtai/)
[![License](https://img.shields.io/github/license/Lingtai-AI/lingtai-kernel?color=%237dab8f)](../../LICENSE)
[![Blog](https://img.shields.io/badge/blog-lingtai.ai-%23d4a853)](https://lingtai.ai)

[English](../../README.md) · [简体中文](README.zh.md) · [文言](README.wen.md) · [贡献](../../CONTRIBUTING.md) · [安全](../../SECURITY.md) · [支持](../../SUPPORT.md)

</div>

---

此仓乃器物之下之枢机，非器物本身也。

**欲得 LingTai 之器物者？** [`Lingtai-AI/lingtai`](https://github.com/Lingtai-AI/lingtai)
一仓，乃「数字科学家」——终身而自长之器灵——叙事之所本，引导之装置、TUI/Portal 及日用诸流皆在焉。
凡常之用者，宜自彼而始，任装置为汝掌此运行时。此仓所向，乃于内核之上有所构、或为内核效力之开发者也；
勿以此间裸 `pip install` 为寻常安装之途。

## 本仓所辖

- **器灵之运行时** —— 使器灵得以运转之核心轮次之环、生灭、工具之派发、信箱、
  soul（内心之声）、molt 与通知诸机也。
- **两 Python 之面** —— 其一 `lingtai.kernel`，至简之运行时（`BaseAgent`、固有之能、
  LLM 之约、书信、日志）；其二 `lingtai`，具足之运行时、CLI 与服务，于其上构
  `Agent(BaseAgent)`，且重导内核公开之 API。
- **配属之物** —— 所捆之内置工具、LLM 适配之器、精择之 MCP 服务器诸实现，暨其打包
  （Python 发行之物与随附之 Rust 搜索 sidecar）。此皆所辖之界，非罗列之能也。

## 开发者速启

为**内核之开发**，非寻常 LingTai 用者安装之途也。须 Python >= 3.11；宜用本地之 `.venv`。代码之基线仍为无上限之 Python >= 3.11；受管之 macOS 运行时与直接依赖，则循下列 wheel 相容之矩阵：

| 所指解释器 | 所支持之 Python | 直接 `onnxruntime` 所需 |
|---|---|---|
| Apple Silicon，macOS 14+ | 3.11–3.14 | 1.28.0 |
| Apple Silicon，macOS 13 | 3.11–3.13 | 1.23.2 |
| Intel/Rosetta，macOS 13+ | 3.11–3.13 | 1.23.2 |

经验之 `onnxruntime` wheels，以 macOS 13 为支持之下限。此矩阵中，惟 Apple Silicon 且 macOS 14+ 可用 Python 3.14；Rosetta 下之 x86_64 进程从 Intel 一列。

```bash
git clone https://github.com/Lingtai-AI/lingtai-kernel.git
cd lingtai-kernel
uv venv --python 3.11
uv pip install -e . pytest
.venv/bin/python -m pytest
```

## 架构与开发者门径

凡欲改此仓者，编码代理宜先寻而读本仓本地之开发指南技艺；此技艺统其工作之流，导诸事至下列门径，而不重复 Anatomy 或 Contract 之体系。

| 门径 | 所涵 |
|---|---|
| [`dev-guide-skill/SKILL.md`](../../dev-guide-skill/SKILL.md) | 必行之仓本地开发指南技艺：编码代理改动之前所必循之开发流程。 |
| [`ANATOMY.md`](../../ANATOMY.md) | 仓之舆图——顶层之布局，暨各子系统解剖所始之处，及其如何联至行为之 Contract。 |
| [`CONTRACT.md`](../../CONTRACT.md) | 规范之接口与行为之诺，暨受治组件配对归属之则，与 Anatomy 之构相联。 |
| [`src/lingtai/kernel/ANATOMY.md`](../../src/lingtai/kernel/ANATOMY.md) | 核心之运行时：`BaseAgent`、轮次与生灭、工具之机、书信、LLM 之约。 |
| [`src/lingtai/ANATOMY.md`](../../src/lingtai/ANATOMY.md) | `lingtai` 之包：`Agent(BaseAgent)`、诸能、预设、CLI、公开之重导。 |
| [`src/lingtai/tools/ANATOMY.md`](../../src/lingtai/tools/ANATOMY.md) | 具体之内置工具，暨组合诸工具之注册表。 |
| [`src/lingtai/mcp_servers/ANATOMY.md`](../../src/lingtai/mcp_servers/ANATOMY.md) | 随附之 MCP 服务器诸实现。 |
| [`CONTRIBUTING.md`](../../CONTRIBUTING.md) | 效力之流程与仓之导览。 |
| [`docs/references/claude-code-guide.md`](../references/claude-code-guide.md) | 全仓之指要——测试之命、架构之注与规约。 |

## 安全 · 支持 · 谢忱

责任之披露，阅 [SECURITY.md](../../SECURITY.md)；求助，阅
[SUPPORT.md](../../SUPPORT.md)；谢忱，阅
[docs/references/acknowledgements.md](../references/acknowledgements.md)。

## 许可

Apache-2.0 —— [Zesen Huang](https://github.com/huangzesen)，2025–2026

<div align="center">

[lingtai.ai](https://lingtai.ai) · [LingTai（器物）](https://github.com/Lingtai-AI/lingtai) · [贡献](../../CONTRIBUTING.md) · [安全](../../SECURITY.md) · [支持](../../SUPPORT.md)

</div>
