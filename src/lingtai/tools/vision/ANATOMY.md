---
related_files:
  - src/lingtai/tools/ANATOMY.md
  - src/lingtai/tools/vision/__init__.py
  - src/lingtai/tools/vision/CONTRACT.md
  - src/lingtai/tools/vision/glossary-en.md
  - src/lingtai/tools/vision/glossary-zh.md
  - src/lingtai/tools/vision/glossary-wen.md
  - src/lingtai/tools/vision/manual/SKILL.md
  - src/lingtai/services/vision/ANATOMY.md
maintenance: |
  Keep related_files as repo-relative paths to real files and keep anatomy links
  reciprocal. Update citations with structural code changes and run the document
  validators after edits.
---
# src/lingtai/tools/vision/

The `vision` tool registers direct current-preset image analysis plus a
provider-neutral manual route when direct setup is unavailable.

## Components

- `__init__.py:34-41` — exact same-provider alias check; only GLM/Zhipu and codex-pool spelling pairs share current identity.
- `__init__.py:44-52` — exact advertised provider registry; the local pseudo-provider remains explicit opt-in and intentionally excluded.
- `__init__.py:58-76` — compatible tool schema; neither action requires an image path at schema level.
- `__init__.py:80-128` — `VisionManager`; `manual` reads bundled guidance without a backend, while `analyze` validates and reads the image.
- `__init__.py:131-379` — `setup`; resolves only the same current model/endpoint/credential/headers/wire, creates supported services, fails closed to manual guidance when identity is incomplete, and always registers the tool.

## Connections

- Setup lazily reaches `lingtai.services.vision` and the Codex pool selector.
- Direct compatible aliases (`openrouter`, `deepseek`, `zhipu`, `glm`, `grok`,
  `qwen`, `kimi`, `custom`) use current OpenAI/Anthropic-compatible identity.
- MiniMax uses Anthropic; Codex aliases use the Codex service; Claude Code and
  unresolved/unsupported routes remain manual-only. No MCP fallback is used.

## Composition

`VisionManager` owns the agent, optional service, and safe manual reason. The
capability is registered by the built-in capability loader and registers one
`vision` tool with the schema and glossary package.

## State

Only the in-memory manager/service references persist. Manual content is bundled
with the package; analyses are not persisted.

## Notes

Setup failures retain provider plus exception type, never exception text. Direct
request failures likewise expose only the exception type and a manual pointer.
Active MiMo Responses/other unsupported wires are manual-only; supported Chat
Completions does not receive unsupported headers or wire kwargs.
