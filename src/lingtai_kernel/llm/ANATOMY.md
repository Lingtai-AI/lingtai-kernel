# llm

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues (mail or `discussions/<name>-patch.md`); do not silently fix.

Provider-agnostic LLM protocol layer. This folder defines the canonical chat log, normalized response/tool schema types, streaming accumulation, and ABCs the kernel uses; concrete provider adapters live in the wrapper package under `src/lingtai/llm/`.

## Components

- `llm/__init__.py` — public re-export surface for `ChatSession`, `LLMResponse`, `ToolCall`, `FunctionSchema`, and `LLMService` (`llm/__init__.py:2-10`).
- `llm/base.py` — normalized dataclasses plus `ChatSession` ABC.
  - `ToolCall`, `UsageMetadata`, `LLMResponse`, and `FunctionSchema` define tool calls, token usage, provider responses, and tool schemas (`llm/base.py:21-88`).
  - `ChatSession` requires an `interface` property and `send()` accepting text or tool results (`llm/base.py:110-131`), then supplies default helpers for history/state, usage totals, streaming fallback, tool-result commits, tool/system updates, reset, interaction id, and context window (`llm/base.py:133-245`).
- `llm/interface.py` — canonical conversation representation.
  - Content blocks: `TextBlock`, `ToolCallBlock`, `ToolResultBlock`, `ThinkingBlock`; `ContentBlock` union and `content_block_from_dict()` (`llm/interface.py:34-124`).
  - `InterfaceEntry` is one role+content row with id, role, timestamp, provider metadata, model/provider, usage, and optional tool snapshot (`llm/interface.py:132-190`).
  - `ChatInterface` is the append-only source of truth for history (`llm/interface.py:199-221`). It appends system/user/assistant/tool-result entries (`llm/interface.py:373-511`), enforces/repairs tool-call pairing (`llm/interface.py:245-370`), removes strict synthetic pairs (`llm/interface.py:513-590`), prunes history (`llm/interface.py:678-727`), estimates tokens (`llm/interface.py:753-800`), and supports compaction summaries (`llm/interface.py:802-875`).
- `llm/service.py` — `LLMService` ABC: `model`, `provider`, `create_session()`, `generate()`, and `make_tool_result()` (`llm/service.py:16-70`).
- `llm/streaming.py` — `StreamingAccumulator`, which gathers streaming text/thought/tool-call deltas and finalizes to `LLMResponse` (`llm/streaming.py:16-69`). It supports sequential tool-call assembly (`llm/streaming.py:71-84`), index-keyed deltas (`llm/streaming.py:88-117`), atomic tool calls (`llm/streaming.py:121-126`), and `_finalize_tool()` (`llm/streaming.py:173-180`).

## Connections

- `base_agent.py` imports kernel LLM types for service injection, tool execution, and synthetic history repair (`base_agent.py:31-35`, `base_agent.py:752`, `base_agent.py:1012`, `base_agent.py:1208`).
- `session.py` imports `ChatSession`, `FunctionSchema`, `LLMResponse`, and `LLMService` to own session lifecycle and token/context bookkeeping (`session.py:12-17`).
- `tool_executor.py` consumes `ToolCall` (`tool_executor.py:8`); `tc_inbox.py` consumes `ToolCallBlock`/`ToolResultBlock` for synthetic pairs (`tc_inbox.py:33`).
- `intrinsics/psyche.py` and `intrinsics/soul/` use canonical blocks/interfaces for molt replay and soul-flow consultation (`intrinsics/psyche.py:37`, `intrinsics/soul/inquiry.py:15`, `intrinsics/soul/consultation.py:196`, `intrinsics/soul/consultation.py:359`, `intrinsics/soul/consultation.py:499`).
- Outbound from this folder is minimal: `ChatInterface.estimate_context_tokens()` lazy-imports `token_counter.count_tokens` (`llm/interface.py:764`).
- Wrapper boundary: `src/lingtai/llm/service.py` provides the concrete `LLMService` subclass (`src/lingtai/llm/service.py:25`); wrapper adapters import kernel types, but the kernel does not import the wrapper.

## Composition

- **Parent:** `src/lingtai_kernel/` (see `ANATOMY.md`).
- **Subfolders:** none.
- **Siblings:** `session.py` persists and compacts `ChatInterface`; `token_ledger.py` persists usage; `intrinsics/` manufactures synthetic LLM blocks for psyche/soul/email flows.

## State

- **Ephemeral:** `ChatInterface._entries`, `_next_id`, current system/tools, and `_pending_system` live in memory for one session (`llm/interface.py:208-217`).
- **Ephemeral:** `StreamingAccumulator` stores partial text, tool args, thoughts, and usage until `finalize()` (`llm/streaming.py:39-69`).
- **Persistent writes:** none in this folder. `session.py` writes `history/chat_history.jsonl`; token/state persistence happens in sibling modules that consume these types.

## Notes

- `add_system()` defers system/tool updates while the tail has unanswered tool calls so strict providers do not see a system entry between assistant tool calls and user tool results (`llm/interface.py:373-422`).
- `close_pending_tool_calls()` synthesizes abort `ToolResultBlock`s with guidance to verify side effects before retrying (`llm/interface.py:344-370`, `llm/interface.py:66-94`).
- `StreamingAccumulator` intentionally supports three provider styles in one place: sequential, index-keyed, and atomic tool calls (`llm/streaming.py:71-126`).
