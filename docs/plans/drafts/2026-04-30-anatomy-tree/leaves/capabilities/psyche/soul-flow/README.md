# psyche/soul-flow

## What

The soul flow is the agent's automatic subconscious — a persistent mirrored LLM
session that reflects on the agent's recent "diary" (assistant text output +
thinking blocks) whenever the agent enters the IDLE state and remains idle for
`soul_delay` seconds. The soul's response is injected into the agent's inbox
prefixed with `[心流]`, driving the agent to act autonomously.

Flow is distinct from nap: nap blocks the soul timer; idle activates it.

## Contract

| Aspect | Value |
|--------|-------|
| Trigger state | IDLE only. Timer starts on state→IDLE transition (`_start_soul_timer`). |
| Delay | `soul_delay` seconds (default 120, configurable via `soul(delay=N)` or `init.json manifest.soul.delay`). |
| Timer type | `threading.Timer` (daemon). Cancelled on any state→non-IDLE transition. |
| Diary collection | `_collect_new_diary()` scans assistant entries from `_soul_cursor` onward; includes `TextBlock` + `ThinkingBlock` content. Cursor advances after collection. |
| Session persistence | `history/soul_history.jsonl` — `ChatInterface.to_dict()` per entry. `history/soul_cursor.json` — `{"cursor": N}`. Restored on boot via `_ensure_soul_session()`. |
| Session model | Same LLM as agent (`agent._config.model`), `thinking="high"`, `tracked=False`, no tools. |
| System prompt | `init.json soul` or `soul_file` field; falls back to i18n `soul.system_prompt`. |
| Token budget | `soul_context_limit` — oldest non-system entries trimmed when exceeded (`_trim_soul_session`). |
| Output prefix | `[心流]` (language-specific via i18n `soul.flow_prefix`). |
| Injection path | `_soul_whisper()` → `soul_flow()` → `_collect_new_diary()` → LLM call → `_persist_soul_entry()` → `_save_soul_session()` → `inbox.put(MSG_REQUEST)`. |
| Persistence log | `logs/soul_flow.jsonl` — `{ts, mode:"flow", source, prompt, thinking, voice}` per entry. |
| Nap interaction | Nap sets a flag that blocks `_start_soul_timer`. Soul does NOT fire during nap. |

**Key invariant:** The soul session persists across molts — `reset_soul_session()`
only resets the diary cursor, not the session itself. The soul retains its
conversation history across the agent's lifetimes.

## Source

- Soul intrinsic: `intrinsics/soul.py:258` — `soul_flow()`
- Diary collector: `intrinsics/soul.py:118` — `_collect_new_diary()`
- Session management: `intrinsics/soul.py:192` — `_ensure_soul_session()`
- Token trimming: `intrinsics/soul.py:293` — `_trim_soul_session()`
- Timer start: `base_agent.py:608` — `_start_soul_timer()`
- Timer fire: `base_agent.py:631` — `_soul_whisper()`
- State transition trigger: `base_agent.py:603` — `AgentState.IDLE` check
- Cursor reset on molt: `intrinsics/soul.py:324` — `reset_soul_session()`
- i18n system prompt: `i18n/en.json:10` — `soul.system_prompt`
- Session persistence: `intrinsics/soul.py:172` — `_save_soul_session()`

## Known Limitations

1. **`soul_flow.jsonl` is append-only with no rotation.** The log grows without
   bound across the agent's lifetime. This is cosmetic — the file is never
   re-read by the mechanism (the soul reads conversation history, not this log)
   — but could fill disk over very long runs. Consider periodic manual truncation.
2. **Cursor tracks conversation diary, not soul_flow.jsonl.** After molt, the
   cursor resets to 0 and points into the (now empty) conversation history.
   `soul_flow.jsonl` persists across molt unchanged. The two files are
   independent: one is the source (conversation turns), the other is the
   output log (soul's reflections).

## Related

- **psyche/inquiry** — on-demand synchronous self-question (vs automatic flow).
- **system(nap)** — blocks soul flow; idle is the preferred resting state.
- **system(sleep)** — keeps process alive; soul flow does not fire (agent is ASLEEP).
- **eigen context/molt** — calls `reset_soul_session()` to reset the diary cursor.
- **DEPENDENCY-MAP.md** — cross-capability interaction diagram (same directory tree).
