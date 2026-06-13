"""Cloud Mail REST client.

Thin synchronous ``httpx`` wrapper around a self-hosted Cloud Mail
deployment (https://github.com/maillab/cloud-mail). Handles the
``{code, message, data}`` envelope, the two distinct auth schemes
(public-token header vs. user JWT header), and token caching.

Design notes / API facts (from cloud-mail source):
  * Envelope: ``{ "code": 200, "message": "success", "data": ... }``.
    Any non-200 ``code`` or transport error is raised as ``CloudMailError``.
  * ``POST /public/genToken`` body ``{email, password}`` (admin only,
    auth-excluded) -> ``data.token`` (a uuid public token).
  * Other ``/public/*`` routes require header ``Authorization: <public token>``
    (NOT ``Bearer``).
  * ``POST /login`` body ``{email, password}`` -> a user JWT (returned either
    as a bare string in ``data`` or as ``data.token``). Authenticated routes
    use header ``Authorization: <jwt>``.

Secrets discipline: this module never logs tokens, passwords, or full
``Authorization`` headers. ``CloudMailError`` messages carry HTTP status /
envelope code / server message only.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("lingtai_cloud_mail")

DEFAULT_TIMEOUT = 30.0


class CloudMailError(Exception):
    """Raised for transport errors or non-200 envelope codes.

    Never carries secrets — only HTTP status, envelope code, and the
    server-provided message.
    """

    def __init__(self, message: str, *, code: int | None = None, http_status: int | None = None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class CloudMailClient:
    """Synchronous REST client for one Cloud Mail base URL.

    Public-API calls auto-acquire a public token via ``genToken`` using the
    configured admin credentials; user-API calls auto-acquire a JWT via
    ``/login`` using the configured user credentials. Tokens are cached and
    re-fetched once on a 401-ish failure.
    """

    def __init__(
        self,
        base_url: str,
        *,
        admin_email: str | None = None,
        admin_password: str | None = None,
        user_email: str | None = None,
        user_password: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._admin_email = admin_email
        self._admin_password = admin_password
        self._user_email = user_email
        self._user_password = user_password
        # `transport` lets tests inject httpx.MockTransport without network.
        self._http = httpx.Client(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
        )
        self._public_token: str | None = None
        self._user_jwt: str | None = None

    # -- lifecycle --

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:
            pass

    def __enter__(self) -> "CloudMailClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- envelope handling --

    @staticmethod
    def _parse_envelope(resp: httpx.Response) -> Any:
        """Validate HTTP + envelope, return ``data`` or raise CloudMailError."""
        if resp.status_code >= 400:
            # Try to surface the server message without leaking request data.
            server_msg = ""
            try:
                body = resp.json()
                if isinstance(body, dict):
                    server_msg = str(body.get("message") or "")
            except Exception:
                pass
            raise CloudMailError(
                f"HTTP {resp.status_code} from Cloud Mail"
                + (f": {server_msg}" if server_msg else ""),
                http_status=resp.status_code,
            )
        try:
            body = resp.json()
        except Exception as exc:
            raise CloudMailError(
                f"Cloud Mail returned non-JSON response (HTTP {resp.status_code})"
            ) from exc
        if not isinstance(body, dict):
            raise CloudMailError("Cloud Mail envelope was not a JSON object")
        code = body.get("code")
        if code != 200:
            raise CloudMailError(
                f"Cloud Mail error (code {code}): {body.get('message') or 'unknown'}",
                code=code,
                http_status=resp.status_code,
            )
        return body.get("data")

    # -- token acquisition --

    def _ensure_public_token(self, *, force: bool = False) -> str:
        if self._public_token and not force:
            return self._public_token
        if not self._admin_email or not self._admin_password:
            raise CloudMailError(
                "public API requires admin_email/admin_password in the account "
                "config to mint a public token via /public/genToken"
            )
        resp = self._http.post(
            "/public/genToken",
            json={"email": self._admin_email, "password": self._admin_password},
        )
        data = self._parse_envelope(resp)
        token = None
        if isinstance(data, dict):
            token = data.get("token")
        elif isinstance(data, str):
            token = data
        if not token:
            raise CloudMailError("genToken response did not include a token")
        self._public_token = str(token)
        return self._public_token

    def _ensure_user_jwt(self, *, force: bool = False) -> str:
        if self._user_jwt and not force:
            return self._user_jwt
        if not self._user_email or not self._user_password:
            raise CloudMailError(
                "user API requires user_email/user_password in the account "
                "config to log in via /login"
            )
        resp = self._http.post(
            "/login",
            json={"email": self._user_email, "password": self._user_password},
        )
        data = self._parse_envelope(resp)
        jwt = None
        if isinstance(data, str):
            jwt = data
        elif isinstance(data, dict):
            jwt = data.get("token") or data.get("jwt")
        if not jwt:
            raise CloudMailError("/login response did not include a token")
        self._user_jwt = str(jwt)
        return self._user_jwt

    # -- low-level authed request helpers (with one auto-retry on 401) --

    def _public_request(self, method: str, path: str, **kwargs) -> Any:
        token = self._ensure_public_token()
        headers = {"Authorization": token}
        resp = self._http.request(method, path, headers=headers, **kwargs)
        if resp.status_code in (401, 403):
            token = self._ensure_public_token(force=True)
            headers = {"Authorization": token}
            resp = self._http.request(method, path, headers=headers, **kwargs)
        return self._parse_envelope(resp)

    def _user_request(self, method: str, path: str, **kwargs) -> Any:
        jwt = self._ensure_user_jwt()
        headers = {"Authorization": jwt}
        resp = self._http.request(method, path, headers=headers, **kwargs)
        if resp.status_code in (401, 403):
            jwt = self._ensure_user_jwt(force=True)
            headers = {"Authorization": jwt}
            resp = self._http.request(method, path, headers=headers, **kwargs)
        return self._parse_envelope(resp)

    # -- public API --

    def email_list(self, **filters: Any) -> list[dict]:
        """POST /public/emailList. Returns the row list (possibly empty).

        Accepts filters: toEmail, content, subject, sendName, sendEmail,
        timeSort ('asc' | desc-default), num (page, default 1), size
        (default 20), type, isDel. Unknown/None filters are dropped.
        """
        body = {k: v for k, v in filters.items() if v is not None}
        data = self._public_request("POST", "/public/emailList", json=body)
        rows = _coerce_rows(data)
        return rows

    def add_user(self, email: str, password: str, **extra: Any) -> Any:
        """POST /public/addUser. Optional convenience; never logs the password.

        Cloud Mail's public ``addUser`` endpoint expects a batch-shaped body:
        ``{"list": [{"email": ..., "password": ..., ...}]}``, even for a
        single user. Keep the public MCP surface simple while matching that
        upstream contract exactly.
        """
        row = {"email": email, "password": password}
        row.update({k: v for k, v in extra.items() if v is not None})
        return self._public_request("POST", "/public/addUser", json={"list": [row]})

    # -- user API --

    def email_send(self, payload: dict) -> Any:
        """POST /email/send with the user JWT. Caller assembles the payload."""
        return self._user_request("POST", "/email/send", json=payload)

    def email_list_user(self, **query: Any) -> Any:
        """GET /email/list with the user JWT (account-scoped listing)."""
        params = {k: v for k, v in query.items() if v is not None}
        return self._user_request("GET", "/email/list", params=params)

    def mark_read(self, email_ids: list[int]) -> Any:
        """PUT /email/read body {emailIds} with the user JWT."""
        return self._user_request("PUT", "/email/read", json={"emailIds": email_ids})


def _coerce_rows(data: Any) -> list[dict]:
    """Normalize an emailList ``data`` payload into a list of row dicts.

    The public emailList returns a bare list, but be defensive about a
    ``{list: [...]}`` wrapper too (the user-API list shape).
    """
    if data is None:
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        inner = data.get("list")
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
    return []
