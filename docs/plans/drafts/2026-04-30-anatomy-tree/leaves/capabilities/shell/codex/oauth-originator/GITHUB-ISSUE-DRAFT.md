# Ready-to-paste GitHub issue: "codex" name collision

Awaiting human approval before sending.

---

## Title

Rename LLM adapter from `"codex"` to `"chatgpt"` — name collision with knowledge store capability

## Body

### Problem

The kernel uses `"codex"` as the name for two unrelated subsystems:

| Subsystem | Module | Purpose |
|---|---|---|
| Codex knowledge store | `core/codex/__init__.py` | Persistent knowledge archive (tool: `"codex"`) |
| Codex LLM provider | `auth/codex.py` + `llm/_register.py:54-82` | OAuth2 token manager for ChatGPT backend (adapter: `"codex"`) |

No shared state, no runtime interaction. The collision is purely nominal but causes confusion in:
- **Conversation**: "use codex to…" — which one?
- **Error messages**: `CodexAuthError` looks like it's about the knowledge store
- **Preset configs**: `provider: "codex"` looks related to the knowledge tool
- **Documentation**: every mention needs a disambiguator

### Root cause

"Codex" was OpenAI's internal product name for what became ChatGPT. The LLM adapter was named after the product. The knowledge store inherited the name because they were built alongside each other.

### Proposed fix

Rename the LLM adapter from `"codex"` to `"chatgpt"` in `llm/_register.py:82`:

```python
LLMService.register_adapter("chatgpt", _codex)  # was "codex"
```

Leave the knowledge store as `"codex"` — the name is apt.

### Blast radius (verified)

**3 files, ~6 line changes:**

| File | What changes |
|---|---|
| `llm/_register.py:82` | `register_adapter("codex", _codex)` → `register_adapter("chatgpt", _codex)` |
| `preset_connectivity.py:37` | `"codex": "https://chatgpt.com"` → `"chatgpt": "https://chatgpt.com"` |
| `~/.lingtai-tui/presets/templates/codex.json` | `"provider": "codex"` (×3) → `"provider": "chatgpt"`; rename file to `chatgpt.json` |

**NOT affected** (these use "codex" as the knowledge store capability):
- `capabilities/__init__.py:18,89` — capability registry (`"codex": "lingtai.core.codex"`)
- All `.agent.json` files with `"codex"` in capabilities arrays
- `core/codex/__init__.py` — the knowledge store itself
- `core/avatar/__init__.py:463,465` — avatar deep-copy copies `codex/` directory
- **Tests**: `test_codex.py` (40+ refs), `test_check_caps.py`, `test_layers_avatar.py` — every `"codex"` in tests is the knowledge store capability. No test exercises `register_adapter("codex")` or `provider: "codex"`.

**Name availability:** `grep -rn "chatgpt"` across the kernel (excluding URLs) returns zero hits.

### Severity

minor — does not break workflows, but misleads agents and humans.

### Context

Found during anatomy leaf authoring for `lingtai-kernel/docs/plans/drafts/2026-04-30-anatomy-tree/leaves/capabilities/shell/codex/`. The leaves include a `DESIGN-NOTE.md` with this analysis at `codex/oauth-originator/DESIGN-NOTE.md`.
