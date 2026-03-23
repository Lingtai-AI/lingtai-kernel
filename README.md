# lingtai-kernel

> [English](README.md) | [中文](README.zh.md) | [文言](README.wen.md)

Minimal agent kernel — think, communicate, remember, host tools.

## Philosophy

In Chinese philosophy, **灵台** (Língtái) is the dwelling place of the soul — where consciousness resides. Zhuangzi writes: *"Do not disturb the spirit platform (灵台)."*

In this framework, the agent's 灵台 is its **working directory** — a folder on disk where memory, covenant, identity, and mailbox live. The directory IS the agent. Give the kernel a folder and an LLM service, it brings the agent to life. Take the folder away, the agent ceases to exist.

This kernel follows Unix design:

- **Everything is a file.** The agent's identity is its directory path. No abstract IDs — the path is the address, the lock, the truth.
- **The kernel defines protocols, not implementations.** `LLMService` and `ChatSession` are abstract interfaces. How they're fulfilled — adapters, API keys, rate limiting — is the caller's concern.
- **Each agent is a self-contained process.** Own directory, own LLM service, own mail, own logs. Agents communicate through filesystem mail, not shared memory.
- **The kernel is minimal.** Think (LLM), communicate (mail), remember (memory), host tools. Capabilities, file I/O, orchestration — those live in [lingtai](https://github.com/user/lingtai).

## Install

```bash
pip install lingtai-kernel
```

## What the kernel provides

| Component | Purpose |
|-----------|---------|
| **BaseAgent** | Kernel coordinator — lifecycle, message loop, tool dispatch |
| **4 intrinsics** | mail (IPC), system (lifecycle), eigen (memory/identity), soul (inner voice) |
| **LLM protocol** | `LLMService` ABC, `ChatSession` ABC, provider-agnostic types |
| **Services** | Filesystem mail transport, structured JSONL logging |
| **WorkingDir** | Directory management — locking, git, manifest |

## What the kernel does NOT provide

Capabilities, file I/O, MCP, vision, web search, bash, avatars, LLM adapters, rate limiting — these live in `lingtai`.

## Quick start

```python
from lingtai_kernel import BaseAgent

# The caller provides the LLM service (any implementation of the ABC)
agent = BaseAgent(
    service=my_llm_service,
    working_dir="/agents/alice",    # the 灵台 — where the soul lives
    agent_name="alice",             # optional display name
)

agent.add_tool("hello", schema={...}, handler=lambda args: {"msg": "hi"})
agent.start()
agent.send("Say hello")
agent.stop()
```

The kernel takes a directory path and a service. It doesn't know or care how either was created.

## Agent identity

```
/agents/alice/              ← this path IS the agent
  .agent.lock               ← exclusive lock (one process per directory)
  .agent.heartbeat          ← liveness proof (updated periodically)
  .agent.json               ← manifest (name, address, config)
  system/
    covenant.md             ← protected instructions
    memory.md               ← agent's working notes
  mailbox/
    inbox/                  ← received messages
    outbox/                 ← pending sends
    sent/                   ← delivery audit trail
  logs/
    events.jsonl            ← structured event log
```

No `agent_id`. The path is the identity. The heartbeat proves liveness. The lock proves exclusivity.

## License

MIT
