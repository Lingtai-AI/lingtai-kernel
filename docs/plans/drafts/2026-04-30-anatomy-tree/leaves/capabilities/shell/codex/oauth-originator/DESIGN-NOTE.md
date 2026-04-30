# Design Notes: Codex Subsystem Issues

> These are NOT part of the anatomy leaves. They are design observations surfaced during leaf authoring.

---

## Issue 1: Name collision — "codex" means two things

**Problem.** The kernel uses "codex" for two completely unrelated subsystems:

| Subsystem | Module | What it does |
|---|---|---|
| Codex knowledge store | `core/codex/__init__.py` | Persistent knowledge archive (submit/filter/view/consolidate/delete/export) |
| Codex LLM provider | `auth/codex.py` + `llm/_register.py:54–82` | OAuth2 token manager for ChatGPT backend API |

They share:
- The word "codex" in class names (`CodexManager` vs `CodexTokenManager`)
- No shared state, no shared files, no runtime interaction

This creates confusion in:
- **Conversation**: "use codex to…" — which one?
- **File paths**: `codex/codex.json` (knowledge store) vs `~/.lingtai-tui/codex-auth.json` (LLM tokens)
- **Error messages**: a "codex" error could come from either subsystem
- **Documentation**: every mention needs a disambiguator

**Root cause.** "Codex" was the internal OpenAI product name for what became ChatGPT. The LLM adapter was named after the product. The knowledge store was built alongside it and inherited the name.

**Possible resolutions:**
1. **Disambiguate in docs only** (what the anatomy leaves do now — explicit header warnings)
2. **Rename the LLM adapter** to `chatgpt` or `openai-codex` — the adapter is already backed by `OpenAIAdapter`, so `provider: "chatgpt"` would be more descriptive
3. **Rename the knowledge store** to something else (e.g. `archive`, `lore`) — but this changes the tool name visible to agents

Option 2 is lowest-risk: the adapter is a thin OAuth wrapper around OpenAI, and "codex" as a provider name is already confusing in preset configs (`provider: "codex"` looks like it should be related to the knowledge tool).

**Verified blast radius for Option 2** (rename LLM adapter `"codex"` → `"chatgpt"`):

| File | What changes |
|---|---|
| `llm/_register.py:82` | `register_adapter("codex", _codex)` → `register_adapter("chatgpt", _codex)` |
| `preset_connectivity.py:37` | `"codex": "https://chatgpt.com"` → `"chatgpt": "https://chatgpt.com"` |
| `~/.lingtai-tui/presets/templates/codex.json` | `"provider": "codex"` (×3) → `"provider": "chatgpt"`; rename file to `chatgpt.json` |

**NOT affected** (these use "codex" as the knowledge store capability, not the LLM provider):
- `capabilities/__init__.py:18,89` — capability registry (`"codex": "lingtai.core.codex"`)
- All `.agent.json` files with `"codex"` in capabilities arrays
- `core/codex/__init__.py` — the knowledge store itself
- `core/avatar/__init__.py:463,465` — avatar deep-copy copies `codex/` directory
- **Tests**: `test_codex.py` (all 40+ references), `test_check_caps.py`, `test_layers_avatar.py` — every `"codex"` in tests is the knowledge store capability, not the LLM provider. No test touches `register_adapter("codex")` or `provider: "codex"`.

**Name collision check:** `grep -rn "chatgpt"` across the kernel (excluding URLs) returns zero hits — the name is free.

---

## Issue 2: No provenance or dedup in knowledge store

**Problem.** Codex entries have no source tracking and no content dedup:

1. **No originator field.** An entry does not record which agent, capability, tool, or human interaction created it. After molt, the next self cannot trace *why* an entry exists or *who* submitted it.

2. **No content hash for dedup.** `_make_id` hashes `title + content + created_at` — the timestamp is always different, so identical content submitted twice produces two distinct entries that both count toward the 20-entry cap.

**Current `_make_id`** (`codex/__init__.py:158-162`):
```python
def _make_id(content: str, created_at: str) -> str:
    return hashlib.sha256((content + created_at).encode()).hexdigest()[:8]
```

**What would need to change:**

| New field | Type | Purpose |
|---|---|---|
| `source` | string (optional) | Free-text origin: agent name, tool call, manual note |
| `content_hash` | string | `sha256(title + summary + content)[:16]` — stable across timestamps |

With `content_hash`, `_submit()` could check for near-duplicates before accepting. With `source`, `_view()` could show provenance.

**Scope of change:** `CodexManager._submit()`, `_view()`, `_inject_catalog()`, entry schema, and `_load_entries()` migration for existing entries.

---

## Decision

The anatomy leaves document current implementation as-is. Both issues are flagged in the leaf headers and cross-referenced here. If either change is desired, it should be a separate kernel PR.

**Filed:** [Lingtai-AI/lingtai-kernel#2](https://github.com/Lingtai-AI/lingtai-kernel/issues/2) — rename LLM adapter from `"codex"` to `"chatgpt"`.

**Migration note** (comment on #2): Existing users with `~/.lingtai-tui/presets/templates/codex.json` will break on first boot after rename. Recommended fix: register under both names for one release cycle (`register_adapter("chatgpt", _codex)` + `register_adapter("codex", _codex)` as deprecated alias), remove alias next release.
