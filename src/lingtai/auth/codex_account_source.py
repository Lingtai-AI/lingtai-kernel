"""AccountSource seam for the one native Codex adapter.

An explicit ``codex_auth_path`` supplies ``FixedAccountSource``; otherwise the
same adapter uses ``WeightedAccountSource`` for every accepted Codex provider
spelling.  Sources own only candidate selection.  The adapter owns OAuth token
refresh, quota, REST/WS transport, safe attribution, and failure classification;
the kernel owns AED rebuild/replay.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Candidate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccountCandidate:
    """A selected account identity ready for Codex to bind and use.

    * ``auth_ref``  — resolved token-file path (opaque to the source beyond its
                      own resolution; Codex reads/refreshes the token).
    * ``source_ref`` — non-sensitive pool breadcrumb (relative ref, or redacted).
    * ``source_index`` — index within the validated account list.
    * ``weight`` — the account's configured weight.
    * ``auth_path_sha8`` — first 8 hex chars of SHA-256 of ``auth_ref``,
      the stable non-secret identity used for exclusion and quota snapshots.
    """

    auth_ref: str
    source_ref: str
    source_index: int
    weight: int
    auth_path_sha8: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "auth_path_sha8",
            hashlib.sha256(self.auth_ref.encode("utf-8")).hexdigest()[:8],
        )


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


_ALLOWED_NO_CANDIDATE_REASONS = frozenset(
    {
        "all_zero_quota",
        "fixed_account_excluded",
        "no_eligible_after_exclude",
        "unknown",
        "zero_effective_weight",
    }
)
_ALLOWED_CODEX_ACCOUNT_SOURCES = frozenset({"fixed", "weighted"})
_ALLOWED_NO_CANDIDATE_COUNT_FIELDS = frozenset(
    {
        "codex_account_combined_excluded_count",
        "codex_account_eligible_count",
        "codex_account_exclude_key_count",
        "codex_account_excluded_count",
        "codex_account_existing_excluded_count",
        "codex_account_pool_size",
        "codex_account_quota_invalid_count",
        "codex_account_quota_observed_count",
        "codex_account_quota_read_error_count",
        "codex_account_quota_snapshot_count",
        "codex_account_quota_target_count",
        "codex_account_zero_effective_weight_count",
        "codex_account_zero_quota_count",
    }
)
_ALLOWED_NO_CANDIDATE_BOOL_FIELDS = frozenset(
    {
        "codex_account_legacy_fallback_allowed",
        "codex_account_quota_snapshot_complete",
        "codex_account_quota_snapshot_present",
    }
)
_ALLOWED_NO_CANDIDATE_STRING_ENUMS = {
    "codex_account_source": _ALLOWED_CODEX_ACCOUNT_SOURCES,
}


def _safe_no_candidate_reason(reason: str | None) -> str | None:
    """Return a fixed diagnostic reason enum, never caller-supplied text."""
    if not reason:
        return None
    text = str(reason)
    if text in _ALLOWED_NO_CANDIDATE_REASONS:
        return text
    return "unknown"


def _safe_no_candidate_diagnostics(
    diagnostics: dict[str, Any] | None,
) -> dict[str, object]:
    """Return the exact non-secret diagnostic subset for selection failure."""
    safe: dict[str, object] = {}
    if not diagnostics:
        return safe
    for key, value in diagnostics.items():
        if not isinstance(key, str):
            continue
        if key in _ALLOWED_NO_CANDIDATE_COUNT_FIELDS:
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                safe[key] = value
        elif key in _ALLOWED_NO_CANDIDATE_BOOL_FIELDS:
            if isinstance(value, bool):
                safe[key] = value
        elif key in _ALLOWED_NO_CANDIDATE_STRING_ENUMS:
            if isinstance(value, str) and value in _ALLOWED_NO_CANDIDATE_STRING_ENUMS[key]:
                safe[key] = value
    return safe


def _format_no_candidate_diagnostics(fields: dict[str, object]) -> str:
    """Compact, safe suffix for logs that only preserve ``str(exc)``."""
    short_names = {
        "no_candidate_reason": "reason",
        "codex_account_source": "source",
        "codex_account_pool_size": "pool",
        "codex_account_existing_excluded_count": "pre_excluded",
        "codex_account_excluded_count": "excluded",
        "codex_account_combined_excluded_count": "combined_excluded",
        "codex_account_zero_quota_count": "zero_quota",
        "codex_account_eligible_count": "eligible",
        "codex_account_quota_target_count": "quota_targets",
        "codex_account_quota_observed_count": "quota_observed",
        "codex_account_quota_read_error_count": "quota_read_errors",
        "codex_account_quota_invalid_count": "quota_invalid",
        "codex_account_quota_snapshot_complete": "quota_complete",
        "codex_account_quota_snapshot_present": "quota_present",
        "codex_account_quota_snapshot_count": "quota_snapshot",
        "codex_account_zero_effective_weight_count": "zero_weight",
        "codex_account_exclude_key_count": "exclude_keys",
        "codex_account_legacy_fallback_allowed": "legacy_fallback_allowed",
    }
    ordered = [key for key in short_names if key in fields]
    ordered.extend(sorted(key for key in fields if key not in short_names))
    parts: list[str] = []
    for key in ordered:
        value = fields[key]
        label = short_names.get(key, key)
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        else:
            rendered = str(value)
        parts.append(f"{label}={rendered}")
        if len(", ".join(parts)) >= 240:
            break
    return ", ".join(parts)


class NoCandidateError(Exception):
    """Every validated, non-excluded account is unavailable.

    The exception may carry non-secret diagnostic counts.  The base message
    remains unchanged when no diagnostics are supplied, preserving existing
    callers/tests that construct ``NoCandidateError("...")`` manually.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        self.base_message = str(message)
        self.reason = _safe_no_candidate_reason(reason)
        self.diagnostics = _safe_no_candidate_diagnostics(diagnostics)
        fields = self.diagnostic_fields()
        suffix = _format_no_candidate_diagnostics(fields)
        rendered = f"{self.base_message} ({suffix})" if suffix else self.base_message
        super().__init__(rendered)

    def diagnostic_fields(self) -> dict[str, object]:
        fields: dict[str, object] = {}
        if self.reason:
            fields["no_candidate_reason"] = self.reason
        fields.update(self.diagnostics)
        return fields

    def with_diagnostics(
        self,
        *,
        reason: str | None = None,
        diagnostics: dict[str, Any] | None = None,
        **extra: Any,
    ) -> "NoCandidateError":
        merged = dict(self.diagnostics)
        if diagnostics:
            merged.update(diagnostics)
        if extra:
            merged.update(extra)
        return NoCandidateError(
            self.base_message,
            reason=reason or self.reason,
            diagnostics=merged,
        )


