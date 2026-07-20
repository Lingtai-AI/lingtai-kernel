---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/auth/__init__.py
  - src/lingtai/auth/codex.py
  - src/lingtai/auth/codex_pool.py
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/intrinsic_skills/system-manual/reference/environment-variables/SKILL.md
maintenance: |
  Keep related_files as repo-relative paths to real files. Include neighboring
  ANATOMY.md files so the anatomy graph stays connected rather than isolated;
  anatomy links must be bidirectional. If you create a new ANATOMY.md, copy this
  maintenance field. If you notice drift between this anatomy and the code,
  report it. See lingtai-dev-guide for details.
---
# src/lingtai/auth/

Codex OAuth token management — reads TUI-written tokens, checks expiry, auto-refreshes via OpenAI OAuth endpoint.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 1 | Docstring-only package marker |
| `codex.py` | 220 | `CodexTokenManager` — reads/refreshes OAuth tokens |
| `codex_pool.py` | 611 | Provider `codex-pool` — sticky weighted choice of which token file to read, quota-aware exclusion at new selection, and request-scoped usage-limit failover helpers |

**Key functions** (`codex_pool.py`):
- `select_codex_pool_auth()` (`codex_pool.py:317`) — picks one enabled account from the non-secret pool file (`codex-auth-pool.json`, `resolve_codex_pool_path`, `codex_pool.py:81`) by weighted hash of a sticky per-agent-session seed (`_selection_seed`, `codex_pool.py:200` — anchor + `.agent.json` `started_at`, molt-independent). Returns the resolved token path for injection as `codex_auth_path` plus a non-secret `selection` dict (`source_ref` — redacted if absolute — /`source_index`/`pool_size`/`weight`/`auth_path_sha8`/`model_scope`) that the `codex-pool` factory stamps on the adapter and its chats for `llm_call` event attribution. `select_codex_pool_auth_path()` is the path-only view. Never reads token file contents; missing/empty/invalid pool → `None` (caller falls back to `legacy_codex_token_path()`, `codex_pool.py:98`).
- **Quota-aware exclusion at new selection** — before the weighted pick, `select_codex_pool_auth()` drops any account for which `_is_proven_exhausted()` (`codex_pool.py:302`) returns `True` (a fresh `lingtai.llm.openai.codex_quota.read_remaining_percent()` result `<= 0`); a `None` result (unavailable/error) fails open. If every account is excluded it raises `CodexPoolAllAccountsExhaustedError` (`codex_pool.py:84`) instead of falling back. Tests: `tests/test_codex_pool_quota_exclusion.py`.
- **Model-classified pools (v2)** — a pool file may carry a top-level `models` map keyed by EXACT model string instead of the flat `accounts` list (`_load_pool_entries`, `codex_pool.py:142`). The factory's configured model picks the category; eligibility, `source_index`, and `pool_size` are category-relative, and `selection.model_scope` records the exact key used (`None` on flat v1 pools). Exact case-sensitive equality only — no prefix/family/wildcard/default matching; when `models` is present it is the sole source of truth (`accounts` beside it is ignored); a model with no exact category behaves like an unusable pool (legacy fallback). The seed excludes the model, so flat v1 pools pick identically regardless of model (zero churn), and the kernel keys off structure, never the `version` field. Tests: `tests/test_codex_pool.py`.
- **Request-scoped usage-limit failover** — `_is_usage_limit_reached_error()` (`codex_pool.py:464`) returns `True` iff a provider error is STRUCTURALLY a `429` (numeric status from `status_code`/`status`/`response.status_code`, never `str(exc)`) whose machine code is exactly `usage_limit_reached` in a repo-trusted structured location (`exc.code`/`body.error.code`/`body.error.type`/top-level `body.code` — the established `_task_card_api_code` idiom). `_codex_pool_failover_candidates()` (`codex_pool.py:504`) returns the request-scoped SWITCH SEQUENCE to fail over through: the SAME validated pool snapshot (model-category-relative for v2), ANCHORED to the ACTUAL selected occurrence and walked forward with wrap. The anchor's authoritative source is `selected_source_index` — the exact index weighted selection chose (`select_codex_pool_auth`'s `selection["source_index"]`, threaded by the factory) — so a pool with duplicate/aliased entries resolving to one file anchors to the occurrence that was actually picked and the sequence begins with the siblings configured right after THAT occurrence, not merely the first path match. Only when the index is absent/out-of-range (a direct/internal caller that did not thread it) does it fall back DEFENSIVELY to an EXACT resolved-path string scan (the same relative-to-TUI resolution the selection used — NO `realpath`, no alias collapsing), then to the pool head. This is a SWITCH/RETRY budget, NOT a distinct-account budget: the sequence is walked VERBATIM with NO realpath/alias dedup — a `usage_limit_reached` can be transient/soft, so repeated path entries, aliases resolving to the same file (`same.json`/`./same.json`/absolute/symlink alias), and revisits/wraps back to the originally-selected account are ALL attempted, each counting as one switch. Exactly `MAX_FAILOVER_SWITCHES` (=10, `codex_pool.py:78`) candidates are returned whenever the pool has ≥1 account (walking as many wraps as needed); an empty pool → `[]` so the caller fails loud. Weights govern only the initial pick. Each candidate's `source_ref` is redacted (`_safe_source_ref`, `codex_pool.py:490` → `ABSOLUTE_REF_REDACTED`, `codex_pool.py:487`) when the configured ref is absolute (or `~`-expanding), so no absolute token-file path leaks; `select_codex_pool_auth`'s primary `source_ref` is redacted the same way. Both helpers are pure and non-secret (no token file read); the `codex-pool` factory wires them into a per-chat send wrapper (see `src/lingtai/llm/ANATOMY.md`). Tests: `tests/test_codex_pool.py`.

