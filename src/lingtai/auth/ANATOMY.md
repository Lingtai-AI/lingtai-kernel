---
related_files:
  - src/lingtai/ANATOMY.md
  - src/lingtai/auth/__init__.py
  - src/lingtai/auth/codex.py
  - src/lingtai/auth/codex_pool.py
  - src/lingtai/auth/codex_account_source.py
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

Codex OAuth token management — reads TUI-written tokens, checks expiry, auto-refreshes via OpenAI OAuth endpoint. Also owns the thin `AccountSource` seam (spec v3, thin-wrapper refactor) that supplies candidate credentials to the `codex`/`codex-pool` providers: the source owns ONLY candidate selection; Codex core (`codex.py` + `src/lingtai/llm/_register.py`) owns token refresh, quota, transport, and failure/AED.

> **Maintenance:** see the `lingtai-kernel-anatomy` skill. **Coding agents** update this file in the same commit as code changes. **LingTai agents** report drift as issues.

## Components

| File | LOC | Role |
|---|---|---|
| `__init__.py` | 1 | Docstring-only package marker |
| `codex.py` | 272 | `CodexTokenManager` — reads/refreshes OAuth tokens; also owns the STRUCTURAL `usage_limit_reached` error classifier |
| `codex_pool.py` | 129 | Non-secret pool-FILE parsing/resolution only — no selection, no classifier |
| `codex_account_source.py` | 337 | `AccountSource` protocol + `AccountCandidate` + `FixedAccountSource` + `WeightedAccountSource` — candidate selection, exclusion, static/dynamic weight arithmetic |

**Key classes** (`codex.py`):
- `CodexTokenManager` (L62) — main API: `is_authenticated()` (L78), `get_access_token()` (L86), `get_account_id()` (L100). Reads a Codex OAuth token file, auto-refreshes when within 5 min of expiry (`REFRESH_BUFFER_SECONDS`, L21). The path defaults to `~/.lingtai-tui/codex-auth.json` (or `LINGTAI_TUI_DIR`), but a non-empty `token_path` constructor arg selects a different file — this is how a Codex preset/manifest's `llm.codex_auth_path` points one agent at its own token file (true multiple Codex accounts). The factory (`_register.py:_codex`) forwards `codex_auth_path` as `token_path` when set and non-blank.
  - `get_account_id()` returns the user's OWN ChatGPT account id (non-secret) for the `ChatGPT-Account-ID` header, or `None`. Source priority: an explicit `account_id` / `chatgpt_account_id` field in `codex-auth.json`, else the namespaced `https://api.openai.com/auth.chatgpt_account_id` claim decoded locally from the `id_token` JWT (`_decode_jwt_payload`, L31 — base64url-only, NO signature verification, non-raising). Never invents a value; missing/malformed → `None`.
- `CodexAuthError` (L54) — raised on 401/403 from refresh endpoint, user-facing message points to `/login`.
- `_is_usage_limit_reached_error()` (L264) — returns `True` iff a provider error is STRUCTURALLY a `429` (`_structured_status_code`, L232 — numeric status from `status_code`/`status`/`response.status_code`, never `str(exc)`) whose machine code is exactly `usage_limit_reached` (`_USAGE_LIMIT_CODE`, L229) in a repo-trusted structured location (`_structured_error_codes`, L248 — `exc.code`/`body.error.code`/`body.error.type`/top-level `body.code`). Codex core (`_register.py`) consults this once per attempt to decide whether a failed candidate's identity should be excluded before the AED-driven retry.

**Key functions** (`codex_pool.py`) — pure, non-secret, no selection or classification:
- `resolve_codex_pool_path()` (L36) / `resolve_codex_tui_dir()` (L30) — locate the pool file (`codex-auth-pool.json`) and TUI base dir.
- `load_codex_auth_pool()` (L65) → `_load_pool_entries()` (L77) — parse + validate the pool file into `[{"path", "weight"}, ...]`; a top-level `models` dict makes it a model-classified (v2) pool (exact case-sensitive category match only, `accounts` beside it ignored); missing/malformed → `[]`.
- `_coerce_weight()` (L111) — strictly-positive int/whole-float weight, else `None` (drops the entry).
- `_safe_source_ref()` (L124) — non-secret breadcrumb: verbatim if relative, `ABSOLUTE_REF_REDACTED` if absolute.
- `legacy_codex_token_path()` (L50) — the single-token fallback (`<tui_dir>/codex-auth.json`) used when the pool is empty/unusable.

