"""Codex auth POOL selection (provider ``codex-pool``).

This module load-balances a Codex agent across several existing Codex OAuth
token files with a *sticky-per-agent-session* choice. It never touches provider
``codex``: it only computes which ``codex-auth.json``-shaped token file the new
``codex-pool`` provider should read, and the caller injects that as the ordinary
``codex_auth_path`` so the existing Codex adapter / ``CodexTokenManager`` handle
the token itself.

Design constraints (Jason's spec):

  * The pool file is NON-SECRET — it lists token *paths* and weights only. Token
    contents are never read or logged here; we only stat/parse the pool file.
  * Selection is weighted (cumulative weights, no giant list expansion) and
    *sticky* within one agent wake/session: the seed is derived from the stable
    ``codex_session_anchor`` plus the agent's ``.agent.json`` ``started_at`` and
    deliberately EXCLUDES ``molt_count`` — an auth account must not rotate on a
    molt (that is what the endpoint pool does, not this).
  * Missing / empty / invalid pool -> return ``None`` so the caller falls back to
    the legacy default Codex token path for the ``codex-pool`` provider.
  * A pool file may be classified by EXACT model (v2): a top-level ``models``
    dict maps an exact, case-sensitive model string to an account list of the
    same entry shape as the flat v1 ``accounts`` list. When ``models`` is
    present it is the sole source of truth (a flat ``accounts`` list in the
    same file is ignored) and selection happens only inside the configured
    model's category. There is no prefix, family, wildcard, or default
    matching: a model with no exact category behaves like an unusable pool
    (legacy fallback). Flat v1 files keep byte-identical behavior for every
    model. The kernel keys off structure, never off the ``version`` field.
  * **Quota-aware exclusion at NEW selection only:** :func:`select_codex_pool_auth`
    excludes an account from THIS pick only when a fresh, available quota
    snapshot explicitly PROVES ``remaining_percent <= 0`` on its main
    rate-limit window (see :func:`_is_proven_exhausted`). Unavailable/error/
    malformed/missing quota data always FAILS OPEN (never excludes). Weights
    are preserved exactly among the remaining eligible accounts. This never
    migrates an already-created adapter/session (the function is only called
    at construction time) and never mutates any pool/config/auth file,
    weight, or the deterministic failover sequence. If every validated
    account is proven exhausted, :class:`CodexPoolAllAccountsExhaustedError`
    is raised instead of silently falling back or picking a proven-unusable
    account.

Public helpers:

  * :func:`resolve_codex_tui_dir`      -> the ``~/.lingtai-tui`` base dir.
  * :func:`resolve_codex_pool_path`    -> the pool file path (override-aware).
  * :func:`load_codex_auth_pool`       -> validated list of enabled accounts.
  * :func:`select_codex_pool_auth`     -> chosen token path + non-secret
                                          selection metadata, or ``None``; may
                                          raise ``CodexPoolAllAccountsExhaustedError``.
  * :func:`select_codex_pool_auth_path`-> chosen token path only, or ``None``.
  * :func:`list_codex_pool_quota_snapshots` -> per-account Codex quota/rate-limit
                                          snapshots (read-only reporting; never
                                          touches selection/routing).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

# Manifest / provider-defaults override for the pool file location. Parallels the
# existing ``codex_auth_path`` naming used for a single token file.
POOL_PATH_KEY = "codex_auth_pool_path"

# Default pool file name inside the TUI dir.
_DEFAULT_POOL_FILENAME = "codex-auth-pool.json"

# The exact structured provider error code that triggers a request-scoped Codex
# account switch. Recognized STRUCTURALLY only (never from the message string).
_USAGE_LIMIT_CODE = "usage_limit_reached"

# Maximum number of account SWITCHES within a single request/turn. This is a
# SWITCH/RETRY budget, not a distinct-account budget: the candidate sequence is
# walked verbatim (no realpath/alias dedup, revisits and wraps permitted), so a
# repeated path, an alias, or the originally-selected account reached again on
# wrap each consume one switch. The initial attempt is not a switch, so up to
# ``MAX_FAILOVER_SWITCHES + 1`` attempts run: the primary, then up to 10 switched
# alternates. The 10th switched attempt runs; only ITS qualifying failure exhausts.
MAX_FAILOVER_SWITCHES = 10

# Legacy single-token default, used as the fallback when the pool is unusable.
_LEGACY_TOKEN_FILENAME = "codex-auth.json"

# Bounded freshness window for the quota-aware exclusion check at NEW
# ``codex-pool`` selection. A cached snapshot older than this is
# treated as stale and re-fetched; this bounds how long a since-reset account
# can stay wrongly excluded, and how long a since-exhausted account can stay
# wrongly eligible, without adding per-selection subprocess latency on every
# call. Selection itself remains fast: an already-cached, still-fresh entry
# costs no subprocess spawn at all.
_QUOTA_EXCLUSION_CACHE_TTL_SECONDS = 30.0

# Process-local cache: resolved auth path -> (monotonic timestamp, quota dict
# from ``codex_quota.quota_snapshot_to_dict``). Guarded by ``_QUOTA_CACHE_LOCK``
# since selection can run from multiple threads (e.g. request-scoped failover
# building isolated adapters concurrently). Never persisted to disk; holds no
# token/auth content — only the already-safe quota snapshot dict.
_QUOTA_CACHE: dict[str, tuple[float, dict]] = {}
_QUOTA_CACHE_LOCK = threading.Lock()


class CodexPoolAllAccountsExhaustedError(Exception):
    """Raised by :func:`select_codex_pool_auth` when EVERY validated account in
    the configured (category-scoped) pool is explicitly PROVEN exhausted by a
    fresh, available quota snapshot (``remaining_percent <= 0`` on its main
    rate-limit window).

    This is intentionally distinct from the ordinary "pool unusable" case
    (missing/empty pool, no exact model category), which still returns
    ``None`` so the caller falls back to the legacy default token — falling
    back here would silently hand the caller a token that is, by the same
    proof, ALSO exhausted (the legacy default itself is not distinguished
    from a pool member) or would mask a real "everything is out of quota"
    operator-actionable condition behind a quiet fallback. The message names
    only the account count and, for a classified pool, the public model name —
    never a pool mapping, auth-path-derived identifier, token, or account id.
    """


def _quota_remaining_percent_from_dict(quota: dict) -> float | None:
    """Extract the ``primary`` bucket's ``primary`` window ``remaining_percent``.

    This is the "applicable main quota snapshot" the exclusion rule reads —
    the protocol's backward-compatible single-bucket view (``rateLimits`` ->
    ``primary`` window), not a specific ``rateLimitsByLimitId`` entry (there
    may be several, and the task scope is the account's overall/main quota).
    Returns ``None`` when the snapshot is unavailable, malformed, or the
    window itself is absent — callers MUST treat ``None`` as "not proven
    exhausted" (fail open), never as exhausted.
    """
    if not isinstance(quota, dict) or not quota.get("available"):
        return None
    primary_bucket = quota.get("primary")
    if not isinstance(primary_bucket, dict):
        return None
    primary_window = primary_bucket.get("primary")
    if not isinstance(primary_window, dict):
        return None
    value = primary_window.get("remaining_percent")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _cached_quota_dict(auth_path: str, *, timeout_seconds: float | None) -> dict:
    """Return a fresh-enough cached quota dict for ``auth_path``, fetching on
    a cache miss / stale entry. Thread-safe; never raises (the underlying
    ``read_codex_quota_snapshot`` is itself fail-soft and never raises)."""
    now = time.monotonic()
    with _QUOTA_CACHE_LOCK:
        cached = _QUOTA_CACHE.get(auth_path)
        if cached is not None and (now - cached[0]) < _QUOTA_EXCLUSION_CACHE_TTL_SECONDS:
            return cached[1]

    from lingtai.llm.openai.codex_quota import (
        quota_snapshot_to_dict,
        read_codex_quota_snapshot,
    )

    kwargs = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    snapshot = read_codex_quota_snapshot(auth_path, **kwargs)
    quota = quota_snapshot_to_dict(snapshot)
    with _QUOTA_CACHE_LOCK:
        _QUOTA_CACHE[auth_path] = (time.monotonic(), quota)
    return quota


def _is_proven_exhausted(auth_path: str, *, timeout_seconds: float | None) -> bool:
    """``True`` only if a fresh, available quota snapshot proves ``remaining_percent
    <= 0`` for ``auth_path``'s main rate-limit window.

    Fail-open by construction: unavailable / error / malformed / missing quota
    (``_quota_remaining_percent_from_dict`` returning ``None``) returns
    ``False`` here — the account stays eligible. Only an explicit, fresh,
    non-negative proof of exhaustion excludes it. A snapshot proving a
    POSITIVE ``remaining_percent`` after having previously been exhausted
    naturally restores eligibility on its own — there is no separate
    unexclusion path to maintain, since exclusion is recomputed fresh (subject
    to the bounded cache TTL) on every call rather than latched.
    """
    quota = _cached_quota_dict(auth_path, timeout_seconds=timeout_seconds)
    remaining = _quota_remaining_percent_from_dict(quota)
    if remaining is None:
        return False
    return remaining <= 0


def resolve_codex_tui_dir() -> Path:
    """Return the LingTai TUI base directory (``$LINGTAI_TUI_DIR`` or default).

    Relative account paths in the pool file resolve against this directory.
    """
    tui_dir = os.environ.get("LINGTAI_TUI_DIR", "~/.lingtai-tui")
    return Path(tui_dir).expanduser()


def resolve_codex_pool_path(defaults: dict | None = None) -> Path:
    """Resolve the pool file path.

    Priority:
      1. An explicit, non-blank ``codex_auth_pool_path`` in provider defaults —
         ``~``/absolute honored; a relative path resolves against the TUI dir.
      2. ``$LINGTAI_TUI_DIR/codex-auth-pool.json`` (or ``~/.lingtai-tui/...``).

    The path is non-secret; nothing here reads its contents.
    """
    tui_dir = resolve_codex_tui_dir()
    override = (defaults or {}).get(POOL_PATH_KEY)
    if isinstance(override, str) and override.strip():
        return _resolve_relative_to_tui(override.strip(), tui_dir)
    return tui_dir / _DEFAULT_POOL_FILENAME


def legacy_codex_token_path() -> Path:
    """The legacy single-token default (``<tui_dir>/codex-auth.json``).

    Mirrors ``CodexTokenManager``'s own default so the ``codex-pool`` fallback
    lands on exactly the same file the manager would have used with no path.
    """
    return resolve_codex_tui_dir() / _LEGACY_TOKEN_FILENAME


def _resolve_relative_to_tui(raw: str, tui_dir: Path) -> Path:
    """Resolve a pool ``path``: ``~``/absolute honored, else relative to TUI dir."""
    if raw.startswith("~"):
        return Path(raw).expanduser()
    p = Path(raw)
    if p.is_absolute():
        return p
    return tui_dir / p


def load_codex_auth_pool(pool_path: Path, model: str | None = None) -> list[dict]:
    """Parse the pool file into a list of validated, enabled accounts.

    Each returned entry is ``{"path": <str>, "weight": <positive int>}``. An
    account is dropped when:
      * ``enabled`` is explicitly ``False``;
      * ``path`` is missing / not a non-blank string;
      * ``weight`` is present but non-numeric or not strictly positive. A
        missing ``weight`` defaults to ``1`` (a hand-edited pool file may omit
        it; the TUI always writes an explicit weight).

    A model-classified (v2) file has a top-level ``models`` dict instead of the
    flat ``accounts`` list; the eligible entries are then ``models[model]``
    under exact, case-sensitive string equality — no prefix/family/wildcard
    matching, and ``models`` is the sole source of truth (any ``accounts`` list
    beside it is ignored). No exact category (including ``model=None``) yields
    ``[]`` so the caller falls back to the legacy default token path.

    A missing file, unreadable file, malformed JSON, or a non-dict / non-list
    structure yields ``[]`` (no exception) so the caller falls back cleanly.
    Token contents are never read here — only the pool file's own JSON.
    """
    return _load_pool_entries(pool_path, model)[0]


def _load_pool_entries(pool_path: Path, model: str | None) -> tuple[list[dict], bool]:
    """Parse + validate the pool file; return ``(accounts, classified)``.

    ``classified`` is ``True`` when the file carries a ``models`` dict (v2) —
    the accounts then come from the exact-``model`` category only. Shared
    parser behind :func:`load_codex_auth_pool` and the selection helpers (one
    read, one validation, plus the classified/flat fact selection needs for
    its ``model_scope`` metadata).
    """
    try:
        raw = json.loads(pool_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], False
    if not isinstance(raw, dict):
        return [], False
    models = raw.get("models")
    if isinstance(models, dict):
        entries = models.get(model)
        classified = True
    else:
        entries = raw.get("accounts")
        classified = False
    if not isinstance(entries, list):
        return [], classified

    valid: list[dict] = []
    for acct in entries:
        if not isinstance(acct, dict):
            continue
        if acct.get("enabled", True) is False:
            continue
        path = acct.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        weight = _coerce_weight(acct.get("weight", 1))
        if weight is None:
            continue
        valid.append({"path": path.strip(), "weight": weight})
    return valid, classified


def _coerce_weight(raw) -> int | None:
    """Return a strictly-positive int weight, or ``None`` for invalid input.

    Bools are rejected (``True``/``False`` are not meaningful weights). Floats
    that are whole positive numbers are accepted (e.g. ``2.0`` -> ``2``).
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, float):
        if raw > 0 and raw.is_integer():
            return int(raw)
        return None
    return None


