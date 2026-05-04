# src/lingtai/auth/

Codex OAuth token management — reads TUI-written tokens, checks expiry, auto-refreshes via OpenAI OAuth endpoint.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 1 | Docstring-only package marker |
| `codex.py` | 145 | `CodexTokenManager` — reads/refreshes OAuth tokens |

**Key classes** (`codex.py`):
- `CodexTokenManager` (L30) — main API: `is_authenticated()` (L46), `get_access_token()` (L54). Reads `~/.lingtai-tui/codex-auth.json`, auto-refreshes when within 5 min of expiry (`REFRESH_BUFFER_SECONDS`, L19).
- `CodexAuthError` (L22) — raised on 401/403 from refresh endpoint, user-facing message points to `/login`.

## Connections

- **No intra-wrapper imports.** Self-contained — only stdlib, `httpx`, `filelock`.
- **Reads disk token file** written by the TUI (`LINGTAI_TUI_DIR` env or `~/.lingtai-tui/`, L35-36).
- **Calls** `https://auth.openai.com/oauth/token` (L17) for token refresh.
- **Referenced by**: `lingtai.core.codex` (the codex capability reads access tokens).

## Composition

Flat — single module, no sub-packages. `__init__.py` re-exports nothing (just docstring).

## State

- `_cache` / `_cache_mtime` (L39-40): mtime-based in-memory cache to avoid re-parsing the token file on every call.
- `FileLock` on `.json.lock` (L38, L99): prevents concurrent refresh races across processes.
- Token file is written atomically via `tmp_path.replace()` (L141) with `0o600` perms (L138).

## Notes

- Refresh uses `filelock` timeout of 30s (L99) — if another process holds the lock, waits then re-reads (L102-104).
- `CLIENT_ID` is hardcoded (L18) — the public Codex OAuth app ID.
- 4 commits in history; most recent adds `CodexAuthError` for graceful failure.
