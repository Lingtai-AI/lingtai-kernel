---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/auth/__init__.py
  - src/lingtai/auth/codex.py
  - src/lingtai/auth/codex_account_source.py
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/intrinsic_skills/system-manual/reference/subs-pool/SKILL.md
  - ENVIRONMENT_VARIABLES.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
  Capability mentions in any document require explicit bidirectional
  related_files mapping to the implementing code (see root ## Maintenance).
---
# src/lingtai/auth/

Codex OAuth token management — reads TUI-written tokens, checks expiry, auto-refreshes via OpenAI OAuth endpoint. Also owns the single-account `FixedAccountSource` the native Codex adapter binds: an explicit `codex_auth_path`, else the default `<tui_dir>/codex-auth.json`. LingTai has no Codex account pool; pooling is the external subs-pool proxy (`src/lingtai/intrinsic_skills/system-manual/reference/subs-pool/SKILL.md`). The source owns only candidate identity; `CodexOpenAIAdapter` owns token refresh, transport, safe attribution, and failure classification, while the kernel owns AED.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 1 | Docstring-only package marker |
| `codex.py` | 330 | `CodexTokenManager` — reads/refreshes OAuth tokens; default-path helpers; the STRUCTURAL `usage_limit_reached` and `token_expired` error classifiers |
| `codex_account_source.py` | 61 | `AccountCandidate` + `NoCandidateError` + `FixedAccountSource` — the one account and its exclusion |

**Key classes/functions** (`codex.py`):
- `resolve_codex_tui_dir()` (L34) / `default_codex_token_path()` (L40) — the TUI base dir (`LINGTAI_TUI_DIR` or `~/.lingtai-tui`) and the default token file `<tui_dir>/codex-auth.json`.
- `CodexTokenManager` (L76) — main API: `is_authenticated()` (L91), `get_access_token()` (L99), `refresh_access_token(rejected_access_token)` (L113), `get_account_id()` (L129). Reads a Codex OAuth token file, auto-refreshes when within 5 min of expiry (`REFRESH_BUFFER_SECONDS`, L21). The path defaults to `default_codex_token_path()`, but a non-empty `token_path` constructor arg selects a different file — this is how a preset/manifest's `llm.codex_auth_path` points one agent at its own token file. The factory (`_register.py:_codex`) builds `FixedAccountSource(codex_auth_path or default path)`; the adapter constructs `CodexTokenManager(token_path=...)` only when a real request binds that source.
  - `refresh_access_token()` is the ordinary-request `401/token_expired` seam. It takes the access token rejected by the provider, enters the existing per-auth-file `FileLock`, invalidates the caller's cache, and reloads the file under that lock. If a concurrent owner already persisted a different usable token, it reuses that token; otherwise it forces the existing OAuth refresh even when the stale token's local expiry claims it is valid. OAuth refresh-endpoint 401/403 still raises `CodexAuthError` with the existing `/login` guidance.
  - `get_account_id()` returns the user's OWN ChatGPT account id (non-secret) for the `ChatGPT-Account-ID` header, or `None`. Source priority: an explicit `account_id` / `chatgpt_account_id` field in `codex-auth.json`, else the namespaced `https://api.openai.com/auth.chatgpt_account_id` claim decoded locally from the `id_token` JWT (`_decode_jwt_payload`, L45 — base64url-only, NO signature verification, non-raising). Never invents a value; missing/malformed → `None`.
- `CodexAuthError` (L68) — raised on 401/403 from refresh endpoint, user-facing message points to `/login`.
- `_is_usage_limit_reached_error()` (L322) — returns `True` iff a provider error is STRUCTURALLY a `429` (`_structured_status_code`, L277 — numeric status from `status_code`/`status`/`response.status_code`, never `str(exc)`) whose machine code is exactly `usage_limit_reached` (`_USAGE_LIMIT_CODE`, L273) in a repo-trusted structured location (`_structured_error_codes`, L293 — `exc.code`/`body.error.code`/`body.error.type`/top-level `body.code`). `CodexOpenAIAdapter` consults this only for the terminal error escaping one provider send, to exclude the bound account; the next selection then raises `NoCandidateError`, which the kernel treats as terminal for the turn.
- `_is_token_expired_error()` (L309) — returns `True` only for an ordinary provider error with structural HTTP status `401` and exact machine code `token_expired` in the same trusted locations. It never classifies generic exception text, status-less errors, refresh-endpoint failures, or WebSocket failures without this HTTP structure; the Codex session uses it only for its bounded pre-output request replay.

**Key classes** (`codex_account_source.py`) — candidate identity only, no network/retry/chat/transport/ledger:
- `AccountCandidate` (L19) — frozen dataclass: `auth_ref` plus derived `auth_path_sha8` (`__post_init__`, L30 — first 8 hex chars of SHA-256 of `auth_ref`, the stable non-secret exclusion/attribution identity).
- `NoCandidateError` (L38) — the one account is excluded for this turn; `diagnostic_fields()` (L45) returns no fields.
- `FixedAccountSource` (L49) — `select(exclude=None)` (L58) always returns the same account; `exclude` containing its identity raises `NoCandidateError`.

## Connections

- **No intra-wrapper imports.** `codex.py` uses only stdlib, `httpx`, `filelock`; `codex_account_source.py` only stdlib.
- **Reads disk token file** written by the TUI (`LINGTAI_TUI_DIR` env or `~/.lingtai-tui/`, `codex.py` L34-42).
- **Calls** `https://auth.openai.com/oauth/token` (`codex.py` L19) for token refresh.
- **Referenced by**: the Codex LLM factory (`src/lingtai/llm/_register.py:_codex`) supplies a `FixedAccountSource` to `CodexOpenAIAdapter`; the vision tool resolves the same default token path. See `src/lingtai/llm/ANATOMY.md`.

## Composition

Flat — two sibling modules (`codex.py`, `codex_account_source.py`), no sub-packages. `__init__.py` re-exports nothing (just docstring). `codex.py` owns the token itself plus the structural failure classifiers; `codex_account_source.py` names the one account.

## State

- `_cache` / `_cache_mtime` (`codex.py` L84-85, set in `CodexTokenManager.__init__`): mtime-based in-memory cache to avoid re-parsing the token file on every call; invalidated on write (L263-264).
- `FileLock` on `.json.lock` (`codex.py` L83 constructs the path, L205 acquires with a 30s timeout): prevents concurrent refresh races across processes.
- Token file is written atomically via `tmp_path.replace()` (L260) with `0o600` perms (L257).
- `codex_account_source.py` holds no persistent state.

## Notes

- `CLIENT_ID` is hardcoded (`codex.py` L20) — the public Codex OAuth app ID.
- The in-kernel Codex account pool (`codex-pool`/`codex_pool` providers, `codex_auth_pool_path`, `codex-auth-pool.json`, weighted/quota-aware selection) was removed. Init validation rejects the old provider spellings with a pointer to subs-pool.
