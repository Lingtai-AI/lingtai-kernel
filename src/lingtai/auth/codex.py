"""Codex OAuth token manager.

Reads tokens written by the TUI (``~/.lingtai-tui/codex-auth.json``),
checks expiry, and auto-refreshes via the OpenAI OAuth endpoint.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from pathlib import Path

import httpx
from filelock import FileLock

TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_BUFFER_SECONDS = 300  # refresh if within 5 minutes of expiry

# Namespaced OAuth claim that carries the user's ChatGPT account id in the
# OpenAI-issued id_token. The official OpenAI OAuth payload nests it under this
# OIDC-style namespace key, e.g.
#   {"https://api.openai.com/auth": {"chatgpt_account_id": "<uuid>", ...}, ...}
_OAUTH_AUTH_CLAIM = "https://api.openai.com/auth"
_ACCOUNT_ID_CLAIM = "chatgpt_account_id"


def _decode_jwt_payload(token: str) -> dict:
    """Decode a JWT's payload segment locally (NO signature verification).

    This only base64url-decodes the middle segment to read non-secret metadata
    claims the issuer put there. We never verify the signature — we are not
    authenticating the token, just reading our own account metadata out of it.
    Returns ``{}`` for anything that is not a well-formed JWT-with-JSON-payload
    rather than raising, so callers can treat "no account id" uniformly.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        # base64url, padding stripped by the JWT spec — restore it before decode.
        padding = "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(payload_b64 + padding)
        decoded = json.loads(raw)
        return decoded if isinstance(decoded, dict) else {}
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return {}


class CodexAuthError(Exception):
    """Raised when Codex OAuth tokens cannot be refreshed.

    The message is user-facing and points to /login for recovery.
    """
    pass


class CodexTokenManager:
    """Manages Codex OAuth tokens stored on disk by the TUI."""

    def __init__(self, token_path: str | None = None) -> None:
        if token_path is None:
            tui_dir = os.environ.get("LINGTAI_TUI_DIR", "~/.lingtai-tui")
            token_path = str(Path(tui_dir).expanduser() / "codex-auth.json")
        self._path = Path(token_path).expanduser()
        self._lock_path = self._path.with_suffix(".json.lock")
        self._cache: dict | None = None
        self._cache_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_authenticated(self) -> bool:
        """Return *True* if the token file exists and contains a refresh token."""
        try:
            data = self._read()
            return bool(data.get("refresh_token"))
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return False

    def get_access_token(self) -> str:
        """Return a valid access token, refreshing automatically if needed.

        Raises ``FileNotFoundError`` when the token file does not exist.
        """
        data = self._read()

        expires_at = data.get("expires_at", 0)
        if time.time() + REFRESH_BUFFER_SECONDS >= expires_at:
            self._refresh(data)
            data = self._read()

        return data["access_token"]

    def get_account_id(self) -> str | None:
        """Return the user's own ChatGPT account id, or ``None`` if unavailable.

        Source priority (the user's OWN auth data only — never invented):
          1. An explicit ``account_id`` / ``chatgpt_account_id`` field written
             into ``codex-auth.json`` by the TUI, if present.
          2. The namespaced claim decoded locally from the ``id_token`` JWT:
             ``payload["https://api.openai.com/auth"]["chatgpt_account_id"]``
             (no signature verification — local metadata extraction only).

        Always non-raising: a missing file, malformed JSON, or absent claim all
        yield ``None`` so callers can simply omit the header. The returned id is
        a non-secret account identifier; the access/refresh tokens are never
        read or returned here.
        """
        try:
            data = self._read()
        except (FileNotFoundError, json.JSONDecodeError):
            return None

        return self._extract_account_id(data)

    @staticmethod
    def _extract_account_id(data: dict) -> str | None:
        """Pull the ChatGPT account id out of already-loaded auth data."""
        # 1. Explicit field wins — the TUI may persist it directly.
        for key in ("account_id", "chatgpt_account_id"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val

        # 2. Fall back to decoding the id_token JWT's namespaced auth claim.
        id_token = data.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            return None
        payload = _decode_jwt_payload(id_token)
        auth_claim = payload.get(_OAUTH_AUTH_CLAIM)
        if isinstance(auth_claim, dict):
            val = auth_claim.get(_ACCOUNT_ID_CLAIM)
            if isinstance(val, str) and val:
                return val
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> dict:
        """Read the token file, using an mtime-based cache to avoid re-parsing."""
        try:
            mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Codex token file not found: {self._path}. "
                "Please authenticate via the TUI first."
            )

        if self._cache is not None and mtime == self._cache_mtime:
            return self._cache

        with open(self._path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self._cache = data
        self._cache_mtime = mtime
        return data

    def _refresh(self, data: dict) -> None:
        """Refresh the access token using the stored refresh token.

        Uses a file lock so concurrent processes don't race.  After
        acquiring the lock the file is re-read — another process may have
        already completed the refresh.
        """
        lock = FileLock(self._lock_path, timeout=30)
        with lock:
            # Re-read inside the lock; someone else may have refreshed.
            fresh = self._read()
            if fresh.get("expires_at", 0) > time.time() + REFRESH_BUFFER_SECONDS:
                return  # already refreshed by another process

            refresh_token = fresh.get("refresh_token") or data.get("refresh_token")
            if not refresh_token:
                raise RuntimeError("No refresh_token available in token file.")

            response = httpx.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": CLIENT_ID,
                },
                timeout=30,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    raise CodexAuthError(
                        "Codex session expired. Run /login in the TUI to re-authenticate."
                    ) from e
                raise
            result = response.json()

            # Merge new tokens into existing data, preserving email etc.
            fresh["access_token"] = result["access_token"]
            if "refresh_token" in result:
                fresh["refresh_token"] = result["refresh_token"]
            fresh["expires_at"] = result.get(
                "expires_at", int(time.time()) + result.get("expires_in", 3600)
            )

            tmp_path = self._path.with_suffix(".json.tmp")
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(fresh, f, indent=2)
            tmp_path.replace(self._path)

            # Invalidate cache so next _read() picks up the new file.
            self._cache = None
            self._cache_mtime = 0.0