**Key classes/functions** (`codex_account_source.py`) — candidate selection only, no quota/network/retry/chat/transport/ledger:
- `AccountCandidate` (L27) — frozen dataclass: `auth_ref`, `source_ref`, `source_index`, `weight`, `auth_path_sha8` (derived `__post_init__`, L45 — first 8 hex chars of SHA-256 of `auth_ref`, the stable non-secret exclude/quota-snapshot identity).
- `AccountSource` (L67, `Protocol`) — `select(exclude, quota_left_snapshot=None) -> AccountCandidate` and `quota_targets(exclude=None) -> [(auth_ref, sha8), ...]`.
- `FixedAccountSource` (L94) — always returns the same single account (used by the ordinary `codex` provider); `exclude` containing its identity raises `NoCandidateError` (L58).
- `WeightedAccountSource` (L140) — pool-file weighted sampling. `_snapshot()` (L182) re-reads the pool file fresh on EVERY call (`pool_size` L212, `quota_targets` L216, `select` L229 each call it independently — no caching at construction, so a live pool-file edit is observed on the very next call). `select()` (L229) excludes by `sha8` identity AND legacy resolved-path string, keeps the true positional index (`enumerate`) so duplicate/aliased entries anchor to the ACTUAL occurrence drawn rather than a re-derived first-match lookup, then does an unbiased cryptographic draw (`_uniform_float`, L332, via `secrets.randbits`). `_compute_raw_weights()` (L281): static (`configured_weight`) when `quota_left_snapshot is None`; dynamic (`configured_weight * quota_left_fraction`) otherwise, with a full-snapshot-completeness check — ANY eligible account's missing/non-comparable entry falls the WHOLE draw back to static (`_is_comparable_fraction`, L322).

## Connections

- **No intra-wrapper imports for `codex.py`.** Self-contained — only stdlib, `httpx`, `filelock`.
- **`codex_account_source.py` imports `codex_pool.py`** (lazily, inside methods) for `load_codex_auth_pool`/`_resolve_relative_to_tui` — parsing stays in `codex_pool.py`, selection in `codex_account_source.py`.
- **Reads disk token file** written by the TUI (`LINGTAI_TUI_DIR` env or `~/.lingtai-tui/`, `codex.py` L35-36).
- **Calls** `https://auth.openai.com/oauth/token` (`codex.py` L17) for token refresh.
- **Referenced by**: the Codex LLM adapter registry (`src/lingtai/llm/_register.py`) — `_codex` uses `CodexTokenManager` for the `codex` provider; `_codex_pool` constructs a `WeightedAccountSource` and, on every `create_chat`, re-queries quota and re-selects a candidate (excluding any identity a PRIOR attempt on the same cached adapter proved exhausted via `_is_usage_limit_reached_error`). The pool itself never retries in-process — a real provider failure is recorded once and re-raised so it enters the existing Codex/AED retry owner (`src/lingtai/kernel/base_agent/turn.py`'s AED loop), which rebuilds the session and calls `create_chat` again, naturally re-excluding the failed identity. See `src/lingtai/llm/ANATOMY.md`.

## Composition

Flat — three sibling modules (`codex.py`, `codex_pool.py`, `codex_account_source.py`), no sub-packages. `__init__.py` re-exports nothing (just docstring). `codex_pool.py` parses the pool FILE only; `codex_account_source.py` turns a parsed pool into a selected candidate; `codex.py` owns the token itself plus the structural failure classifier Codex core needs to decide exclusion.

## State

- `_cache` / `_cache_mtime` (`codex.py` L71-72, set in `CodexTokenManager.__init__` L65): mtime-based in-memory cache to avoid re-parsing the token file on every call; invalidated on write (L219-220).
- `FileLock` on `.json.lock` (`codex.py` L70 constructs the path, L174 acquires with a 30s timeout): prevents concurrent refresh races across processes.
- Token file is written atomically via `tmp_path.replace()` (L216) with `0o600` perms (L213).
- `codex_account_source.py` holds NO persistent state of its own: `WeightedAccountSource` stores only `pool_path`/`tui_dir`/`model` (immutable config), never a cached account list — every `select`/`quota_targets`/`pool_size` call re-derives its snapshot from disk.

## Notes

- `CLIENT_ID` is hardcoded (`codex.py` L20) — the public Codex OAuth app ID.
- The old sticky per-agent-session selection, request-scoped in-process failover switch sequence, and `CodexPoolAllAccountsExhaustedError` (formerly in `codex_pool.py`) were removed in the thin-wrapper v3 refactor — selection is no longer sticky, and failover is no longer pool-owned. See `_register.py`'s `_codex_pool` factory for the current per-attempt Codex/AED-driven shape.
