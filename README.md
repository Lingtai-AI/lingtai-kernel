<div align="center">

# lingtai-kernel

**The Python runtime and SDK that powers LingTai agents.**

[![PyPI](https://img.shields.io/pypi/v/lingtai?color=%237dab8f)](https://pypi.org/project/lingtai/)
[![License](https://img.shields.io/github/license/Lingtai-AI/lingtai-kernel?color=%237dab8f)](LICENSE)
[![Blog](https://img.shields.io/badge/blog-lingtai.ai-%23d4a853)](https://lingtai.ai)

[English](README.md) · [简体中文](docs/readmes/README.zh.md) · [文言](docs/readmes/README.wen.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Support](SUPPORT.md)

</div>

---

This repository is the engine underneath the product, not the product itself.

**Looking for LingTai the product?** The [`Lingtai-AI/lingtai`](https://github.com/Lingtai-AI/lingtai)
repository is the source of truth for the Digital Scientist — the lifelong, self-growing
agent — including the guided installer, the TUI/Portal, and everyday workflows. Normal
users should start there and let the installer manage this runtime for them. This
repository is for developers who build on or contribute to the kernel; do not treat a
bare `pip install` here as the normal installation path.

## What this repository owns

- **The agent runtime** — the core turn loop, lifecycle, tool dispatch, mailbox,
  soul (inner voice), molt, and notification machinery that make an agent run.
- **Two Python surfaces** — `lingtai.kernel`, the minimal runtime (`BaseAgent`,
  intrinsics, the LLM protocol, mail, and logging), and `lingtai`, the
  batteries-included runtime, CLI, and services that build `Agent(BaseAgent)` on top
  of it and re-export the kernel's public API.
- **The batteries** — the bundled built-in tools, the LLM adapters, the curated MCP
  server implementations, the packaging (Python distribution plus the bundled Rust
  search sidecar). These are ownership boundaries, not a feature list.

## Developer quick start

For **kernel development** — not the normal LingTai user installation path. Requires
Python >= 3.11; use a local `.venv`.

```bash
git clone https://github.com/Lingtai-AI/lingtai-kernel.git
cd lingtai-kernel
uv venv --python 3.11
uv pip install -e . pytest
.venv/bin/python -m pytest
```

## Architecture / developer entry points

| Entry point | What it covers |
|---|---|
| [`ANATOMY.md`](ANATOMY.md) | Repository map — top-level layout and where each subsystem's anatomy begins. |
| [`src/lingtai/kernel/ANATOMY.md`](src/lingtai/kernel/ANATOMY.md) | The core runtime: `BaseAgent`, turn/lifecycle, tool machinery, mail, LLM protocol. |
| [`src/lingtai/ANATOMY.md`](src/lingtai/ANATOMY.md) | The `lingtai` package: `Agent(BaseAgent)`, capabilities, presets, CLI, public re-exports. |
| [`src/lingtai/tools/ANATOMY.md`](src/lingtai/tools/ANATOMY.md) | The concrete built-in tools and the registry that composes them. |
| [`src/lingtai/mcp_servers/ANATOMY.md`](src/lingtai/mcp_servers/ANATOMY.md) | The bundled MCP server implementations. |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Contribution workflow and repository navigation. |
| [`docs/references/claude-code-guide.md`](docs/references/claude-code-guide.md) | The full repository guide — test commands, architecture notes, and conventions. |

## Security · Support · Acknowledgements

For responsible disclosure, read [SECURITY.md](SECURITY.md); for help, read
[SUPPORT.md](SUPPORT.md); for credits, read
[docs/references/acknowledgements.md](docs/references/acknowledgements.md).

## License

Apache-2.0 — [Zesen Huang](https://github.com/huangzesen), 2025–2026

<div align="center">

[lingtai.ai](https://lingtai.ai) · [LingTai (product)](https://github.com/Lingtai-AI/lingtai) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Support](SUPPORT.md)

</div>