def _selection_seed(defaults: dict | None, tui_dir: Path) -> str:
    """Build the stable per-agent-session selection seed.

    Combines the ``codex_session_anchor`` with the agent's ``started_at`` (read
    from ``<anchor-dir>/.agent.json``) so the choice is stable across requests
    and molts within one wake and changes on a new agent session. Deliberately
    EXCLUDES ``molt_count``. When ``started_at`` is unavailable, falls back to
    ``agent_id`` -> ``address`` -> the anchor path itself.
    """
    d = defaults or {}
    anchor = d.get("codex_session_anchor")
    anchor_str = anchor if isinstance(anchor, str) and anchor else str(tui_dir)

    session_marker = _read_started_at(anchor_str)
    if session_marker is None:
        for key in ("agent_id", "address"):
            val = d.get(key)
            if isinstance(val, str) and val:
                session_marker = val
                break
    if session_marker is None:
        session_marker = anchor_str

    return f"{anchor_str}\0{session_marker}"


def _read_started_at(anchor_str: str) -> str | None:
    """Read ``started_at`` from ``<anchor-dir>/.agent.json``; ``None`` on failure.

    The ``.agent.json`` sits next to the anchor (``init.json``). A missing /
    malformed file or absent ``started_at`` yields ``None`` — no exception, no
    token content touched.
    """
    try:
        agent_json = Path(anchor_str).parent / ".agent.json"
        data = json.loads(agent_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    val = data.get("started_at")
    if val is None:
        return None
    return str(val)


def _weighted_pick(accounts: list[dict], seed: str) -> tuple[int, dict]:
    """Deterministically pick one account by cumulative weight from ``seed``.

    Hashes ``seed`` into a stable integer and maps it into ``[0, total_weight)``
    via cumulative weights — no per-unit list expansion, so a huge weight stays
    O(len(accounts)). The same seed + same accounts always picks the same one.
    Returns ``(index, account)`` — the index within the validated account list.
    """
    return _weighted_pick_indexed(list(enumerate(accounts)), seed)


def _weighted_pick_indexed(
    indexed_accounts: list[tuple[int, dict]], seed: str
) -> tuple[int, dict]:
    """Like :func:`_weighted_pick`, but over an explicit ``(original_index,
    account)`` pair list so a caller can filter accounts (e.g. quota-excluded
    ones) while still returning the ORIGINAL index into the full validated
    account list — required so ``source_index`` stays a stable anchor for
    :func:`_codex_pool_failover_candidates` regardless of which accounts were
    filtered out of this particular pick. Configured weights are preserved
    exactly among whatever accounts are passed in; only the SET eligible for
    the pick changes. Same seed + same filtered set always picks the same one.
    """
    total = sum(acct["weight"] for _, acct in indexed_accounts)
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    point = int.from_bytes(digest[:8], "big") % total
    cumulative = 0
    for original_idx, acct in indexed_accounts:
        cumulative += acct["weight"]
        if point < cumulative:
            return original_idx, acct
    # Unreachable (point < total == final cumulative) — defensive fallback.
    return indexed_accounts[-1]


def select_codex_pool_auth(
    defaults: dict | None = None,
    model: str | None = None,
    *,
    quota_timeout_seconds: float | None = None,
) -> dict | None:
    """Select the pool account and describe the choice for runtime logging.

    Returns ``{"auth_path": <resolved token path str>, "selection": <dict>}``,
    or ``None`` when the pool file is missing / has no valid enabled accounts —
    in which case the caller falls back to the legacy default Codex token path.
    For a model-classified (v2) pool, ``model`` — the exact configured model
    string — picks the category; only that category's accounts are eligible,
    and no exact category means ``None`` (same fallback as an unusable pool).
    The model is NOT mixed into the selection seed: a flat v1 pool picks the
    same account regardless of ``model`` (zero churn), and for v2 the category
    list itself already differentiates the outcome.

    **Quota-aware exclusion at NEW selection.** This function is only ever
    called at a NEW selection moment (adapter construction in
    ``_register.py``'s ``_codex_pool`` factory, or an explicit ``vision``
    manual pool lookup) — never on an already-created adapter/session, so this
    exclusion can never migrate a live sticky session. Before picking, each
    validated (category-scoped) account is checked via
    :func:`_is_proven_exhausted` (bounded-TTL cached
    ``account/rateLimits/read`` probe): an account is excluded from THIS pick
    only when a fresh, available snapshot explicitly proves
    ``remaining_percent <= 0`` on its main rate-limit window. Every other case
    — unavailable, error, malformed, missing quota, or a snapshot that simply
    couldn't be read — FAILS OPEN (the account stays eligible); this function
    never guesses exhaustion from absence of proof. Configured weights are
    preserved EXACTLY among the remaining eligible accounts (the cumulative
    weighted pick runs over the filtered set with each account's own
    configured weight, unchanged). If a since-exhausted account's quota later
    shows a positive ``remaining_percent`` (or a cache entry ages out and a
    fresh read proves it recovered), it is automatically eligible again on the
    next call — there is no separate "cooldown" state to expire. If ALL
    validated accounts in scope are explicitly proven exhausted, raises
    :class:`CodexPoolAllAccountsExhaustedError` instead of silently falling
    back to the legacy default (which would hand back a token equally likely
    to be exhausted) or arbitrarily picking an account already proven unusable.

    This exclusion pass affects ONLY which account gets selected right now; it
    never disables an account, writes a cooldown, reorders
    :func:`_codex_pool_failover_candidates`'s deterministic sequence, or
    touches the pool/config/auth files on disk.

    ``selection`` is the NON-SECRET attribution breadcrumb an operator needs to
    answer "which codex-pool source handled this call" from the event log:

      * ``source_ref``     — the account ``path`` exactly as configured in the
                             pool file (relative refs stay relative);
      * ``source_index``   — index within the validated enabled-account list
                             (category-relative for a classified pool);
      * ``pool_size``      — number of validated enabled accounts (category-
                             relative for a classified pool);
      * ``weight``         — the chosen account's weight;
      * ``auth_path_sha8`` — first 8 hex chars of SHA-256 of the resolved token
                             path, a stable id that avoids logging the absolute
                             path itself;
      * ``model_scope``    — the exact category key used (classified pool), or
                             ``None`` (flat v1 pool). Model names are already
                             non-secret manifest values.

    Quota probes read only process-owned temporary copies of the configured
    token files; no token content, Authorization material, or raw auth-file data
    appears in the returned metadata or logs.
    """
    tui_dir = resolve_codex_tui_dir()
    pool_path = resolve_codex_pool_path(defaults)
    accounts, classified = _load_pool_entries(pool_path, model)
    if not accounts:
        return None

    resolved_paths = [
        str(_resolve_relative_to_tui(a["path"], tui_dir)) for a in accounts
    ]
    eligible = [
        (i, acct)
        for i, acct in enumerate(accounts)
        if not _is_proven_exhausted(
            resolved_paths[i], timeout_seconds=quota_timeout_seconds
        )
    ]
    if not eligible:
        raise CodexPoolAllAccountsExhaustedError(
            f"All {len(accounts)} configured Codex-pool account(s) "
            f"{'for model ' + repr(model) + ' ' if classified else ''}"
            "are exhausted (remaining_percent <= 0 on a fresh quota read). "
            "Add another account, wait for a rate-limit reset, or configure "
            "additional pool capacity before retrying."
        )

    seed = _selection_seed(defaults, tui_dir)
    if len(eligible) == len(accounts):
        # No account was excluded: use the plain (non-indexed) picker so this
        # stays the exact same call/seam as before quota-aware exclusion
        # existed — byte-identical selection behavior AND the same monkeypatch
        # point existing tests/tools bind to (`_weighted_pick`).
        idx, chosen = _weighted_pick(accounts, seed)
    else:
        idx, chosen = _weighted_pick_indexed(eligible, seed)
    auth_path = resolved_paths[idx]
    return {
        "auth_path": auth_path,
        "selection": {
            # Relative refs stay verbatim; an ABSOLUTE ref would leak a real
            # token-file location, so it is redacted here too (consistent with the
            # failover alternates). Stable identity travels in ``auth_path_sha8``.
            "source_ref": _safe_source_ref(chosen["path"]),
            "source_index": idx,
            "pool_size": len(accounts),
            "weight": chosen["weight"],
            "auth_path_sha8": hashlib.sha256(auth_path.encode("utf-8")).hexdigest()[:8],
            "model_scope": model if classified else None,
        },
    }


def select_codex_pool_auth_path(
    defaults: dict | None = None,
    model: str | None = None,
    *,
    quota_timeout_seconds: float | None = None,
) -> str | None:
    """Select the Codex token path for the ``codex-pool`` provider.

    Returns the resolved token file path (a filesystem string) chosen stickily
    for this agent session by weighted selection, or ``None`` when the pool file
    is missing / has no valid enabled accounts (for a model-classified pool:
    no valid accounts in the exact-``model`` category) — in which case the
    caller falls back to the legacy default Codex token path. May raise
    :class:`CodexPoolAllAccountsExhaustedError` — see
    :func:`select_codex_pool_auth`, which this delegates to unchanged.

    Path-only view of :func:`select_codex_pool_auth` (one selection, two views).
    No token file is read here beyond what the quota-aware exclusion check
    itself performs (a read-only rate-limit probe); nothing secret is logged.
    """
    selected = select_codex_pool_auth(
        defaults, model, quota_timeout_seconds=quota_timeout_seconds
    )
    return selected["auth_path"] if selected else None


def list_codex_pool_quota_snapshots(
    defaults: dict | None = None,
    model: str | None = None,
    *,
    timeout_seconds: float | None = None,
) -> list[dict]:
    """Return a safe, per-account Codex quota snapshot for every pool entry.

    Reuses :func:`_load_pool_entries` — the SAME validated, model-category
    filtered, duplicate/alias-preserving account order that
    :func:`select_codex_pool_auth` uses — so this never guesses a mapping
    the selection logic doesn't already use, and never alters selection,
    weights, sticky-session state, failover order, or any pool/config/auth
    file. Each entry's Codex quota is read via a throwaway ``codex
    app-server`` stdio probe (``lingtai.llm.openai.codex_quota``); a missing,
    unqueryable, or unsupported account surfaces as
    ``available=False`` rather than being fabricated or silently dropped.

    An empty/unusable pool (or no exact model category) returns ``[]`` — same
    fallback condition as :func:`select_codex_pool_auth` returning ``None``.

    Each returned entry::

        {
          "source_index": int,        # position in the validated account list
          "source_ref": str,          # relative ref, or ABSOLUTE_REF_REDACTED
          "auth_path_sha8": str,      # sha256(resolved path)[:8], never the raw path
          "weight": int,
          "model_scope": str | None,  # exact v2 category key, or None (flat v1)
          "quota": <CodexQuotaSnapshot dict from codex_quota.quota_snapshot_to_dict>,
        }

    Never mutates or reads any deterministic-selection state; this is a
    read-only reporting view over the same pool file :func:`load_codex_auth_pool`
    parses. No token content, auth-file content, or raw account id is ever
    read or returned — only the resolved token PATH is used, to spawn the
    read-only quota probe.
    """
    from lingtai.llm.openai.codex_quota import (
        quota_snapshot_to_dict,
        read_codex_quota_snapshot,
    )

    tui_dir = resolve_codex_tui_dir()
    pool_path = resolve_codex_pool_path(defaults)
    accounts, classified = _load_pool_entries(pool_path, model)
    if not accounts:
        return []
    model_scope = model if classified else None

    kwargs = {} if timeout_seconds is None else {"timeout_seconds": timeout_seconds}
    out: list[dict] = []
    for idx, acct in enumerate(accounts):
        auth_path = str(_resolve_relative_to_tui(acct["path"], tui_dir))
        snapshot = read_codex_quota_snapshot(auth_path, **kwargs)
        out.append(
            {
                "source_index": idx,
                "source_ref": _safe_source_ref(acct["path"]),
                "auth_path_sha8": hashlib.sha256(auth_path.encode("utf-8")).hexdigest()[:8],
                "weight": acct["weight"],
                "model_scope": model_scope,
                "quota": quota_snapshot_to_dict(snapshot),
            }
        )
    return out


def _structured_status_code(exc: BaseException) -> int | None:
    """Best-effort STRUCTURAL HTTP status extraction, or ``None``.

    Reads only integer status fields the provider SDK sets on the exception
    object (``status_code`` / ``status``) or its ``response.status_code`` — never
    parses the message string, so ``429`` appearing in free-form text can never
    be mistaken for the real status. ``bool`` is rejected explicitly because it
    is an ``int`` subclass in Python but not a meaningful HTTP status.
    """
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    if response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _structured_error_codes(exc: BaseException) -> tuple[str, ...]:
    """Collect the machine error codes from the structured locations the repo
    already trusts — never from ``str(exc)`` / message substrings / URLs.

    Mirrors the established ``_task_card_api_code`` idiom: the SDK populates
    ``exc.code`` only from a TOP-LEVEL body key, while OpenAI-family bodies nest
    the real code under ``body["error"]``. So gather, with dict guards:
      * ``exc.code`` when it is a string;
      * ``body["error"]["code"]`` and ``body["error"]["type"]``;
      * top-level ``body["code"]``.
    Returns the string-valued candidates found (possibly empty). Never raises.
    """
    candidates: list[object] = []
    code_attr = getattr(exc, "code", None)
    if isinstance(code_attr, str):
        candidates.append(code_attr)
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            candidates.append(err.get("code"))
            candidates.append(err.get("type"))
        candidates.append(body.get("code"))
    return tuple(c for c in candidates if isinstance(c, str) and c)


def _is_usage_limit_reached_error(exc: BaseException) -> bool:
    """Return ``True`` iff ``exc`` is structurally a ``429`` whose structured
    error code is exactly ``usage_limit_reached``.

    Both facts are extracted STRUCTURALLY:
      * a numeric HTTP status of exactly ``429`` from an integer status field
        (or ``response.status_code``) — see :func:`_structured_status_code`;
      * the exact machine code ``usage_limit_reached`` in one of the structured
        locations the repo already trusts — see :func:`_structured_error_codes`.

    A string mention in the message alone is NOT sufficient, and the number 429
    appearing only in free-form text is NOT read as the status. Ordinary 429s
    with another/no code, non-429 statuses, network errors, timeouts, and
    arbitrary exceptions all return ``False``. Never raises.
    """
    if _structured_status_code(exc) != 429:
        return False
    return _USAGE_LIMIT_CODE in _structured_error_codes(exc)


# Sentinel emitted in place of an ABSOLUTE account ref so no absolute token-file
# path ever reaches selection/usage/event metadata. Stable identity is carried by
# ``auth_path_sha8`` instead. A relative ref is safe and kept verbatim.
ABSOLUTE_REF_REDACTED = "<absolute-path-redacted>"


def _safe_source_ref(raw_ref: str) -> str:
    """Return a non-secret ``source_ref``: the raw ref if relative, else redacted.

    A relative pool entry (``codex-auth/x.json``) is safe to surface verbatim. An
    ABSOLUTE entry (or a ``~`` entry that expands to an absolute path) would leak a
    real token-file location, so it is replaced by :data:`ABSOLUTE_REF_REDACTED`;
    the stable non-secret identity travels in ``auth_path_sha8`` instead.
    """
    expanded = os.path.expanduser(raw_ref) if raw_ref.startswith("~") else raw_ref
    if os.path.isabs(expanded):
        return ABSOLUTE_REF_REDACTED
    return raw_ref


def _codex_pool_failover_candidates(
    defaults: dict | None,
    model: str | None,
    selected_auth_path: str | None,
    selected_source_index: int | None = None,
) -> list[dict]:
    """Return the request-scoped SWITCH SEQUENCE to fail over through after the
    account the primary attempt used.

    Reads the SAME non-secret pool snapshot and the SAME validated account order
    as the initial selection (:func:`_load_pool_entries`, model-category-relative
    for a v2 pool). Ordering is ANCHORED to the ACTUAL selected occurrence so the
    sequence follows the configured order of siblings right after it:

      * ``selected_source_index`` — the AUTHORITATIVE anchor. It is the exact
        occurrence :func:`select_codex_pool_auth` chose via weighted selection
        (its ``selection["source_index"]``). When it is a valid in-range integer
        it is used verbatim, so a pool with duplicate/aliased entries resolving to
        one file anchors to the occurrence weighted selection actually picked (not
        merely the first path match) and the sequence begins with the siblings
        that configuredly follow THAT occurrence.
      * ``selected_auth_path`` — a DEFENSIVE FALLBACK used only when the index is
        absent/out-of-range (e.g. a direct/internal caller that did not thread the
        index): walk to the FIRST entry whose resolved path equals it (exact string
        match on the same relative-to-TUI resolution the selection used; NO
        ``realpath``, no alias dedup). Falls back to the pool head when the path is
        unknown/absent too.

    This is a SWITCH/RETRY budget, NOT a distinct-account budget. The sequence is
    walked VERBATIM with NO realpath/alias dedup and NO suppression by resolved
    auth identity: a ``usage_limit_reached`` can be transient/soft, so after the
    pool wraps the same underlying credential may work again. Repeated path
    entries, aliases resolving to the same file, and revisits/wraps back to the
    originally-selected account are therefore ALL emitted in order, each counting
    as one switch. Exactly :data:`MAX_FAILOVER_SWITCHES` (10) candidates are
    returned whenever the pool has at least one account (walking as many wraps as
    needed); an empty pool yields ``[]`` so the caller fails loud rather than
    looping. Weights govern only the initial pick; failover order is deterministic
    next-in-order.

    Each entry is a non-secret dict::

        {"auth_path": <resolved token path str>,   # for injection as codex_auth_path
         "source_ref": <relative ref, or ABSOLUTE_REF_REDACTED>,  # never an absolute path
         "source_index": <index within the validated account list>,
         "pool_size": <validated account count>,
         "weight": <the account's weight>,
         "auth_path_sha8": <sha256(resolved path)[:8]>,  # stable non-secret identity
         "model_scope": <exact v2 category key, or None on a flat v1 pool>}

    No token file is read; nothing secret is emitted.
    """
    tui_dir = resolve_codex_tui_dir()
    pool_path = resolve_codex_pool_path(defaults)
    accounts, classified = _load_pool_entries(pool_path, model)
    pool_size = len(accounts)
    if pool_size == 0:
        return []
    model_scope = model if classified else None

    resolved = [
        str(_resolve_relative_to_tui(a["path"], tui_dir)) for a in accounts
    ]

    # Anchor to the ACTUAL selected occurrence. The authoritative source is
    # ``selected_source_index`` — the exact index weighted selection chose — so a
    # pool with duplicate/aliased entries anchors to the occurrence that was
    # actually picked (not merely the first resolved-path match, which would rotate
    # the sequence and could omit the siblings configured right after the real
    # occurrence). Only when the index is absent/out-of-range do we fall back to
    # the resolved-path scan (exact string match, NO ``realpath``, no alias dedup),
    # and finally to the pool head. In the real flow the index is always threaded,
    # so the fallbacks are defensive for direct/internal callers.
    anchor = -1
    if isinstance(selected_source_index, int) and not isinstance(
        selected_source_index, bool
    ) and 0 <= selected_source_index < pool_size:
        anchor = selected_source_index
    elif selected_auth_path:
        for i, path in enumerate(resolved):
            if path == selected_auth_path:
                anchor = i
                break
    start = anchor if anchor >= 0 else 0

    # Walk the validated order verbatim from ``start + 1``, wrapping, emitting one
    # candidate per step (NO dedup: aliases, repeated entries, and — on wrap — the
    # originally-selected account are all attempted). Exactly MAX_FAILOVER_SWITCHES
    # entries are produced, walking as many full wraps as the budget requires.
    out: list[dict] = []
    for step in range(1, MAX_FAILOVER_SWITCHES + 1):
        idx = (start + step) % pool_size
        chosen = accounts[idx]
        auth_path = resolved[idx]
        out.append(
            {
                "auth_path": auth_path,
                "source_ref": _safe_source_ref(chosen["path"]),
                "source_index": idx,
                "pool_size": pool_size,
                "weight": chosen["weight"],
                "auth_path_sha8": hashlib.sha256(
                    auth_path.encode("utf-8")
                ).hexdigest()[:8],
                "model_scope": model_scope,
            }
        )
    return out
