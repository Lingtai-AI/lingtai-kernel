---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/auth/__init__.py
  - src/lingtai/auth/codex.py
  - src/lingtai/auth/codex_pool.py
  - src/lingtai/llm/_register.py
  - src/lingtai/llm/openai/ANATOMY.md
  - src/lingtai/llm/openai/codex_quota.py
  - src/lingtai/tools/vision/__init__.py
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
| `codex_pool.py` | 824 | Provider `codex-pool` — sticky weighted choice of which token file to read, quota-aware exclusion at new selection, and request-scoped usage-limit failover helpers |

**Key functions** (`codex_pool.py`):
- `select_codex_pool_auth()` (`codex_pool.py:405`) — picks one enabled account from the non-secret pool file (`codex-auth-pool.json`, `resolve_codex_pool_path`, `codex_pool.py:205`) by weighted hash of a sticky per-agent-session seed (`_selection_seed`, `codex_pool.py:324` — anchor + `.agent.json` `started_at`, molt-independent). Returns the resolved token path for injection as `codex_auth_path` plus a non-secret `selection` dict (`source_ref` — redacted if absolute — /`source_index`/`pool_size`/`weight`/`auth_path_sha8`/`model_scope`) that the `codex-pool` factory stamps on the adapter and its chats for `llm_call` event attribution. `select_codex_pool_auth_path()` (`codex_pool.py:534`) is the path-only view. Quota probes use isolated copies of token files but never return or log token contents; missing/empty/invalid pool → `None` (caller falls back to `legacy_codex_token_path()`, `codex_pool.py:222`). May raise `CodexPoolAllAccountsExhaustedError` — see below.
- **Quota-aware exclusion at NEW selection ONLY** (`codex_pool.py:109-193`, wired into `select_codex_pool_auth` `codex_pool.py:473-506`): before the weighted pick, each validated (category-scoped) account is checked via `_is_proven_exhausted()` (`codex_pool.py:176`) — a bounded-TTL-cached (`_QUOTA_EXCLUSION_CACHE_TTL_SECONDS = 30.0`, `codex_pool.py:80`; `_cached_quota_dict`, `codex_pool.py:153`) call into `lingtai.llm.openai.codex_quota.read_codex_quota_snapshot()`. An account is excluded from THIS pick only when a fresh, `available` snapshot proves `remaining_percent <= 0` on the `primary` bucket's `primary` window (`_quota_remaining_percent_from_dict`, `codex_pool.py:128`) — every other case (unavailable/error/malformed/missing/absent field) FAILS OPEN and the account stays eligible; this is a fail-open-by-construction design, never guessing exhaustion from absence of proof. When nothing is excluded, selection uses the unfiltered `_weighted_pick()` (`codex_pool.py:370`) unchanged — same call shape as before this feature, preserving the exact seam existing tests/tools bind to. When one or more accounts ARE excluded, `_weighted_pick_indexed()` (`codex_pool.py:381`) runs the SAME cumulative-weight algorithm over the filtered `(original_index, account)` pairs, so configured weights are preserved exactly among the remaining eligible accounts while `source_index` in the returned selection stays the index into the FULL validated account list (required so `_codex_pool_failover_candidates`'s anchor stays correct). If every validated account is proven exhausted, raises `CodexPoolAllAccountsExhaustedError` (`codex_pool.py:109`) naming only the account count and optional public model name—not pool refs or path-derived identifiers—instead of silently falling back to the legacy default (equally likely exhausted) or picking a proven-unusable account. This exclusion pass runs ONLY at NEW selection — `select_codex_pool_auth` is called once per `codex-pool` adapter construction (`_register.py:_codex_pool`) or one `vision` manual-pool lookup (`src/lingtai/tools/vision/__init__.py`), never again on an already-created adapter/session — so it can never migrate a live sticky session, never disables/cooldowns an account, never mutates any pool/config/auth file, and never changes the deterministic failover sequence. A since-exhausted account becomes eligible again automatically once a fresh (or cache-expired) read shows positive `remaining_percent` — there is no separate cooldown/unexclusion state. `vision`'s call site wraps the call in `try/except` and degrades to the same safe `manual_reason` UX as every other setup failure there (`src/lingtai/tools/vision/__init__.py`) rather than letting the exception propagate. Tests: `tests/test_codex_pool_quota_exclusion.py`.
- **Model-classified pools (v2)** — a pool file may carry a top-level `models` map keyed by EXACT model string instead of the flat `accounts` list (`_load_pool_entries`, `codex_pool.py:266`). The factory's configured model picks the category; eligibility, `source_index`, and `pool_size` are category-relative, and `selection.model_scope` records the exact key used (`None` on flat v1 pools). Exact case-sensitive equality only — no prefix/family/wildcard/default matching; when `models` is present it is the sole source of truth (`accounts` beside it is ignored); a model with no exact category behaves like an unusable pool (legacy fallback). The seed excludes the model, so flat v1 pools pick identically regardless of model (zero churn), and the kernel keys off structure, never the `version` field. Tests: `tests/test_codex_pool.py`.
- **Request-scoped usage-limit failover** — `_is_usage_limit_reached_error()` (`codex_pool.py:677`) returns `True` iff a provider error is STRUCTURALLY a `429` (numeric status from `status_code`/`status`/`response.status_code`, never `str(exc)`) whose machine code is exactly `usage_limit_reached` in a repo-trusted structured location (`exc.code`/`body.error.code`/`body.error.type`/top-level `body.code` — the established `_task_card_api_code` idiom). `_codex_pool_failover_candidates()` (`codex_pool.py:717`) returns the request-scoped SWITCH SEQUENCE to fail over through: the SAME validated pool snapshot (model-category-relative for v2), ANCHORED to the ACTUAL selected occurrence and walked forward with wrap — unaffected by quota-aware exclusion, which only narrows the INITIAL pick, never this sequence. The anchor's authoritative source is `selected_source_index` — the exact index weighted selection chose (`select_codex_pool_auth`'s `selection["source_index"]`, threaded by the factory) — so a pool with duplicate/aliased entries resolving to one file anchors to the occurrence that was actually picked and the sequence begins with the siblings configured right after THAT occurrence, not merely the first path match. Only when the index is absent/out-of-range (a direct/internal caller that did not thread it) does it fall back DEFENSIVELY to an EXACT resolved-path string scan (the same relative-to-TUI resolution the selection used — NO `realpath`, no alias collapsing), then to the pool head. This is a SWITCH/RETRY budget, NOT a distinct-account budget: the sequence is walked VERBATIM with NO realpath/alias dedup — a `usage_limit_reached` can be transient/soft, so repeated path entries, aliases resolving to the same file (`same.json`/`./same.json`/absolute/symlink alias), and revisits/wraps back to the originally-selected account are ALL attempted, each counting as one switch. Exactly `MAX_FAILOVER_SWITCHES` (=10, `codex_pool.py:85`) candidates are returned whenever the pool has ≥1 account (walking as many wraps as needed); an empty pool → `[]` so the caller fails loud. Weights govern only the initial pick. Each candidate's `source_ref` is redacted (`_safe_source_ref`, `codex_pool.py:703` → `ABSOLUTE_REF_REDACTED`, `codex_pool.py:700`) when the configured ref is absolute (or `~`-expanding), so no absolute token-file path leaks; `select_codex_pool_auth`'s primary `source_ref` is redacted the same way. Both helpers are pure and non-secret (no token file read); the `codex-pool` factory wires them into a per-chat send wrapper (see `src/lingtai/llm/ANATOMY.md`). Tests: `tests/test_codex_pool.py`.
- **Per-account quota reporting (read-only)** — `list_codex_pool_quota_snapshots()` (`codex_pool.py:560`) returns a safe, per-account Codex quota/rate-limit snapshot for every validated pool entry (reusing `_load_pool_entries`, never guessing a mapping); missing/unqueryable accounts surface as `quota.available=False` rather than being fabricated or dropped. Pure reporting — never touches selection, weights, or files. Tests: `tests/test_codex_quota.py`.

