"""Single-account source for the native Codex adapter.

The ``codex`` provider binds exactly one OAuth token file: an explicit
``codex_auth_path`` or the default ``<tui_dir>/codex-auth.json``. Account
pooling is not a kernel concern; it is provided by the external subs-pool
proxy, reached as an ordinary OpenAI-compatible Responses endpoint (see the
``subs-pool`` intrinsic skill). The source owns only candidate selection; the
adapter owns OAuth token refresh, REST/WS transport, safe attribution, and
failure classification; the kernel owns AED rebuild/replay.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AccountCandidate:
    """The account identity ready for Codex to bind and use.

    * ``auth_ref`` — resolved token-file path (Codex reads/refreshes the token).
    * ``auth_path_sha8`` — first 8 hex chars of SHA-256 of ``auth_ref``, the
      stable non-secret identity used for exclusion and attribution.
    """

    auth_ref: str
    auth_path_sha8: str = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "auth_path_sha8",
            hashlib.sha256(self.auth_ref.encode("utf-8")).hexdigest()[:8],
        )


class NoCandidateError(Exception):
    """The configured Codex account is unusable for this turn.

    Raised when the one account is excluded (after ``usage_limit_reached``).
    The kernel treats it as terminal for the turn: no AED rebuild/replay.
    """

    def diagnostic_fields(self) -> dict[str, int | bool]:
        return {}


class FixedAccountSource:
    """Always returns the SAME single account on every ``select``.

    ``exclude`` containing the account's identity raises ``NoCandidateError``.
    """

    def __init__(self, auth_path: str) -> None:
        self._candidate = AccountCandidate(auth_ref=auth_path)

    def select(self, exclude: set[str] | None = None) -> AccountCandidate:
        if exclude and self._candidate.auth_path_sha8 in exclude:
            raise NoCandidateError("Codex account is excluded")
        return self._candidate