**Key classes** (`codex.py`):
- `CodexTokenManager` (L62) — main API: `is_authenticated()` (L78), `get_access_token()` (L86), `get_account_id()` (L100). Reads a Codex OAuth token file, auto-refreshes when within 5 min of expiry (`REFRESH_BUFFER_SECONDS`, L21). The path defaults to `~/.lingtai-tui/codex-auth.json` (or `LINGTAI_TUI_DIR`), but a non-empty `token_path` constructor arg selects a different file — this is how a Codex preset/manifest's `llm.codex_auth_path` points one agent at its own token file (true multiple Codex accounts). The factory (`_register.py:_codex`) forwards `codex_auth_path` as `token_path` when set and non-blank.
  - `get_account_id()` returns the user's OWN ChatGPT account id (non-secret) for the `ChatGPT-Account-ID` header, or `None`. Source priority: an explicit `account_id` / `chatgpt_account_id` field in `codex-auth.json`, else the namespaced `https://api.openai.com/auth.chatgpt_account_id` claim decoded locally from the `id_token` JWT (`_decode_jwt_payload`, L31 — base64url-only, NO signature verification, non-raising). Never invents a value; missing/malformed → `None`.
- `CodexAuthError` (L54) — raised on 401/403 from refresh endpoint, user-facing message points to `/login`.

## Connections

- **No intra-wrapper imports for `codex.py`.** Self-contained — only stdlib, `httpx`, `filelock`. `codex_pool.py` lazily imports `lingtai.llm.openai.codex_quota.read_remaining_percent` inside `_is_proven_exhausted()`.
- **Reads disk token file** written by the TUI (`LINGTAI_TUI_DIR` env or `~/.lingtai-tui/`, L35-36).
- **Calls** `https://auth.openai.com/oauth/token` (L17) for token refresh.
- **Referenced by**: the Codex LLM adapter registry (`src/lingtai/llm/_register.py`), which uses ChatGPT OAuth tokens for the `codex` provider and calls `codex_pool.select_codex_pool_auth()` in its `codex-pool` factory.

## Composition

Flat — two sibling modules (`codex.py`, `codex_pool.py`), no sub-packages. `__init__.py` re-exports nothing (just docstring). `codex_pool.py` computes only *which* token file to use; `codex.py` owns the token itself.

## State

- `_cache` / `_cache_mtime` (L39-40): mtime-based in-memory cache to avoid re-parsing the token file on every call.
- `FileLock` on `.json.lock` (L38, L99): prevents concurrent refresh races across processes.
- Token file is written atomically via `tmp_path.replace()` (L141) with `0o600` perms (L138).

## Notes

- Refresh uses `filelock` timeout of 30s (L99) — if another process holds the lock, waits then re-reads (L102-104).
- `CLIENT_ID` is hardcoded (L18) — the public Codex OAuth app ID.
- 4 commits in history; most recent adds `CodexAuthError` for graceful failure.
