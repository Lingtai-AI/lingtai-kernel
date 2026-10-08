---
template_version: agent-readme/v1
---
# LingTai Agent Working Directory

This is the working directory of a LingTai agent. It holds the agent's identity,
memory, communication, logs, and work products. Use this README to navigate the
folder; mechanism details live in `system/substrate.md`.

## Where to look

| Path | What it is | Open when |
|---|---|---|
| [`system/substrate.md`](system/substrate.md) | How an agent's body works: extensions, lifecycle, communication, memory/molt, idle behavior, tool tiers | You want the mechanism — main entrance |
| [`system/lingtai.md`](system/lingtai.md) | Who this agent is: specialties, working style, relationships | You want to know the agent |
| [`system/pad.md`](system/pad.md) | Current state: active tasks, handoff notes | You want to know what it is doing now |
| [`knowledge/`](knowledge/) | Private long-term memory entries | You want the facts it remembers |
| [`.library/`](.library/) | Skill catalog (intrinsic + custom) | You want reusable procedures |
| [`mailbox/`](mailbox/) | Internal email inbox | You want to communicate with it |
| [`init.json`](init.json) | Config: agent name, LLM, context window, permissions | You want to inspect or change config |
| [`logs/`](logs/) | Runtime logs | You are troubleshooting |
| [`history/`](history/) | Chat history and molt archives | You are looking back |
| [`daemons/`](daemons/) | Ephemeral subagent (神识) artifacts | You want delegated results |
| [`taskcard/`](taskcard/) | Task Card state | You are tracking a long task |
| [`scratch/`](scratch/) | Scratch workspace | You are looking for work products |
| [`.secrets/`](.secrets/) | Credentials (name only — never read contents) | You are configuring credentials |

## Notes

- `.secrets/` contains sensitive credentials: **never** copy its contents into
  chat, logs, or public documents.
- This README is a navigation entry point only: it does not carry the agent's
  identity or any live/dynamic values. Dynamic truth lives in `init.json`
  (config), `.status.json` (runtime state), `.agent.json` (identity metadata),
  and `system/pad.md` (current notes).
- For the contract that fixes these roles, see the kernel repo:
  `src/lingtai/kernel/agent_readme/CONTRACT.md`.
