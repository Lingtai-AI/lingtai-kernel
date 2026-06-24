"""Tests for lingtai.auth.codex.CodexTokenManager."""

from __future__ import annotations

import base64
import json
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from lingtai.auth.codex import REFRESH_BUFFER_SECONDS, CodexAuthError, CodexTokenManager


def _make_id_token(payload: dict) -> str:
    """Build a syntactically valid (unsigned) JWT carrying ``payload``.

    Only the payload segment matters here — ``get_account_id`` reads it without
    verifying the signature, so the header/signature are throwaway fillers.
    """
    def _seg(obj) -> str:
        raw = json.dumps(obj).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return f"{_seg({'alg': 'RS256', 'typ': 'JWT'})}.{_seg(payload)}.sig-not-verified"


def _write_token_file(path, *, access_token="tok_valid", refresh_token="rt_abc",
                      expires_at=None, email="user@example.com"):
    """Helper to write a well-formed token file."""
    if expires_at is None:
        expires_at = int(time.time()) + 3600  # 1 hour from now
    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "email": email,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


# ------------------------------------------------------------------
# get_access_token
# ------------------------------------------------------------------

class TestGetAccessToken:
    def test_valid_token(self, tmp_path):
        """Token with future expiry is returned as-is."""
        token_file = tmp_path / "codex-auth.json"
        _write_token_file(token_file, access_token="tok_fresh")

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_access_token() == "tok_fresh"

    def test_expands_user_in_explicit_token_path(self, tmp_path, monkeypatch):
        """A user-relative explicit token_path is expanded before reading."""
        home = tmp_path / "home"
        home.mkdir()
        token_file = home / "codex-auth.json"
        _write_token_file(token_file, access_token="tok_home")
        monkeypatch.setenv("HOME", str(home))

        mgr = CodexTokenManager(token_path="~/codex-auth.json")
        assert mgr.get_access_token() == "tok_home"
        assert mgr.is_authenticated() is True

    def test_missing_file_raises(self, tmp_path):
        """FileNotFoundError is raised when the token file does not exist."""
        token_file = tmp_path / "nonexistent" / "codex-auth.json"
        mgr = CodexTokenManager(token_path=str(token_file))

        with pytest.raises(FileNotFoundError):
            mgr.get_access_token()


# ------------------------------------------------------------------
# is_authenticated
# ------------------------------------------------------------------

class TestIsAuthenticated:
    def test_true_when_file_has_refresh_token(self, tmp_path):
        token_file = tmp_path / "codex-auth.json"
        _write_token_file(token_file)

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.is_authenticated() is True

    def test_false_when_no_file(self, tmp_path):
        token_file = tmp_path / "does-not-exist.json"
        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.is_authenticated() is False


# ------------------------------------------------------------------
# Refresh behaviour
# ------------------------------------------------------------------

class TestRefresh:
    @patch("lingtai.auth.codex.httpx.post")
    def test_refresh_on_expired_token(self, mock_post, tmp_path):
        """An expired token triggers a refresh; new tokens are written."""
        token_file = tmp_path / "codex-auth.json"
        _write_token_file(
            token_file,
            access_token="tok_old",
            expires_at=int(time.time()) - 60,  # already expired
        )

        new_expires_at = int(time.time()) + 7200
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "tok_new",
            "refresh_token": "rt_new",
            "expires_at": new_expires_at,
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        mgr = CodexTokenManager(token_path=str(token_file))
        token = mgr.get_access_token()

        assert token == "tok_new"
        mock_post.assert_called_once()

        # Verify file was updated
        written = json.loads(token_file.read_text(encoding="utf-8"))
        assert written["access_token"] == "tok_new"
        assert written["refresh_token"] == "rt_new"
        assert written["expires_at"] == new_expires_at
        assert written["email"] == "user@example.com"  # preserved

    @patch("lingtai.auth.codex.httpx.post")
    def test_refresh_near_expiry(self, mock_post, tmp_path):
        """Token expiring within the buffer window triggers a refresh."""
        token_file = tmp_path / "codex-auth.json"
        # Expires in 2 minutes — inside the 5-minute buffer
        near_expiry = int(time.time()) + 120
        _write_token_file(
            token_file,
            access_token="tok_soon_expired",
            expires_at=near_expiry,
        )

        new_expires_at = int(time.time()) + 7200
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "tok_refreshed",
            "refresh_token": "rt_refreshed",
            "expires_at": new_expires_at,
        }
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        mgr = CodexTokenManager(token_path=str(token_file))
        token = mgr.get_access_token()

        assert token == "tok_refreshed"
        mock_post.assert_called_once()


# ------------------------------------------------------------------
# Refresh errors
# ------------------------------------------------------------------