**Key classes** (`codex.py`):
- `CodexTokenManager` (L62) — main API: `is_authenticated()` (L78), `get_access_token()` (L86), `get_account_id()` (L100). Reads a Codex OAuth token file, auto-refreshes when within 5 min of expiry (`REFRESH_BUFFER_SECONDS`, L21). The path defaults to `~/.lingtai-tui/codex-auth.json` (or `LINGTAI_TUI_DIR`), but a non-empty `token_path` constructor arg selects a different file — this is how a Codex preset/manifest's `llm.codex_auth_path` points one agent at its own token file (true multiple Codex accounts). The factory (`_register.py:_codex`) forwards `codex_auth_path` as `token_path` when set and non-blank.
  - `get_account_id()` returns the user's OWN ChatGPT account id (non-secret) for the `ChatGPT-Account-ID` header, or `None`. Source priority: an explicit `account_id` / `chatgpt_account_id` field in `codex-auth.json`, else the namespaced `https://api.openai.com/auth.chatgpt_account_id` claim decoded locally from the `id_token` JWT (`_decode_jwt_payload`, L31 — base64url-only, NO signature verification, non-raising). Never invents a value; missing/malformed → `None`.
- `CodexAuthError` (L54) — raised on 401/403 from refresh endpoint, user-facing message points to `/login`.