# ---------------------------------------------------------------------------
# Source protocol
# ---------------------------------------------------------------------------


class AccountSource(Protocol):
    """Minimal candidate-supplier seam inside the Codex engine.

    ``select(exclude, quota_left_snapshot)`` returns one ``AccountCandidate``
    or raises ``NoCandidateError``.  The source owns no network, quota, retry,
    transport, or provider-error knowledge.
    """

    def select(
        self,
        exclude: set[str] | None = None,
        quota_left_snapshot: dict[str, float] | None = None,
    ) -> AccountCandidate: ...

    def quota_targets(
        self, exclude: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """Return ``[(auth_ref, auth_path_sha8), ...]`` for every non-excluded
        account so Codex can query per-account quota before calling ``select``."""
        ...


# ---------------------------------------------------------------------------
# FixedAccountSource
# ---------------------------------------------------------------------------


class FixedAccountSource:
    """Always returns the SAME single account on every ``select``.

    ``exclude`` containing the account's identity raises ``NoCandidateError``.
    """

    def __init__(self, auth_path: str, weight: int = 1) -> None:
        self._candidate = AccountCandidate(
            auth_ref=auth_path,
            source_ref=_safe_source_ref(auth_path),
            source_index=0,
            weight=weight,
        )

    def select(
        self,
        exclude: set[str] | None = None,
        quota_left_snapshot: dict[str, float] | None = None,
    ) -> AccountCandidate:
        if exclude and self._candidate.auth_path_sha8 in exclude:
            raise NoCandidateError(
                "Fixed account is excluded",
                reason="fixed_account_excluded",
                diagnostics={
                    "codex_account_source": "fixed",
                    "codex_account_pool_size": 1,
                    "codex_account_excluded_count": 1,
                    "codex_account_eligible_count": 0,
                    "codex_account_exclude_key_count": len(exclude),
                },
            )
        return self._candidate

    def quota_targets(
        self, exclude: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        if exclude and self._candidate.auth_path_sha8 in exclude:
            return []
        return [(self._candidate.auth_ref, self._candidate.auth_path_sha8)]


# ---------------------------------------------------------------------------
# WeightedAccountSource
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResolvedAccount:
    """One pool entry resolved against ``tui_dir``, for one fresh snapshot."""

    raw_path: str
    resolved_path: str
    sha8: str
    weight: int


class WeightedAccountSource:
    """Selects a Codex account from a pool file by weighted sampling.

    Owns ONLY pool parsing, exclude application, static/dynamic weight
    arithmetic, normalization, and safe candidate metadata.  Calls re-read
    the pool file via :meth:`snapshot` unless a caller supplies one immutable
    snapshot across a larger operation (for example, quota scan + selection).
    No accounts are cached at construction time, so a live pool-file edit is
    observed by the next unsnapshotted operation.

    Static mode (the default)::

        raw_weight_i = configured_weight_i

    Dynamic mode (linear function)::

        raw_weight_i = configured_weight_i * quota_left_fraction_i

    When ``quota_left_snapshot`` is provided it MUST contain a comparable
    fraction for **every** eligible account (after exclusion); if ANY
    eligible account's snapshot entry is missing or non-comparable,
    the WHOLE draw falls back to static.

    Dynamic mode is entered only when ``quota_left_snapshot is not None``
    (the caller explicitly supplied a snapshot).  An empty snapshot against a
    non-empty eligible set also falls back to static.

    All base weights set to 1 makes dynamic mode a pure quota-left proportion.
    """

    def __init__(
        self,
        pool_path: Path,
        tui_dir: Path,
        model: str | None = None,
    ) -> None:
        self._pool_path = pool_path
        self._tui_dir = tui_dir
        self._model = model

    # -- internal: one fresh, coherent read -----------------------------------

    def _snapshot(self) -> list[_ResolvedAccount]:
        """Re-read the pool file and resolve every entry — one fresh read."""
        from lingtai.auth.codex_pool import (
            _resolve_relative_to_tui,
            load_codex_auth_pool,
        )

        accounts = load_codex_auth_pool(self._pool_path, model=self._model)
        out: list[_ResolvedAccount] = []
        for acct in accounts:
            rp = str(_resolve_relative_to_tui(acct["path"], self._tui_dir))
            sha8 = hashlib.sha256(rp.encode("utf-8")).hexdigest()[:8]
            out.append(
                _ResolvedAccount(
                    raw_path=acct["path"],
                    resolved_path=rp,
                    sha8=sha8,
                    weight=acct["weight"],
                )
            )
        return out

    # -- public API ----------------------------------------------------------

    def snapshot(self) -> tuple[_ResolvedAccount, ...]:
        """Return one immutable, freshly read pool snapshot.

        Callers spanning multiple source operations may pass this same value
        back to :meth:`quota_targets` and :meth:`select` so one logical draw
        cannot mix two live pool-file states.
        """
        return tuple(self._snapshot())

    @property
    def pool_size(self) -> int:
        """Number of validated enabled accounts in a fresh pool snapshot."""
        return len(self.snapshot())

    def quota_targets(
        self,
        exclude: set[str] | None = None,
        snapshot: tuple[_ResolvedAccount, ...] | None = None,
    ) -> list[tuple[str, str]]:
        """Return ``[(auth_ref, auth_path_sha8), ...]`` for every non-excluded
        account.  Codex uses this to query per-account quota before calling
        ``select``, without the source needing to know anything about quota."""
        exclude = exclude or set()
        accounts = snapshot if snapshot is not None else self.snapshot()
        return [
            (a.resolved_path, a.sha8)
            for a in accounts
            if a.sha8 not in exclude and a.resolved_path not in exclude
        ]

    def select(
        self,
        exclude: set[str] | None = None,
        quota_left_snapshot: dict[str, float] | None = None,
        snapshot: tuple[_ResolvedAccount, ...] | None = None,
    ) -> AccountCandidate:
        """Pick one account by weighted sampling, respecting *exclude* and
        optionally scaling weights by *quota_left_snapshot* for dynamic draws.
        """
        exclude = exclude or set()
        accounts = snapshot if snapshot is not None else self.snapshot()

        # Exclude by sha8 identity AND by the old-style (legacy) auth-path
        # exclusion for backward compat. Keep the true positional index
        # alongside each eligible entry — duplicate/aliased accounts compare
        # equal by value, so anchoring to a re-derived ``.index()`` lookup
        # would always resolve to the FIRST matching path rather than the
        # actual occurrence drawn.
        excluded_accounts = [
            a for a in accounts
            if a.sha8 in exclude or a.resolved_path in exclude
        ]
        eligible = [
            (i, a) for i, a in enumerate(accounts)
            if a.sha8 not in exclude and a.resolved_path not in exclude
        ]
        base_diagnostics = {
            "codex_account_source": "weighted",
            "codex_account_pool_size": len(accounts),
            "codex_account_excluded_count": len(excluded_accounts),
            "codex_account_eligible_count": len(eligible),
            "codex_account_exclude_key_count": len(exclude),
            "codex_account_quota_snapshot_present": quota_left_snapshot is not None,
            "codex_account_quota_snapshot_count": (
                len(quota_left_snapshot) if quota_left_snapshot is not None else 0
            ),
        }

        if not eligible:
            raise NoCandidateError(
                "No eligible account remaining",
                reason="no_eligible_after_exclude",
                diagnostics=base_diagnostics,
            )

        # -- compute raw weights ---------------------------------------------
        raw_weights = self._compute_raw_weights(eligible, quota_left_snapshot)

        # -- normalize & sample ----------------------------------------------
        total = sum(raw_weights)
        if total <= 0:
            zero_weight_count = sum(1 for weight in raw_weights if weight <= 0)
            raise NoCandidateError(
                "Total effective weight is zero",
                reason="zero_effective_weight",
                diagnostics={
                    **base_diagnostics,
                    "codex_account_zero_effective_weight_count": zero_weight_count,
                },
            )

        # Unbiased draw using cryptographic randomness.
        point = _uniform_float() * total
        cumulative = 0.0
        chosen_idx, chosen = eligible[-1]
        for (idx, a), rw in zip(eligible, raw_weights):
            cumulative += rw
            if point < cumulative:
                chosen_idx, chosen = idx, a
                break

        return AccountCandidate(
            auth_ref=chosen.resolved_path,
            source_ref=_safe_source_ref(chosen.raw_path),
            source_index=chosen_idx,
            weight=chosen.weight,
        )

    # -- internal ------------------------------------------------------------

    def _compute_raw_weights(
        self,
        eligible: list[tuple[int, "_ResolvedAccount"]],
        quota_left_snapshot: dict[str, float] | None,
    ) -> list[float]:
        """Return raw (pre-normalization) weights for each eligible account.

        Static when *quota_left_snapshot* is ``None``; otherwise dynamic with
        a full-snapshot-completeness check (any missing/non-comparable entry
        causes a whole-draw fallback to static).
        """
        configured = [float(a.weight) for _, a in eligible]

        if quota_left_snapshot is None:
            return configured

        # Dynamic mode: validate completeness.
        dynamic_raw: list[float] = []
        for (_, a), base in zip(eligible, configured):
            frac = quota_left_snapshot.get(a.sha8)
            if frac is None or not _is_comparable_fraction(frac):
                # Incomplete snapshot → whole draw falls back to static.
                return configured
            dynamic_raw.append(base * frac)

        return dynamic_raw


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _safe_source_ref(raw_ref: str) -> str:
    """Return a non-secret source_ref: verbatim if relative, else redacted."""
    expanded = os.path.expanduser(raw_ref) if raw_ref.startswith("~") else raw_ref
    if os.path.isabs(expanded):
        return "<absolute-path-redacted>"
    return raw_ref


def _is_comparable_fraction(value: object) -> bool:
    """True when *value* is a finite float in [0, 1]."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        f = float(value)
        return 0.0 <= f <= 1.0 and not (f != f)  # NaN check
    return False


def _uniform_float() -> float:
    """Return a uniform random float in [0, 1) — one call site so we can
    mock the draw in tests without touching every ``random``/``secrets`` path.
    """
    # secrets.randbits gives us 53 bits (enough for a float64 mantissa).
    return secrets.randbits(53) / (1 << 53)
