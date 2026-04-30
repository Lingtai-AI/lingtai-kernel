# Molt Protocol

> **Subsystem:** core / molt-protocol
> **Layer:** Runtime context management

---

## What

Molt (凝蜕) is the context-reset ritual. When context fills, the agent sheds conversation history while preserving durable identity stores. Same process continues — chat wiped, summary injected as first message. Two thresholds drive a progressive warning ladder.

---

## Contract

### Thresholds

| Threshold | Default | Config | Behavior |
|-----------|---------|--------|----------|
| Soft | 0.70 (70%) | `manifest.molt_pressure` | Warning ladder begins; +1 warning per turn |
| Hard ceiling | 0.95 (95%) | `config.py:33` | Unconditional force-wipe |
| Warning limit | 5 | `config.py:32` | Auto-molt after 5 accumulated warnings |

### Warning ladder (three levels)

| Warnings | Level | Content | Extra |
|----------|-------|---------|-------|
| 1 | 1 | Gentle pressure reminder | — |
| 2 | 2 | Escalated text | + molt procedure appended |
| 3-5 | 3 | "URGENT — forced wipe next turn" | + molt procedure appended |

Level = `min(warnings, 3)`. Custom `manifest.molt_prompt` overrides default text entirely.

### Four triggers

| Trigger | Control |
|---------|---------|
| `psyche(context, molt, summary=...)` | Agent writes its own summary |
| `pressure >= 0.95` | None — system force-wipes |
| `pressure >= 0.70` + 5 warnings | Partial — 5 turns to prepare |
| `.clear` file signal | None — system-authored summary |

### Molt steps (`_context_molt()`)

1. Validate summary (non-empty)
2. Wipe chat session (`_chat = None`)
3. Reset warning counter
4. Increment `molt_count` (persisted in `.agent.json`)
5. Archive `chat_history.jsonl` → `chat_history_archive.jsonl`
6. Reset soul cursor (session preserved)
7. Run post-molt hooks (reload lingtai/pad)
8. `ensure_session()` → fresh LLM session
9. Inject `[Carried forward]\n{summary}` as first user message

### What survives vs what clears

**Survives:** identity, molt_count, pad, lingtai, covenant, principle, procedures, brief, rules, soul session, codex, mailbox, library, delegates, MCP registry, chat archive.

**Cleared:** LLM chat session, interaction ID, warning counter, current `chat_history.jsonl` (archived first), soul cursor (reset).

### Four durable stores (tend before molt)

| Store | Tool | Holds |
|-------|------|-------|
| lingtai | `psyche(lingtai, update)` | Identity |
| pad | `psyche(pad, edit)` | Working state |
| codex | `codex(submit)` | Permanent facts |
| library | `write .library/custom/` | Reusable skills |

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|------|------|---------|
| `_context_molt()` | `intrinsics/eigen.py` | 124 |
| `context_forget()` | `intrinsics/eigen.py` | 218 |
| Warning ladder logic | `base_agent.py` | 1152-1198 |
| Hard ceiling check | `base_agent.py` | 1161-1167 |
| Chat archive | `eigen.py` | 151-161 |
| Soul cursor reset | `eigen.py` | 164-165 |
| Post-molt hooks | `eigen.py` | 168-172 |
| Summary injection | `eigen.py` | 179-181 |
| Defaults: pressure=0.70, warnings=5, ceiling=0.95 | `config.py` | 31-33 |
| Psyche post-molt hook | `core/psyche/__init__.py` | 333-336 |

---

## Related

| Sibling leaf | Relationship |
|--------------|-------------|
| `init/init-schema` | `molt_pressure`/`molt_prompt` validated at init |
| `core/wake-mechanisms` | Post-molt agent is IDLE; soul flow triggers |
| `capabilities/mail/mailbox-core` | Mailbox survives molt |
