"""Small, Outlook.com consumer-account authentication boundary.

The runtime never performs interactive authorization.  ``bootstrap`` is the
separate human-operated device-flow entry point; the account provider only uses
the serialized MSAL cache and silent acquisition.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Protocol

AUTH_TYPE = "microsoft_oauth2"
AUTHORITY = "https://login.microsoftonline.com/consumers"
IMAP_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All"
SMTP_SCOPE = "https://outlook.office.com/SMTP.Send"


class OAuthError(RuntimeError):
    """An intentionally secret-free authentication/configuration failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AuthProvider(Protocol):
    mode: str

    def authenticate_imap(self, client: Any, *, account: str) -> None: ...
    def authenticate_smtp(self, server: Any, *, account: str) -> None: ...
    def status(self) -> dict[str, Any]: ...


class PasswordAuthProvider:
    mode = "password"

    def __init__(self, password: str) -> None:
        self._password = password

    def authenticate_imap(self, client: Any, *, account: str) -> None:
        client.login(account, self._password)

    def authenticate_smtp(self, server: Any, *, account: str) -> None:
        server.login(account, self._password)

    def status(self) -> dict[str, Any]:
        return {"auth_type": self.mode, "auth_state": "configured"}


class TokenCacheStore:
    """Owner-only, same-directory atomic storage for serialized MSAL state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()

    def _parent(self) -> Path:
        parent = self.path.parent
        if parent.is_symlink():
            raise OAuthError("oauth_cache_permissions")
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        mode = stat.S_IMODE(parent.stat().st_mode)
        if mode & 0o077 or not parent.is_dir():
            raise OAuthError("oauth_cache_permissions")
        return parent

    def _safe_file(self) -> None:
        if self.path.is_symlink():
            raise OAuthError("oauth_cache_permissions")
        if self.path.exists():
            mode = stat.S_IMODE(self.path.stat().st_mode)
            if not self.path.is_file() or mode & 0o077:
                raise OAuthError("oauth_cache_permissions")

    def load(self) -> str | None:
        with self._lock:
            self._parent()
            self._safe_file()
            if not self.path.exists():
                return None
            try:
                return self.path.read_bytes().decode("utf-8")
            except (OSError, UnicodeError) as exc:
                raise OAuthError("oauth_cache_unreadable") from exc

    def save(self, state: str) -> None:
        if not isinstance(state, str):
            raise OAuthError("oauth_cache_unreadable")
        with self._lock:
            parent = self._parent()
            self._safe_file()
            temp_name: str | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb", dir=parent,
                    prefix=f".{self.path.name}.", delete=False,
                ) as tmp:
                    temp_name = tmp.name
                    os.chmod(tmp.name, 0o600)
                    tmp.write(state.encode("utf-8"))
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(temp_name, self.path)
                temp_name = None
                try:
                    dir_fd = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                except OSError:
                    pass
            except OSError as exc:
                raise OAuthError("oauth_cache_write_failed") from exc
            finally:
                if temp_name:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass

    def exists(self) -> bool:
        with self._lock:
            self._parent()
            self._safe_file()
            return self.path.exists()


def _msal(module: Any | None) -> Any:
    try:
        return module or importlib.import_module("msal")
    except ImportError as exc:
        raise OAuthError("oauth_dependency_missing") from exc


class OAuth2AuthProvider:
    mode = AUTH_TYPE

    def __init__(
        self,
        email: str,
        client_id: str,
        token_cache: str | Path,
        *,
        smtp_enabled: bool = False,
        msal_module: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.email = email
        self.client_id = client_id
        self.smtp_enabled = smtp_enabled
        self.store = token_cache if isinstance(token_cache, TokenCacheStore) else TokenCacheStore(token_cache)
        self._msal_module = msal_module
        self._client_factory = client_factory
        self._client: Any | None = None
        self._cache: Any | None = None
        self._auth_state = "configured"
        self._lock = threading.RLock()

    def _client_for_cache(self) -> Any:
        if self._client is not None:
            return self._client
        module = _msal(self._msal_module)
        cache = module.SerializableTokenCache()
        state = self.store.load()
        if state:
            try:
                cache.deserialize(state)
            except Exception as exc:
                raise OAuthError("oauth_cache_unreadable") from exc
        factory = self._client_factory or module.PublicClientApplication
        self._cache = cache
        self._client = factory(
            client_id=self.client_id, authority=AUTHORITY, token_cache=cache,
        )
        return self._client

    def _acquire(self, *, smtp: bool) -> str:
        scopes = [IMAP_SCOPE]
        if smtp:
            if not self.smtp_enabled:
                raise OAuthError("smtp_disabled")
            scopes.append(SMTP_SCOPE)
        with self._lock:
            client = self._client_for_cache()
            accounts = client.get_accounts(username=self.email)
            account = accounts[0] if accounts else None
            result = client.acquire_token_silent(scopes, account=account) if account else None
            if self._cache is not None and self._cache.has_state_changed:
                self.store.save(self._cache.serialize())
            token = result.get("access_token") if isinstance(result, dict) else None
            if not token:
                self._auth_state = "reauth_required"
                raise OAuthError("oauth_reauthorization_required")
            self._auth_state = "ready"
            return token

    def authenticate_imap(self, client: Any, *, account: str) -> None:
        token = self._acquire(smtp=False)
        try:
            client.oauth2_login(account, token, mech="XOAUTH2")
        except Exception as exc:
            self._auth_state = "failed"
            raise OAuthError("oauth_imap_auth_failed") from exc

    def authenticate_smtp(self, server: Any, *, account: str) -> None:
        token = self._acquire(smtp=True)
        payload = f"user={account}\x01auth=Bearer {token}\x01\x01"
        try:
            # STARTTLS resets SMTP capabilities; EHLO again before AUTH.
            server.ehlo_or_helo_if_needed()
            server.auth("XOAUTH2", lambda challenge=None: payload, initial_response_ok=True)
        except Exception as exc:
            self._auth_state = "failed"
            raise OAuthError("oauth_smtp_auth_failed") from exc

    def status(self) -> dict[str, Any]:
        try:
            present = self.store.exists()
        except OAuthError:
            present = False
        state = self._auth_state if self._auth_state != "configured" else ("configured" if present else "bootstrap_required")
        return {
            "auth_type": AUTH_TYPE,
            "smtp_enabled": self.smtp_enabled,
            "auth_state": state,
        }


def normalize_account(account: dict[str, Any], index: int = 0) -> dict[str, Any]:
    if not isinstance(account, dict) or not isinstance(account.get("email_address"), str) or not account["email_address"].strip():
        raise ValueError(f"config_invalid: accounts[{index}].email_address")
    account = dict(account)
    has_auth = "auth" in account
    auth = account.get("auth")
    if not has_auth:
        if "email_password" not in account:
            raise ValueError(f"config_invalid: accounts[{index}].email_password")
        if "client_secret" in account:
            raise ValueError(f"config_invalid: accounts[{index}].client_secret")
        return account
    if not isinstance(auth, dict) or auth.get("type") != AUTH_TYPE:
        raise ValueError(f"config_invalid: accounts[{index}].auth.type")
    if "email_password" in account or "client_secret" in account:
        raise ValueError(f"config_invalid: accounts[{index}].auth")
    unknown = set(auth) - {"type", "client_id", "token_cache", "smtp_enabled"}
    if unknown or not isinstance(auth.get("client_id"), str) or not auth["client_id"].strip() or not isinstance(auth.get("token_cache"), str) or not auth["token_cache"].strip():
        raise ValueError(f"config_invalid: accounts[{index}].auth")
    if "smtp_enabled" in auth and not isinstance(auth["smtp_enabled"], bool):
        raise ValueError(f"config_invalid: accounts[{index}].auth.smtp_enabled")
    if "smtp_enabled" in account and not isinstance(account["smtp_enabled"], bool):
        raise ValueError(f"config_invalid: accounts[{index}].smtp_enabled")
    if "smtp_enabled" in auth and "smtp_enabled" in account:
        raise ValueError(f"config_invalid: accounts[{index}].smtp_enabled")
    return account


def normalize_accounts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(cfg, dict):
        raise ValueError("config_invalid: root")
    if "accounts" in cfg and "email_address" in cfg:
        raise ValueError("config_invalid: accounts")
    if "accounts" in cfg:
        accounts = cfg["accounts"]
        if not isinstance(accounts, list) or not accounts:
            raise ValueError("config_invalid: accounts")
    elif "email_address" in cfg:
        legacy = {k: cfg[k] for k in cfg if k != "accounts"}
        legacy.setdefault("email_password", "")
        accounts = [legacy]
    else:
        raise ValueError("config_invalid: accounts")
    return [normalize_account(item, i) for i, item in enumerate(accounts)]


def provider_from_config(account: dict[str, Any], working_dir: Path | None = None) -> tuple[AuthProvider, bool]:
    auth = account.get("auth")
    if auth is None:
        return PasswordAuthProvider(account["email_password"]), account.get("smtp_enabled", True)
    cache_path = Path(auth["token_cache"]).expanduser()
    if not cache_path.is_absolute() and working_dir:
        cache_path = working_dir / cache_path
    enabled = auth.get("smtp_enabled", account.get("smtp_enabled", False))
    return OAuth2AuthProvider(account["email_address"], auth["client_id"], cache_path, smtp_enabled=enabled), enabled


def bootstrap_device_flow(email: str, client_id: str, token_cache: str | Path, *, smtp_enabled: bool = False, msal_module: Any | None = None, client_factory: Callable[..., Any] | None = None, output: Callable[[str], None] = print) -> None:
    module = _msal(msal_module)
    store = TokenCacheStore(token_cache)
    cache = module.SerializableTokenCache()
    state = store.load()
    if state:
        try:
            cache.deserialize(state)
        except Exception as exc:
            raise OAuthError("oauth_cache_unreadable") from exc
    factory = client_factory or module.PublicClientApplication
    client = factory(client_id=client_id, authority=AUTHORITY, token_cache=cache)
    # MSAL automatically adds its reserved offline_access/openid/profile scopes.
    scopes = [IMAP_SCOPE] + ([SMTP_SCOPE] if smtp_enabled else [])
    flow = client.initiate_device_flow(scopes=scopes)
    if not isinstance(flow, dict) or not flow.get("user_code") or not flow.get("message"):
        raise OAuthError("oauth_bootstrap_failed")
    output(flow["message"])
    result = client.acquire_token_by_device_flow(flow)
    if not isinstance(result, dict) or not result.get("access_token"):
        raise OAuthError("oauth_reauthorization_required")
    if cache.has_state_changed:
        store.save(cache.serialize())


def bootstrap_from_config(cfg: dict[str, Any], account: str | None = None, *, working_dir: Path | None = None, **kwargs: Any) -> None:
    accounts = normalize_accounts(cfg)
    if account is None and len(accounts) != 1:
        raise ValueError("config_invalid: selected OAuth account")
    selected = next((a for a in accounts if not account or a["email_address"] == account), None)
    if not selected or "auth" not in selected:
        raise ValueError("config_invalid: selected OAuth account")
    auth = selected["auth"]
    cache_path = Path(auth["token_cache"]).expanduser()
    if working_dir and not cache_path.is_absolute():
        cache_path = working_dir / cache_path
    bootstrap_device_flow(selected["email_address"], auth["client_id"], cache_path, smtp_enabled=auth.get("smtp_enabled", selected.get("smtp_enabled", False)), **kwargs)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Bootstrap a Microsoft personal-account IMAP cache")
    parser.add_argument("--config", default=os.environ.get("LINGTAI_IMAP_CONFIG"))
    parser.add_argument("--account")
    args = parser.parse_args(argv)
    if not args.config:
        raise SystemExit("LINGTAI_IMAP_CONFIG or --config is required")
    try:
        with open(args.config, encoding="utf-8") as fh:
            work_dir = Path(os.environ.get("LINGTAI_AGENT_DIR") or Path.cwd())
            bootstrap_from_config(json.load(fh), args.account, working_dir=work_dir)
    except (OSError, ValueError, OAuthError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