class TestRefreshErrors:
    @patch("lingtai.auth.codex.httpx.post")
    def test_401_raises_codex_auth_error(self, mock_post, tmp_path):
        """A 401 from the refresh endpoint raises CodexAuthError."""
        token_file = tmp_path / "codex-auth.json"
        _write_token_file(
            token_file,
            access_token="tok_old",
            expires_at=int(time.time()) - 60,
        )

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Unauthorized", request=MagicMock(), response=mock_response,
        )
        mock_post.return_value = mock_response

        mgr = CodexTokenManager(token_path=str(token_file))
        with pytest.raises(CodexAuthError, match="expired"):
            mgr.get_access_token()

    @patch("lingtai.auth.codex.httpx.post")
    def test_500_propagates_raw_error(self, mock_post, tmp_path):
        """A 500 from the refresh endpoint propagates as HTTPStatusError."""
        token_file = tmp_path / "codex-auth.json"
        _write_token_file(
            token_file,
            access_token="tok_old",
            expires_at=int(time.time()) - 60,
        )

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_response,
        )
        mock_post.return_value = mock_response

        mgr = CodexTokenManager(token_path=str(token_file))
        with pytest.raises(httpx.HTTPStatusError):
            mgr.get_access_token()


# ------------------------------------------------------------------
# get_account_id  (ChatGPT-Account-ID source resolution)
# ------------------------------------------------------------------

# Placeholder, non-secret account-id values used only in tests. These are not
# real account identifiers — just opaque strings to assert pass-through.
_ACCT_EXPLICIT = "acct-explicit-0000"
_ACCT_FROM_TOKEN = "acct-from-token-1111"


class TestGetAccountId:
    def _write(self, path, extra: dict):
        """Write a token file with arbitrary extra top-level fields."""
        data = {
            "access_token": "tok_valid",
            "refresh_token": "rt_abc",
            "expires_at": int(time.time()) + 3600,
            "email": "user@example.com",
        }
        data.update(extra)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def test_explicit_account_id_field(self, tmp_path):
        """An explicit ``account_id`` field is returned verbatim."""
        token_file = tmp_path / "codex-auth.json"
        self._write(token_file, {"account_id": _ACCT_EXPLICIT})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() == _ACCT_EXPLICIT

    def test_explicit_chatgpt_account_id_field(self, tmp_path):
        """The alternate ``chatgpt_account_id`` field is also honored."""
        token_file = tmp_path / "codex-auth.json"
        self._write(token_file, {"chatgpt_account_id": _ACCT_EXPLICIT})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() == _ACCT_EXPLICIT

    def test_extracted_from_id_token_namespaced_claim(self, tmp_path):
        """Falls back to the namespaced claim decoded from the id_token JWT."""
        token_file = tmp_path / "codex-auth.json"
        id_token = _make_id_token(
            {"https://api.openai.com/auth": {"chatgpt_account_id": _ACCT_FROM_TOKEN}}
        )
        self._write(token_file, {"id_token": id_token})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() == _ACCT_FROM_TOKEN

    def test_explicit_field_wins_over_id_token(self, tmp_path):
        """An explicit field takes priority over the id_token claim."""
        token_file = tmp_path / "codex-auth.json"
        id_token = _make_id_token(
            {"https://api.openai.com/auth": {"chatgpt_account_id": _ACCT_FROM_TOKEN}}
        )
        self._write(
            token_file, {"account_id": _ACCT_EXPLICIT, "id_token": id_token}
        )

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() == _ACCT_EXPLICIT

    def test_none_when_no_account_anywhere(self, tmp_path):
        """No explicit field and no id_token claim → None (header omitted)."""
        token_file = tmp_path / "codex-auth.json"
        self._write(token_file, {})  # only the base fields, no account id

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() is None

    def test_none_when_id_token_lacks_claim(self, tmp_path):
        """An id_token without the namespaced auth claim yields None."""
        token_file = tmp_path / "codex-auth.json"
        id_token = _make_id_token({"sub": "user-123", "email": "u@example.com"})
        self._write(token_file, {"id_token": id_token})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() is None

    def test_none_when_id_token_malformed(self, tmp_path):
        """A malformed (non-JWT) id_token is tolerated and yields None."""
        token_file = tmp_path / "codex-auth.json"
        self._write(token_file, {"id_token": "not-a-jwt"})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() is None

    def test_none_when_id_token_payload_not_base64(self, tmp_path):
        """A JWT-shaped token with an undecodable payload yields None."""
        token_file = tmp_path / "codex-auth.json"
        # Three segments, but the middle is not valid base64url JSON.
        self._write(token_file, {"id_token": "aaa.!!!not-base64!!!.ccc"})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() is None

    def test_none_when_file_missing(self, tmp_path):
        """A missing token file yields None rather than raising."""
        token_file = tmp_path / "nonexistent" / "codex-auth.json"
        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() is None

    def test_none_when_file_malformed_json(self, tmp_path):
        """A token file that is not valid JSON yields None rather than raising."""
        token_file = tmp_path / "codex-auth.json"
        token_file.write_text("{not valid json", encoding="utf-8")

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() is None

    def test_ignores_non_string_account_field(self, tmp_path):
        """A non-string explicit field is ignored; falls through to id_token."""
        token_file = tmp_path / "codex-auth.json"
        id_token = _make_id_token(
            {"https://api.openai.com/auth": {"chatgpt_account_id": _ACCT_FROM_TOKEN}}
        )
        self._write(token_file, {"account_id": 12345, "id_token": id_token})

        mgr = CodexTokenManager(token_path=str(token_file))
        assert mgr.get_account_id() == _ACCT_FROM_TOKEN