## Connections

- **No intra-wrapper imports for `codex.py`.** Self-contained — only stdlib, `httpx`, `filelock`.
- **`codex_pool.py` lazily imports `lingtai.llm.openai.codex_quota`** (`read_codex_quota_snapshot`, `quota_snapshot_to_dict`) inside `_cached_quota_dict()` (`codex_pool.py:153`) and `list_codex_pool_quota_snapshots()` (`codex_pool.py:560`) — the only cross-package dependency in this module, kept lazy so importing `codex_pool` never pulls in the OpenAI adapter stack unless a quota read actually runs.
- **Reads disk token file** written by the TUI (`LINGTAI_TUI_DIR` env or `~/.lingtai-tui/`, L35-36).
- **Calls** `https://auth.openai.com/oauth/token` (L17) for token refresh.
- **Referenced by**: the Codex LLM adapter registry (`src/lingtai/llm/_register.py`), which uses ChatGPT OAuth tokens for the `codex` provider and calls `codex_pool.select_codex_pool_auth()` in its `codex-pool` factory (uncaught — a `CodexPoolAllAccountsExhaustedError` propagates as the adapter-construction failure); and `src/lingtai/tools/vision/__init__.py`'s manual codex-pool vision lookup, which wraps the same call in `try/except` and degrades to `manual_reason` on any exception (including the new one).

## Composition

Flat — two sibling modules (`codex.py`, `codex_pool.py`), no sub-packages. `__init__.py` re-exports nothing (just docstring). `codex_pool.py` computes only *which* token file to use (now including a quota-aware exclusion pass at selection time); `codex.py` owns the token itself.

## State

- `_cache` / `_cache_mtime` (`codex.py` L39-40): mtime-based in-memory cache to avoid re-parsing the token file on every call.
- `FileLock` on `.json.lock` (`codex.py` L38, L99): prevents concurrent refresh races across processes.
- Token file is written atomically via `tmp_path.replace()` (`codex.py` L141) with `0o600` perms (L138).
- **`codex_pool._QUOTA_CACHE`** (`codex_pool.py:82`, guarded by `_QUOTA_CACHE_LOCK`, `codex_pool.py:83`): process-local, in-memory `{resolved_auth_path: (monotonic_timestamp, quota_dict)}` cache with a `_QUOTA_EXCLUSION_CACHE_TTL_SECONDS = 30.0` (`codex_pool.py:80`) bound — never persisted, holds only the already-safe quota dict (never token/auth content). Thread-safe since selection can run concurrently (e.g. isolated failover adapters).

## Notes

- Refresh uses `filelock` timeout of 30s (L99) — if another process holds the lock, waits then re-reads (L102-104).
- `CLIENT_ID` is hardcoded (L18) — the public Codex OAuth app ID.
- 4 commits in history; most recent adds `CodexAuthError` for graceful failure.
