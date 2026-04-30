# Capability Dependency Map

This document maps how the 4 capabilities interact with each other and
with the kernel's intrinsics. Arrows show data flow or invocation.

```
┌─────────────────────────────────────────────────────────────┐
│                        KERNEL                                │
│                                                              │
│  base_agent.py ─── _start_soul_timer() ──► [soul-flow]      │
│       │                                                      │
│       ├── AgentState.IDLE ──────────────► soul timer starts  │
│       ├── AgentState.ACTIVE ────────────► soul timer cancelled│
│       └── ASLEEP (nap/sleep) ──────────► soul timer cancelled│
│                                                              │
│  intrinsics/eigen.py ── _context_molt() ──► [core-memories]  │
│       │                                                      │
│       ├── archive chat_history → chat_history_archive        │
│       ├── reset_soul_session() ─────────► [soul-flow]        │
│       ├── _post_molt_hooks[] ───────────► [core-memories]    │
│       └── ensure_session() (fresh start)                     │
│                                                              │
│  intrinsics/soul.py ── soul_flow() ────► [soul-flow]         │
│                ├── soul_inquiry() ─────► [inquiry]           │
│                └── handle() ──────────► tool dispatch        │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│                    CAPABILITIES                              │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │   psyche/     │    │   library/   │    │   vision/    │   │
│  │              │    │              │    │              │   │
│  │  lingtai.md ─┼────┤  paths-      │    │  multimodal  │   │
│  │  pad.md     ─┼────┤  resolution  │    │              │   │
│  │  molt       ─┼────┤              │    │              │   │
│  └──────┬───────┘    └──────────────┘    └──────┬───────┘   │
│         │                                       │           │
│         │         ┌──────────────┐              │           │
│         │         │  web_search/ │              │           │
│         │         │              │              │           │
│         │         │  fallback    │              │           │
│         │         └──────────────┘              │           │
│         │                                       │           │
│  ┌──────┴───────┐                               │           │
│  │  soul/       │                               │           │
│  │              │                               │           │
│  │  soul-flow ──┼──┐                            │           │
│  │  inquiry   ──┼──┼── (cloned context) ────────┼── (may    │
│  │              │  │                            │    use)    │
│  └──────────────┘  │                            │           │
│                    │                            │           │
└────────────────────┼────────────────────────────┼───────────┘
                     │                            │
                     ▼                            ▼
              inbox (agent input)         VisionService.analyze_image()
```

## Key Interactions

### 1. soul-flow ↔ core-memories (pad)
- Soul flow reads the agent's diary (conversation text output + thinking).
- The diary does NOT contain pad content directly — pad is in the
  system prompt section, not in conversation turns.
- However, the agent's *reactions* to pad content appear in the diary.
- After molt, `reset_soul_session()` resets the diary cursor so the soul
  re-reads the agent's post-molt diary from scratch.

### 2. inquiry ↔ core-memories (lingtai + pad)
- Inquiry clones the conversation (text + thinking only).
- It does NOT read lingtai.md or pad.md directly — those are in the
  system prompt, not in conversation entries.
- The inquiry session gets its own system prompt from `soul.system_prompt`
  (i18n default) or `init.json soul` field.
- So inquiry sees the agent's *reasoning about* its identity, not the
  identity text itself.

### 3. inquiry ↔ vision
- Inquiry does NOT invoke vision. They are independent capabilities.
- However, if the agent's diary contains vision analysis results, the
  inquiry deep copy will see those results as text.

### 4. core-memories (molt) → soul-flow
- `_context_molt()` calls `reset_soul_session()` which resets
  `_soul_cursor = 0` and persists it.
- The soul session itself (persistent chat history) is NOT reset —
  the soul retains its memory across molts.
- After cursor reset, the next soul flow will re-read the agent's
  fresh post-molt diary.

### 5. library → (all capabilities)
- Library is pure presentation — it scans `.library/` paths and builds
  an XML catalog. It does not depend on or invoke other capabilities.
- Other capabilities' manuals live in `.library/intrinsic/capabilities/`
  and are read by agents when needed.

### 6. web_search fallback
- Standalone. No dependency on other capabilities.
- The fallback from unsupported provider → DuckDuckGo happens at
  `setup()` time, not at query time.

## Molt Sequence (Verified Against Source)

When `psyche({object: "context", action: "molt", summary: "..."})` is called:

```
1. psyche._context_molt(args)              # core/psyche/__init__.py:317
2.   → eigen._context_molt(agent, args)    # intrinsics/eigen.py:124
3.     archive chat_history → archive file  # eigen.py:150-161
4.     delete current chat_history.jsonl
5.     reset_soul_session()                 # eigen.py:164 (soul cursor → 0)
6.     for cb in _post_molt_hooks: cb()     # eigen.py:168 (psyche's lambda)
7.       → mgr._lingtai_load({})            # re-inject lingtai.md into covenant
8.       → mgr._pad_load({})                # re-inject pad.md + pinned refs
9.     ensure_session()                     # eigen.py:175 (fresh LLM session)
10.    inject summary as opening message    # eigen.py:181
```

Note: steps 6-8 happen BEFORE step 9. The prompt manager has the
correct lingtai + pad content before the new session is created.
