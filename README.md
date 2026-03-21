# lingtai-kernel

Minimal agent kernel — think, communicate, remember, host tools.

The kernel of the [lingtai](https://github.com/user/lingtai) agent framework, extracted as a standalone package. Provides the minimal runtime for AI agents without any capabilities, file I/O, or multimodal features.

## Install

```bash
pip install lingtai-kernel
```

## What's included

- **BaseAgent** — kernel coordinator with 2-state lifecycle, message loop, tool dispatch
- **3 intrinsics** — mail (IPC), system (lifecycle), eigen (memory)
- **LLM protocol** — adapter registry, session management, context compaction
- **Services** — mail transport (TCP), structured logging (JSONL)

## What's NOT included

Capabilities, file I/O, MCP, vision, web search, bash, delegate — these live in `lingtai`.

## Quick start

```python
from lingtai_kernel import BaseAgent
from lingtai_kernel.llm import LLMService

# Register your adapter first
LLMService.register_adapter("my_provider", my_adapter_factory)

service = LLMService("my_provider", "my-model", api_key="...")
agent = BaseAgent(agent_name="bot", service=service, base_dir="/tmp")
agent.add_tool("hello", schema={"type": "object"}, handler=lambda args: {"msg": "hi"})
agent.start()
agent.send("Say hello")
agent.stop()
```

## License

MIT
