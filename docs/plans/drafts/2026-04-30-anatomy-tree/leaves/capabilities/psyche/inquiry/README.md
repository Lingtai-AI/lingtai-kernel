# psyche/inquiry

## What

Inquiry is the agent's synchronous self-reflection mechanism. When the agent
calls `soul({action: "inquiry", inquiry: "question"})`, the kernel creates a
one-shot LLM session that clones the agent's conversation (text + thinking only,
no tool calls/results), sends the question, and returns the answer in the tool
result. The deep copy sees the agent's full context but thinks independently.

## Contract

| Aspect | Value |
|--------|-------|
| Tool action | `soul(action="inquiry", inquiry="question text")` |
| Validation | `inquiry` must be a non-empty string. Empty → error. |
| Cloned context | All assistant + user entries from `_chat.interface.entries`; only `TextBlock` + `ThinkingBlock` are kept. System entries and tool calls/results are stripped. |
| Session creation | Fresh `ChatInterface` each call. System prompt = soul prompt + "\n\nYou have no tools." |
| LLM config | Same model as agent (`agent._config.model`), `thinking="high"`, `tracked=False`, `tools=None`. |
| Timeout | `agent._config.retry_timeout` seconds. Runs in a daemon thread; returns `None` on timeout. |
| Return shape | `{status: "ok", voice: "<text>"}` on success, `{status: "ok", voice: "(silence)"}` if LLM returns empty. |
| Persistence | `logs/soul_inquiry.jsonl` — `{ts, mode:"inquiry", source, prompt, thinking, voice}` per entry. |
| External trigger | `.inquiry` signal file in working dir. Contents: `"<source>\n<question>"`. Atomic rename `.inquiry` → `.inquiry.taken` → spawn thread. `.inquiry.taken` deleted after completion. |
| Token accounting | Inquiry tokens are logged to `logs/token_ledger.jsonl` with `source: "soul"`. |

**Key difference from flow:** Inquiry is a fresh one-shot session (no
persistence, no cursor). Flow uses a persistent mirrored session. Inquiry
returns the answer directly in the tool result; flow injects it into the inbox.

## Source

- Handler dispatch: `intrinsics/soul.py:52` — `action == "inquiry"` branch
- Inquiry function: `intrinsics/soul.py:337` — `soul_inquiry()`
- Clone logic: `intrinsics/soul.py:345-359` — `ChatInterface()` construction
- Timeout wrapper: `intrinsics/soul.py:88` — `_send_with_timeout()`
- Persistence: `base_agent.py:648` — `_persist_soul_entry(mode="inquiry")`
- Signal file trigger: `base_agent.py:807-837` — `.inquiry` file detection
- Token ledger: `intrinsics/soul.py:236` — `_write_soul_tokens()`

## Related

- **psyche/soul-flow** — automatic persistent subconscious (vs on-demand inquiry).
- **soul({action: "delay"})** — adjusts flow frequency; does not affect inquiry.
- **logs/soul_inquiry.jsonl** — persistent log of all inquiries.
- **.inquiry signal file** — external trigger path (e.g., from TUI `/btw`).
- **DEPENDENCY-MAP.md** — cross-capability interaction diagram (same directory tree).
