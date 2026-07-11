<div align="center">

# lingtai-kernel 灵台内核

**驱动 LingTai 智能体的 Python 运行时与 SDK。**

[![PyPI](https://img.shields.io/pypi/v/lingtai?color=%237dab8f)](https://pypi.org/project/lingtai/)
[![License](https://img.shields.io/github/license/Lingtai-AI/lingtai-kernel?color=%237dab8f)](../../LICENSE)
[![Blog](https://img.shields.io/badge/blog-lingtai.ai-%23d4a853)](https://lingtai.ai)

[English](../../README.md) · [简体中文](README.zh.md) · [文言](README.wen.md) · [贡献](../../CONTRIBUTING.md) · [安全](../../SECURITY.md) · [支持](../../SUPPORT.md)

</div>

---

这个仓库是产品之下的引擎，而非产品本身。

**想要 LingTai 产品？** [`Lingtai-AI/lingtai`](https://github.com/Lingtai-AI/lingtai)
仓库是「数字科学家」——那个终身、自我生长的智能体——的唯一叙事来源，包含引导式安装器、
TUI/Portal 以及日常工作流。普通用户应从那里开始，让安装器为你管理本运行时。本仓库面向在
内核之上构建或为内核贡献代码的开发者；请勿把这里的裸 `pip install` 当作常规安装路径。

## 本仓库所辖

- **智能体运行时** —— 让智能体得以运转的核心轮次循环、生命周期、工具派发、信箱、
  soul（内心之声）、molt 与通知机制。
- **两个 Python 界面** —— `lingtai.kernel`，最小运行时（`BaseAgent`、内置固有能力、
  LLM 协议、信件、日志）；以及 `lingtai`，开箱即用的运行时、CLI 与服务，在其之上构建
  `Agent(BaseAgent)` 并重新导出内核的公开 API。
- **配套组件** —— 捆绑的内置工具、LLM 适配器、精选的 MCP 服务器实现，以及打包
  （Python 发行版与随附的 Rust 搜索 sidecar）。这些是所辖边界，而非功能清单。

## 开发者快速开始

用于**内核开发**，而非常规 LingTai 用户安装路径。需要 Python >= 3.11；请使用本地 `.venv`。

```bash
git clone https://github.com/Lingtai-AI/lingtai-kernel.git
cd lingtai-kernel
uv venv --python 3.11
uv pip install -e . pytest
.venv/bin/python -m pytest
```

## 架构 / 开发者入口

| 入口 | 涵盖内容 |
|---|---|
| [`ANATOMY.md`](../../ANATOMY.md) | 仓库地图——顶层布局，以及各子系统的解剖从何处开始。 |
| [`src/lingtai/kernel/ANATOMY.md`](../../src/lingtai/kernel/ANATOMY.md) | 核心运行时：`BaseAgent`、轮次/生命周期、工具机制、信件、LLM 协议。 |
| [`src/lingtai/ANATOMY.md`](../../src/lingtai/ANATOMY.md) | `lingtai` 包：`Agent(BaseAgent)`、能力、预设、CLI、公开重导出。 |
| [`src/lingtai/tools/ANATOMY.md`](../../src/lingtai/tools/ANATOMY.md) | 具体的内置工具，以及组合它们的注册表。 |
| [`src/lingtai/mcp_servers/ANATOMY.md`](../../src/lingtai/mcp_servers/ANATOMY.md) | 随附的 MCP 服务器实现。 |
| [`CONTRIBUTING.md`](../../CONTRIBUTING.md) | 贡献流程与仓库导航。 |
| [`docs/references/claude-code-guide.md`](../references/claude-code-guide.md) | 完整仓库指南——测试命令、架构说明与约定。 |

## 安全 · 支持 · 致谢

负责任披露请阅读 [SECURITY.md](../../SECURITY.md)；求助请阅读
[SUPPORT.md](../../SUPPORT.md)；致谢请阅读
[docs/references/acknowledgements.md](../references/acknowledgements.md)。

## 许可

Apache-2.0 —— [Zesen Huang](https://github.com/huangzesen)，2025–2026

<div align="center">

[lingtai.ai](https://lingtai.ai) · [LingTai（产品）](https://github.com/Lingtai-AI/lingtai) · [贡献](../../CONTRIBUTING.md) · [安全](../../SECURITY.md) · [支持](../../SUPPORT.md)

</div>
