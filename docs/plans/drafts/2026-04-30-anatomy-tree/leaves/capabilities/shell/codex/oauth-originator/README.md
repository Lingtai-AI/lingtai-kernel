# OAuth Originator (Codex LLM Token Management)

> **Module:** `lingtai/auth/codex.py`
> **Name collision warning:** "codex" refers to TWO unrelated subsystems. This leaf covers the **codex LLM provider** — OAuth2 tokens for ChatGPT's backend API (`auth/codex.py`, registered in `llm/_register.py:54–82` as adapter `"codex"`). The other is the **codex knowledge store** (`core/codex/__init__.py`) — the agent's persistent knowledge archive. Same name, different modules, no shared state. See `codex/README.md` for the knowledge store.

---

## What

The OAuth originator pattern manages OAuth2 tokens for the "codex" LLM provider (OpenAI's ChatGPT backend API). The `CodexTokenManager` reads tokens written by the TUI, checks expiry, and auto-refreshes via the OpenAI OAuth endpoint. It wraps the OpenAI adapter so that every API call gets a fresh access token.

There is no "originator" field tracking which agent or capability created a codex knowledge entry. Codex entry IDs are content-hashed (`sha256(title + content + created_at)[:8]`) — there is no dedup by content hash. Two identical submissions at different times produce different IDs.

---

## Contract

### Token lifecycle

```
TUI writes ~/.lingtai-tui/codex-auth.json
  ↓
CodexTokenManager reads it (with mtime-based cache)
  ↓
Checks expires_at vs current time + 300s buffer
  ↓
If expired: POST to https://auth.openai.com/oauth/token
  with grant_type=refresh_token, client_id=app_EMoamEEZ73f0CkXaXp7hrann
  ↓
Writes new tokens back to same file (atomic via tmp + replace)
```

### Token file schema

```json
{
    "access_token": "ey...",
    "refresh_token": "v1...",
    "expires_at": 1714500000,
    "email": "user@example.com"
}
```

### Concurrency

A `FileLock` (`codex-auth.json.lock`) prevents concurrent refresh races. After acquiring the lock, the manager re-reads the file — another process may have already refreshed.

### Integration with LLM adapter

In `_register.py` (lines 54-82), the `_codex` factory:
1. Creates an `OpenAIAdapter` with `base_url="https://chatgpt.com/backend-api"`.
2. Stores the `CodexTokenManager` on the adapter as `adapter._codex_token_mgr`.
3. Monkey-patches `create_chat` and `generate` to call `mgr.get_access_token()` before each API call, updating `adapter._client.api_key` in-place.

### Codex knowledge store: no originator tracking

The codex knowledge store (`core/codex/__init__.py`) has:
- **No originator field** in entry schema
- **No dedup by content hash** — IDs include `created_at`, so identical content gets different IDs
- **No source tracking** — entries don't record which agent, tool, or capability created them

The `_make_id` function hashes `title + content + created_at`, making IDs deterministic only for the exact same submission at the exact same timestamp.

> **Observation (anatomy finding):** Two entries with identical title+summary+content submitted at different times both consume a slot in the 20-entry cap with no way to detect the redundancy. A `content_hash` field (sha256 of title+summary+content, excluding timestamp) would enable dedup-on-submit. A `source` field would enable provenance tracking across molts. See `DESIGN-NOTE.md` in this directory for the schema change sketch.

---

## Source

All references to `lingtai-kernel/src/`.

| What | File | Line(s) |
|---|---|---|
| Module docstring | `lingtai/auth/codex.py` | 1-4 |
| Token URL constant | `lingtai/auth/codex.py` | 17 |
| Client ID constant | `lingtai/auth/codex.py` | 18 |
| Refresh buffer (300s) | `lingtai/auth/codex.py` | 19 |
| `CodexTokenManager` class | `lingtai/auth/codex.py` | 30-145 |
| `is_authenticated()` | `lingtai/auth/codex.py` | 46-52 |
| `get_access_token()` | `lingtai/auth/codex.py` | 54-66 |
| `_read()` with mtime cache | `lingtai/auth/codex.py` | 72-90 |
| `_refresh()` with file lock | `lingtai/auth/codex.py` | 92-145 |
| `_codex` LLM factory in `_register.py` | `lingtai/llm/_register.py` | 54-82 |
| Monkey-patched `create_chat` | `lingtai/llm/_register.py` | 71-74 |
| Monkey-patched `generate` | `lingtai/llm/_register.py` | 75-79 |
| Codex entry `_make_id` (no originator) | `lingtai/core/codex/__init__.py` | 158-162 |

---

## Related

| Leaf | Relationship |
|---|---|
| `codex` (parent) | The knowledge store capability; this leaf covers the LLM provider auth that shares the "codex" name |
| `codex` entry schema | No `originator` or `source` field — entries are anonymous by design |
